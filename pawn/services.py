"""Everything that is more than a form or a template lives here.

The interest engine (compute_due) is the one function the rest of the system leans on:
redemption, renewal, alerts, the active loans report and the API all call it.
"""
import calendar
import hashlib
import io
import json
import secrets
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from cryptography.fernet import Fernet
from django.conf import settings
from django.core import signing
from django.core.files.base import ContentFile
from django.db import transaction as db_transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import (
    Appraisal, Auction, AuctionSale, AuditLog, Branch, CashLedger, ChatMessage, ChatSession, Currency, Customer,
    Item, ItemCategory, ItemImage, MobileLoad, PawnTicket, Receipt, Redemption, Remittance, Renewal, SMSLog,
    SupportTicket, Transaction, TransactionType, WrapSummary, IDType,
)

ZERO = Decimal("0.00")


class PawnError(Exception):
    """A business rule was broken. The message is safe to show to staff."""


def q(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def peso(value):
    return f"{Decimal(value):,.2f}"


# ------------------------------------------------------------------------------ encryption
_fernet = Fernet(settings.FIELD_KEY)


def encrypt_text(text):
    return _fernet.encrypt(text.encode()).decode()


def decrypt_text(token):
    return _fernet.decrypt(token.encode()).decode()


def encrypt_bytes(data):
    return _fernet.encrypt(data)


def decrypt_bytes(data):
    return _fernet.decrypt(data)


def id_hash(id_type_id, number):
    clean = "".join(ch for ch in str(number).upper() if ch.isalnum())
    return hashlib.sha256(f"{id_type_id}:{clean}".encode()).hexdigest()


def mask(number):
    return "*" * max(len(number) - 4, 0) + number[-4:]


# ----------------------------------------------------------------------------------- audit
def audit(user, action, table, record_id=None, old=None, new=None):
    AuditLog.objects.create(
        user=user if getattr(user, "pk", None) else None, action=action, table_name=table,
        record_id=record_id, old_value=old, new_value=new)


# ------------------------------------------------------------------------------ dates / interest
def add_months(d, n):
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def months_elapsed(start, end):
    """Months since the loan date, rounded up. A partial month counts as a full month. Minimum 1."""
    m = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day > start.day:
        m += 1
    return max(m, 1)


def compute_due(ticket, on=None):
    on = on or timezone.localdate()
    principal = ticket.loan_amount
    months = months_elapsed(ticket.loan_date, on)
    total_interest = q(principal * ticket.interest_rate * months)
    paid = ticket.renewals.aggregate(s=Sum("interest_paid"))["s"] or ZERO
    interest_due = max(total_interest - paid, ZERO)
    overdue = on > ticket.maturity_date
    penalty = q(principal * settings.PAWN_PENALTY_RATE) if overdue else ZERO
    one_month = q(principal * ticket.interest_rate)
    return {
        "as_of": on, "months": months, "principal": principal, "interest_total": total_interest,
        "interest_paid": paid, "interest_due": interest_due, "service_fee": ticket.service_fee,
        "penalty": penalty, "overdue": overdue,
        "days_to_maturity": (ticket.maturity_date - on).days,
        "days_to_expiry": (ticket.expiry_date - on).days,
        "redeem_total": q(principal + interest_due + ticket.service_fee + penalty),
        "renew_total": q(max(interest_due, one_month) + penalty),
    }


# --------------------------------------------------------------------------- qr and numbers
def qr_token_for(txn_no):
    return signing.dumps({"n": txn_no}, salt="op-qr")


def txn_no_from_token(token):
    try:
        return signing.loads(token.strip(), salt="op-qr")["n"]
    except signing.BadSignature:
        return None


def qr_png(data):
    import qrcode
    img = qrcode.make(data, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def next_txn_no(today=None):
    today = today or timezone.localdate()
    prefix = f"PPS-{today:%Y%m%d}-"
    n = Transaction.objects.filter(transaction_no__startswith=prefix).count() + 1
    while Transaction.objects.filter(transaction_no=f"{prefix}{n:04d}").exists():
        n += 1
    return f"{prefix}{n:04d}"


def new_txn(type_name, customer, employee, amount=ZERO, fee=ZERO, currency=None, original=None, rate=None, branch=None):
    ttype, _ = TransactionType.objects.get_or_create(type_name=type_name)
    currency = currency or Currency.objects.get(code="PHP")
    no = next_txn_no()
    return Transaction.objects.create(
        transaction_no=no, transaction_type=ttype, customer=customer, employee=employee,
        branch=branch or (employee.branch if employee else Branch.objects.first()),
        currency=currency, exchange_rate_used=rate or currency.rate(),
        original_amount=original if original is not None else amount, amount=amount, fee=fee,
        qr_token=qr_token_for(no))


def ledger(txn, employee, direction, category, amount, description="", branch=None):
    return CashLedger.objects.create(
        branch=branch or (txn.branch if txn else employee.branch), transaction=txn, direction=direction,
        category=category, description=description or (txn.transaction_no if txn else ""),
        amount=q(amount), recorded_by=employee)


def require_active(customer):
    if customer.status != "Active":
        raise PawnError(f"{customer.full_name} is {customer.status.lower()}. Verify the customer's ID first.")


# --------------------------------------------------------------------------- receipts (PDF)
def _qr_drawing(data, size=84):
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    widget = QrCodeWidget(data)
    x0, y0, x1, y1 = widget.getBounds()
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(widget)
    return d


def issue_receipt(txn, rtype, title, lines, footer=""):
    from reportlab.graphics import renderPDF
    from reportlab.lib.pagesizes import A5
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A5)
    w, h = A5
    c.setFillColorRGB(0.04, 0.04, 0.06)
    c.rect(0, h - 70, w, 70, fill=1, stroke=0)
    c.setFillColorRGB(1, 0.18, 0.59)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(28, h - 38, "ONLY PAWNS")
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica", 10)
    c.drawString(28, h - 54, f"{txn.branch.branch_name}  |  {txn.branch.contact_no}")
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(28, h - 100, title)
    c.setFont("Helvetica", 9)
    c.drawString(28, h - 114, f"Transaction No. {txn.transaction_no}")
    c.drawRightString(w - 28, h - 114, timezone.localtime(txn.created_at).strftime("%b %d, %Y %I:%M %p"))
    y = h - 142
    for label, value in lines:
        c.setFont("Helvetica", 9.5)
        c.setFillColorRGB(0.4, 0.4, 0.4)
        c.drawString(28, y, str(label))
        c.setFillColorRGB(0.05, 0.05, 0.05)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawRightString(w - 28, y, str(value)[:60])
        y -= 16
    y -= 6
    renderPDF.draw(_qr_drawing(txn.qr_token), c, 28, max(y - 84, 60))
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(130, max(y - 84, 60) + 40, "Scan this code at the counter to pull up this transaction.")
    if txn.employee:
        c.drawString(130, max(y - 84, 60) + 28, f"Handled by {txn.employee.full_name}")
    if footer:
        c.drawString(28, 40, footer[:110])
    c.save()
    receipt_no = f"R-{txn.transaction_no[4:]}"
    receipt = Receipt(transaction=txn, receipt_no=receipt_no, receipt_type=rtype)
    receipt.file.save(f"{receipt_no}.pdf", ContentFile(buf.getvalue()), save=False)
    receipt.save()
    return receipt


# ------------------------------------------------------------------------------ appraisal
@db_transaction.atomic
def create_appraisal(employee, customer, category, item_type, description, condition, value, suggested, remarks, images):
    require_active(customer)
    value = q(value)
    suggested = q(suggested if suggested not in (None, "") else value * settings.PAWN_LOAN_TO_VALUE)
    if suggested > value:
        raise PawnError("The suggested loan cannot be higher than the appraised value.")
    item = Item.objects.create(customer=customer, category=category, item_type=item_type, description=description,
                               item_condition=condition, appraised_value=value)
    for f in images:
        ItemImage.objects.create(item=item, image=f)
    txn = new_txn(TransactionType.APPRAISE, customer, employee)
    appraisal = Appraisal.objects.create(transaction=txn, item=item, appraiser=employee, appraised_value=value,
                                         suggested_loan=suggested, remarks=remarks)
    audit(employee.user, "CREATE", "appraisals", appraisal.pk, None, {"item": item.pk, "value": str(value)})
    return appraisal


@db_transaction.atomic
def decline_appraisal(appraisal, employee):
    if appraisal.result != "Pending":
        raise PawnError("This appraisal was already answered.")
    appraisal.result = "Declined"
    appraisal.save(update_fields=["result"])
    audit(employee.user, "UPDATE", "appraisals", appraisal.pk, {"result": "Pending"}, {"result": "Declined"})


@db_transaction.atomic
def create_ticket(appraisal, loan_amount, employee, kind=TransactionType.SANGLA):
    if appraisal.result != "Pending":
        raise PawnError("This appraisal was already answered.")
    loan_amount = q(loan_amount)
    item, customer = appraisal.item, appraisal.item.customer
    require_active(customer)
    if loan_amount <= 0 or loan_amount > appraisal.suggested_loan:
        raise PawnError(f"The loan must be more than zero and no higher than PHP {peso(appraisal.suggested_loan)}.")
    if kind == TransactionType.LOAN and item.category.category_name.lower().startswith("jewel"):
        raise PawnError("Jewelry is pawned as Sangla. Use Loan for gadgets, appliances and other items.")
    cat, today = item.category, timezone.localdate()
    txn = new_txn(kind, customer, employee, amount=loan_amount)
    maturity = add_months(today, 1)
    ticket = PawnTicket.objects.create(
        ticket_no=f"PT-{txn.transaction_no[4:]}", transaction=txn, customer=customer, item=item,
        loan_amount=loan_amount, interest_rate=cat.monthly_interest_rate, service_fee=cat.service_fee,
        loan_date=today, maturity_date=maturity, expiry_date=maturity + timedelta(days=cat.grace_period_days))
    item.status = "In Vault"
    item.save(update_fields=["status"])
    appraisal.result = "Accepted"
    appraisal.save(update_fields=["result"])
    ledger(txn, employee, "OUT", "Loan Release", loan_amount, f"{kind} {ticket.ticket_no}")
    issue_receipt(txn, "Pawn Ticket", "PAWN TICKET", [
        ("Ticket No.", ticket.ticket_no), ("Customer", customer.full_name), ("Customer ID", customer.code),
        ("Item", f"{item.item_type} ({cat})"), ("Appraised value", f"PHP {peso(item.appraised_value)}"),
        ("Loan amount", f"PHP {peso(loan_amount)}"), ("Interest", f"{cat.monthly_interest_rate * 100:.2f}% per month"),
        ("Service fee", f"PHP {peso(cat.service_fee)}"), ("Loan date", f"{today:%b %d, %Y}"),
        ("Maturity date", f"{maturity:%b %d, %Y}"), ("Last day to redeem", f"{ticket.expiry_date:%b %d, %Y}"),
    ], "Bring this ticket and a valid ID when you redeem or renew. Unredeemed items go to auction after the last day.")
    audit(employee.user, "CREATE", "pawn_tickets", ticket.pk, None, {"ticket": ticket.ticket_no, "loan": str(loan_amount)})
    send_sms(customer, f"Only Pawns: ticket {ticket.ticket_no} for PHP {peso(loan_amount)} is active. "
                       f"Maturity {maturity:%b %d, %Y}.", "Receipt", ticket)
    return ticket


# ----------------------------------------------------------------------- redeem and renew
def _check_open(ticket):
    if not ticket.is_open:
        raise PawnError(f"Ticket {ticket.ticket_no} is {ticket.status.lower()} and cannot be processed here.")


@db_transaction.atomic
def redeem_ticket(ticket, tendered, employee):
    ticket = PawnTicket.objects.select_for_update().get(pk=ticket.pk)
    _check_open(ticket)
    due = compute_due(ticket)
    tendered = q(tendered)
    if tendered < due["redeem_total"]:
        raise PawnError(f"Payment is short. The total due today is PHP {peso(due['redeem_total'])}.")
    txn = new_txn(TransactionType.REDEMPTION, ticket.customer, employee, amount=due["redeem_total"])
    Redemption.objects.create(
        ticket=ticket, transaction=txn, principal_paid=due["principal"],
        interest_paid=due["interest_due"] + due["service_fee"], penalty_paid=due["penalty"], total_paid=due["redeem_total"])
    ticket.status = "Redeemed"
    ticket.save(update_fields=["status"])
    ticket.item.status = "Released"
    ticket.item.save(update_fields=["status"])
    ledger(txn, employee, "IN", "Redemption", due["redeem_total"], f"Redeemed {ticket.ticket_no}")
    issue_receipt(txn, "Redemption", "REDEMPTION RECEIPT", [
        ("Ticket No.", ticket.ticket_no), ("Customer", ticket.customer.full_name),
        ("Item released", ticket.item.item_type), ("Principal", f"PHP {peso(due['principal'])}"),
        ("Interest", f"PHP {peso(due['interest_due'])}"), ("Service fee", f"PHP {peso(due['service_fee'])}"),
        ("Penalty", f"PHP {peso(due['penalty'])}"), ("Total paid", f"PHP {peso(due['redeem_total'])}"),
        ("Cash received", f"PHP {peso(tendered)}"), ("Change", f"PHP {peso(tendered - due['redeem_total'])}"),
    ], "Item released to the customer. Thank you.")
    audit(employee.user, "UPDATE", "pawn_tickets", ticket.pk, {"status": "open"}, {"status": "Redeemed"})
    return txn


@db_transaction.atomic
def renew_ticket(ticket, tendered, employee):
    ticket = PawnTicket.objects.select_for_update().get(pk=ticket.pk)
    _check_open(ticket)
    due = compute_due(ticket)
    tendered = q(tendered)
    if tendered < due["renew_total"]:
        raise PawnError(f"Payment is short. Renewal today costs PHP {peso(due['renew_total'])}.")
    today = timezone.localdate()
    grace = (ticket.expiry_date - ticket.maturity_date).days
    old = ticket.maturity_date
    new = add_months(max(old, today), 1)
    txn = new_txn(TransactionType.RENEWAL, ticket.customer, employee, amount=due["renew_total"])
    Renewal.objects.create(ticket=ticket, transaction=txn, interest_paid=due["renew_total"] - due["penalty"],
                           old_maturity_date=old, new_maturity_date=new, renewal_no=ticket.renewals.count() + 1)
    ticket.maturity_date, ticket.expiry_date, ticket.status = new, new + timedelta(days=grace), "Renewed"
    ticket.save(update_fields=["maturity_date", "expiry_date", "status"])
    ledger(txn, employee, "IN", "Renewal", due["renew_total"], f"Renewed {ticket.ticket_no}")
    issue_receipt(txn, "Renewal", "RENEWAL RECEIPT", [
        ("Ticket No.", ticket.ticket_no), ("Customer", ticket.customer.full_name), ("Item", ticket.item.item_type),
        ("Principal (unchanged)", f"PHP {peso(ticket.loan_amount)}"), ("Interest paid", f"PHP {peso(due['renew_total'])}"),
        ("Old maturity", f"{old:%b %d, %Y}"), ("New maturity", f"{new:%b %d, %Y}"),
        ("Last day to redeem", f"{ticket.expiry_date:%b %d, %Y}"), ("Cash received", f"PHP {peso(tendered)}"),
        ("Change", f"PHP {peso(tendered - due['renew_total'])}"),
    ])
    audit(employee.user, "UPDATE", "pawn_tickets", ticket.pk, {"maturity": str(old)}, {"maturity": str(new)})
    return txn


# --------------------------------------------------------------------------- padala and load
@db_transaction.atomic
def create_padala(employee, customer, currency, amount, receiver_name, receiver_mobile, receiver_branch):
    require_active(customer)
    amount = q(amount)
    if amount <= 0:
        raise PawnError("Enter an amount greater than zero.")
    rate = currency.rate()
    php = q(amount * rate)
    fee = max(settings.PADALA_MIN_FEE, q(php * settings.PADALA_FEE_RATE))
    txn = new_txn(TransactionType.PADALA, customer, employee, amount=php, fee=fee, currency=currency, original=amount, rate=rate)
    ref = "PD" + "".join(secrets.choice("0123456789") for _ in range(10))
    while Remittance.objects.filter(reference_no=ref).exists():
        ref = "PD" + "".join(secrets.choice("0123456789") for _ in range(10))
    rem = Remittance.objects.create(transaction=txn, reference_no=ref, receiver_name=receiver_name,
                                    receiver_mobile=receiver_mobile, receiver_branch=receiver_branch)
    ledger(txn, employee, "IN", "Padala", php + fee, f"Padala {ref}")
    issue_receipt(txn, "Padala", "PADALA RECEIPT", [
        ("Reference No.", ref), ("Sender", customer.full_name), ("Receiver", receiver_name),
        ("Receiver mobile", receiver_mobile), ("Claim at", receiver_branch.branch_name),
        ("Amount sent", f"{currency.code} {peso(amount)}"), ("Rate used", f"{rate:.4f}"),
        ("Amount in PHP", f"PHP {peso(php)}"), ("Fee", f"PHP {peso(fee)}"), ("Total paid", f"PHP {peso(php + fee)}"),
    ], "The receiver needs the reference number and a valid ID to claim.")
    return rem


@db_transaction.atomic
def claim_padala(remittance, employee):
    remittance = Remittance.objects.select_for_update().get(pk=remittance.pk)
    if remittance.claim_status != "Unclaimed":
        raise PawnError(f"This remittance is already {remittance.claim_status.lower()}.")
    if not employee.user.is_admin_role and employee.branch_id != remittance.receiver_branch_id:
        raise PawnError(f"It can only be claimed at {remittance.receiver_branch.branch_name}.")
    remittance.claim_status, remittance.claimed_at = "Claimed", timezone.now()
    remittance.save(update_fields=["claim_status", "claimed_at"])
    t = remittance.transaction
    ledger(t, employee, "OUT", "Padala", t.amount, f"Padala payout {remittance.reference_no}", branch=employee.branch)
    audit(employee.user, "UPDATE", "remittances", remittance.pk, {"claim_status": "Unclaimed"}, {"claim_status": "Claimed"})


@db_transaction.atomic
def create_load(employee, customer, network, mobile_no, amount):
    require_active(customer)
    amount = q(amount)
    txn = new_txn(TransactionType.LOAD, customer, employee, amount=amount)
    MobileLoad.objects.create(transaction=txn, network=network, mobile_no=mobile_no, load_amount=amount)
    ledger(txn, employee, "IN", "Load", amount, f"{network} load {mobile_no}")
    issue_receipt(txn, "Load", "LOAD CONFIRMATION", [
        ("Network", network), ("Mobile number", mobile_no), ("Load amount", f"PHP {peso(amount)}"),
        ("Customer", customer.full_name), ("Status", "Success")],
        "Simulated for the demo. No real load was sent.")
    return txn


# ----------------------------------------------------------------------------------- auction
@db_transaction.atomic
def schedule_auction(ticket, starting_price, auction_date, employee):
    if ticket.status != "Expired":
        raise PawnError("Only expired tickets can be scheduled for auction.")
    a = Auction.objects.create(item=ticket.item, ticket=ticket, starting_price=q(starting_price),
                               auction_date=auction_date, created_by=employee)
    ticket.status = "For Auction"
    ticket.save(update_fields=["status"])
    audit(employee.user, "CREATE", "auctions", a.pk, None, {"ticket": ticket.ticket_no})
    return a


@db_transaction.atomic
def record_auction(auction, outcome, employee, bidder="", contact="", amount=None):
    if auction.status != "Scheduled":
        raise PawnError("The result for this auction was already recorded.")
    item, ticket = auction.item, auction.ticket
    if outcome == "Sold":
        if not bidder or not amount or q(amount) <= 0:
            raise PawnError("Enter the winning bidder and the sale amount.")
        AuctionSale.objects.create(auction=auction, winning_bidder_name=bidder, bidder_contact=contact, sale_amount=q(amount))
        auction.status, item.status = "Sold", "Sold"
        ledger(None, employee, "IN", "Auction Sale", amount, f"Auction sale {ticket.ticket_no} to {bidder}", branch=employee.branch)
    else:
        auction.status, item.status = "Returned to Inventory", "Returned to Inventory"
    ticket.status = "Auctioned"
    for obj in (auction, item, ticket):
        obj.save()
    audit(employee.user, "UPDATE", "auctions", auction.pk, {"status": "Scheduled"}, {"status": auction.status})


# ------------------------------------------------------------------------------ sms / alerts
def send_sms(customer, message, message_type, ticket=None):
    status = "Sent"
    if settings.SMS_BACKEND == "semaphore" and settings.SEMAPHORE_API_KEY:
        try:
            data = urllib.parse.urlencode({
                "apikey": settings.SEMAPHORE_API_KEY, "number": customer.mobile_no, "message": message,
                "sendername": settings.SEMAPHORE_SENDER}).encode()
            urllib.request.urlopen("https://api.semaphore.co/api/v4/messages", data=data, timeout=10)
        except Exception:
            status = "Failed"
    return SMSLog.objects.create(customer=customer, ticket=ticket, message_type=message_type, message=message, status=status)


def run_alerts(today=None):
    """Nightly job: reminders, overdue notices, then flag tickets past the grace period."""
    today = today or timezone.localdate()
    sent = expired = 0
    for t in PawnTicket.objects.filter(status__in=PawnTicket.OPEN).select_related("customer", "item"):
        days = (t.maturity_date - today).days
        kind = text = None
        if days == 7:
            kind, text = "Reminder", f"ticket {t.ticket_no} matures in 7 days ({t.maturity_date:%b %d})."
        elif days == 1:
            kind, text = "Reminder", f"ticket {t.ticket_no} matures tomorrow ({t.maturity_date:%b %d})."
        elif days == 0:
            kind, text = "Due Today", f"ticket {t.ticket_no} is due today. Redeem or renew at the counter."
        elif days < 0 and (-days) % 7 == 1 and today <= t.expiry_date:
            kind = "Expired"
            text = (f"ticket {t.ticket_no} is past maturity. {(t.expiry_date - today).days} days left "
                    f"before the item goes to auction.")
        if kind and not SMSLog.objects.filter(ticket=t, message_type=kind, sent_at__date=today).exists():
            send_sms(t.customer, f"Only Pawns: {text}", kind, t)
            sent += 1
        if t.expiry_date < today:
            t.status = "Expired"
            t.save(update_fields=["status"])
            t.item.status = "For Auction"
            t.item.save(update_fields=["status"])
            send_sms(t.customer, f"Only Pawns: ticket {t.ticket_no} has expired. The item is now for auction.", "Expired", t)
            audit(None, "UPDATE", "pawn_tickets", t.pk, {"status": "open"}, {"status": "Expired"})
            expired += 1
    return {"sms": sent, "expired": expired}


# --------------------------------------------------------------------------------- wrap star
def compute_wrap(customer, period=None):
    period = period or str(timezone.localdate().year)
    year = int(period[:4])
    month = int(period[5:7]) if len(period) == 7 else None
    tickets = customer.tickets.filter(loan_date__year=year)
    reds = Redemption.objects.filter(ticket__customer=customer, redeemed_at__year=year)
    if month:
        tickets, reds = tickets.filter(loan_date__month=month), reds.filter(redeemed_at__month=month)
    pawned = tickets.aggregate(s=Sum("loan_amount"), n=Count("id"))
    redeemed = reds.aggregate(s=Sum("principal_paid"), n=Count("id"))
    on_time = sum(1 for r in reds.select_related("ticket") if timezone.localtime(r.redeemed_at).date() <= r.ticket.maturity_date)
    rate = q(on_time * 100 / redeemed["n"]) if redeemed["n"] else ZERO
    top = (tickets.values("item__category").annotate(c=Count("id")).order_by("-c").first())
    n = redeemed["n"]
    level = "VIP Star" if n >= 5 and rate >= 80 else "Regular" if n >= 2 else "Rookie"
    obj, _ = WrapSummary.objects.update_or_create(
        customer=customer, period_label=period,
        defaults=dict(total_pawned=pawned["s"] or ZERO, total_redeemed=redeemed["s"] or ZERO, items_pawned=pawned["n"] or 0,
                      items_redeemed=n, on_time_rate=rate, star_level=level, generated_at=timezone.now(),
                      top_category=ItemCategory.objects.filter(pk=top["item__category"]).first() if top else None))
    return obj


# -------------------------------------------------------------------------------- bot star
def bot_reply(customer, text):
    """Returns (reply, wants_human). Plain keyword matching, no outside service."""
    t = text.lower()

    def has(*words):
        return any(w in t for w in words)

    if has("human", "agent", "staff", "real person", "talk to someone", "complain"):
        return ("Okay, I'll pass this to Pawn Patrol. Someone from the team will reply to you in the Help tab.", True)
    if has("my ticket", "my loan", "balance", "how much do i owe", "due"):
        tickets = customer.tickets.filter(status__in=PawnTicket.OPEN)
        if not tickets:
            return ("You don't have any open tickets right now.", False)
        parts = []
        for tk in tickets:
            d = compute_due(tk)
            parts.append(f"{tk.ticket_no}: PHP {peso(d['redeem_total'])} to redeem today, matures {tk.maturity_date:%b %d, %Y}.")
        return ("Here's where your open tickets stand:\n" + "\n".join(parts), False)
    if has("rate", "interest", "how much"):
        lines = [f"{c.category_name}: {c.monthly_interest_rate * 100:.2f}% a month" for c in ItemCategory.objects.all()]
        return ("Interest is charged per month, and a partial month counts as a full one.\n" + "\n".join(lines), False)
    if has("require", "bring", "id ", "valid id", "register"):
        ids = ", ".join(IDType.objects.values_list("id_type_name", flat=True))
        return (f"You need to be 18 or older and bring one valid ID: {ids}. Register in the app and a staff member will verify your ID.", False)
    if has("redeem", "get back", "claim my"):
        return ("To redeem, go to any branch with your ticket (or its QR code) and a valid ID. You pay the loan, the interest and the service fee. The cashier hands the item back and prints your receipt.", False)
    if has("renew", "extend"):
        return ("Renewal means paying only the interest. Your maturity date moves one month ahead and the loan stays the same. You can do it at the counter.", False)
    if has("grace", "auction", "expire", "forfeit"):
        return ("After the maturity date you still have a grace period (90 days for most items). If the ticket isn't redeemed by the last day printed on it, the item goes to auction.", False)
    if has("hour", "open", "branch", "where", "location"):
        b = ", ".join(f"{x.branch_name} ({x.open_time:%I:%M %p}-{x.close_time:%I:%M %p})" for x in Branch.objects.filter(status="Active"))
        return (f"Our branches: {b}. The Branches tab shows them on the map.", False)
    if has("padala", "remit", "send money"):
        return ("Padala is done at the counter. Give the cashier the receiver's name, mobile number and the branch where they will claim. You get a reference number for the receiver.", False)
    if has("load"):
        return ("You can buy prepaid load at the counter for Globe, Smart and DITO.", False)
    if has("hello", "hi", "hey", "good morning", "good afternoon", "good evening"):
        return ("Hi, I'm Bot Star. Ask me about rates, requirements, redeeming, renewing, branch hours, or your own tickets.", False)
    if has("thank"):
        return ("You're welcome.", False)
    return ("I'm not sure about that one. Try asking about rates, requirements, redeeming, renewing, auctions or branch hours. "
            "If you'd rather talk to a person, type \"agent\".", False)


def escalate_chat(session, customer, subject="Chat from Bot Star"):
    session.escalated = True
    session.save(update_fields=["escalated"])
    last = list(session.messages.filter(sender="Customer").values_list("message", flat=True))[-3:]
    ticket = SupportTicket.objects.create(customer=customer, session=session, subject=subject, category="Inquiry")
    from .models import SupportMessage
    SupportMessage.objects.create(support_ticket=ticket, sender_user=customer.user,
                                  message="Handed over from Bot Star. Recent messages:\n" + "\n".join(last))
    return ticket


# ------------------------------------------------------------------------------ google signin
def verify_google_token(id_token):
    """Checks a Google ID token with Google. Returns {"email", "sub", "name"} or None."""
    if not settings.GOOGLE_CLIENT_ID:
        return None
    try:
        url = "https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(id_token)
        data = json.loads(urllib.request.urlopen(url, timeout=10).read())
    except Exception:
        return None
    if data.get("aud") != settings.GOOGLE_CLIENT_ID or data.get("email_verified") not in ("true", True):
        return None
    return {"email": data["email"].lower(), "sub": data["sub"], "name": data.get("name", "")}


# -------------------------------------------------------------------------------- reports
def _txn_rows(qs):
    return [[t.transaction_no, timezone.localtime(t.created_at).strftime("%I:%M %p"), t.transaction_type.type_name,
             t.customer.full_name, peso(t.amount), peso(t.fee), t.status, t.employee.full_name if t.employee else "App"]
            for t in qs]


def build_report(name, employee, params):
    """Returns dict(title, headers, rows, summary). Cashiers only ever get their own shift."""
    today = timezone.localdate()
    start = params.get("start") or today
    end = params.get("end") or start
    if name == "daily":
        qs = Transaction.objects.filter(created_at__date__gte=start, created_at__date__lte=end).select_related(
            "customer", "transaction_type", "employee")
        if not employee.user.is_admin_role:
            qs = qs.filter(employee=employee)
        rows = _txn_rows(qs.order_by("created_at"))
        total = sum((t.amount for t in qs.exclude(status="Voided")), ZERO)
        return {"title": f"Daily Transaction Report ({start} to {end})",
                "headers": ["Transaction", "Time", "Type", "Customer", "Amount (PHP)", "Fee", "Status", "Handled by"],
                "rows": rows, "summary": [f"{len(rows)} transactions", f"Total amount PHP {peso(total)}"]}
    if name == "active":
        rows, total = [], ZERO
        for t in PawnTicket.objects.filter(status__in=PawnTicket.OPEN).select_related("customer", "item", "item__category"):
            d = compute_due(t)
            total += t.loan_amount
            rows.append([t.ticket_no, t.customer.full_name, t.item.item_type, peso(t.loan_amount), str(t.maturity_date),
                         str(t.expiry_date), peso(d["redeem_total"]), t.status])
        return {"title": f"Summary of Active Loans (as of {today})",
                "headers": ["Ticket", "Customer", "Item", "Loan (PHP)", "Maturity", "Last day", "Due today", "Status"],
                "rows": rows, "summary": [f"{len(rows)} open tickets", f"Principal out on loan PHP {peso(total)}"]}
    if name == "auction":
        rows = []
        for r in Redemption.objects.filter(redeemed_at__date__gte=start, redeemed_at__date__lte=end).select_related("ticket", "ticket__customer"):
            rows.append(["Redemption", r.ticket.ticket_no, r.ticket.customer.full_name, str(timezone.localtime(r.redeemed_at).date()),
                         "Redeemed", peso(r.total_paid)])
        for a in Auction.objects.filter(auction_date__gte=start, auction_date__lte=end).select_related("ticket", "ticket__customer"):
            sale = getattr(a, "sale", None)
            rows.append(["Auction", a.ticket.ticket_no, a.ticket.customer.full_name, str(a.auction_date), a.status,
                         peso(sale.sale_amount) if sale else "0.00"])
        return {"title": f"Auction and Redemption Report ({start} to {end})",
                "headers": ["Kind", "Ticket", "Customer", "Date", "Result", "Amount (PHP)"], "rows": rows,
                "summary": [f"{len(rows)} records"]}
    if name == "cashflow":
        rows, tin, tout = [], ZERO, ZERO
        for b in Branch.objects.all():
            led = CashLedger.objects.filter(branch=b, recorded_at__date__gte=start, recorded_at__date__lte=end)
            i = led.filter(direction="IN").aggregate(s=Sum("amount"))["s"] or ZERO
            o = led.filter(direction="OUT").aggregate(s=Sum("amount"))["s"] or ZERO
            tin, tout = tin + i, tout + o
            rows.append([b.branch_name, peso(i), peso(o), peso(i - o)])
        return {"title": f"Cash Flow per Branch ({start} to {end})", "headers": ["Branch", "Cash in", "Cash out", "Net"],
                "rows": rows, "summary": [f"Total in PHP {peso(tin)}", f"Total out PHP {peso(tout)}", f"Net PHP {peso(tin - tout)}"]}
    raise PawnError("Unknown report.")


def report_pdf(rep):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=28)
    st = getSampleStyleSheet()
    parts = [Paragraph("Only Pawns", st["Title"]), Paragraph(rep["title"], st["Heading3"]), Spacer(1, 8)]
    data = [rep["headers"]] + (rep["rows"] or [["No records"] + [""] * (len(rep["headers"]) - 1)])
    tbl = Table(data, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14141C")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F2F5")])]))
    parts += [tbl, Spacer(1, 10)] + [Paragraph(s, st["Normal"]) for s in rep["summary"]]
    doc.build(parts)
    return buf.getvalue()


def report_xlsx(rep):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    ws.append([rep["title"]])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    ws.append(rep["headers"])
    for c in ws[3]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="14141C")
    for r in rep["rows"]:
        ws.append(r)
    ws.append([])
    for s in rep["summary"]:
        ws.append([s])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 3, 40)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
