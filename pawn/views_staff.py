from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.paginator import Paginator
from django.db.models import Count, ProtectedError, Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import forms as F
from . import services as S
from .models import (
    Appraisal, Auction, AuditLog, BackupLog, Branch, CashLedger, Currency, Customer, CustomerID, Employee,
    ExchangeRate, IDType, ItemCategory, MobileLoad, PawnTicket, Receipt, Remittance, Role, SMSLog, SupportMessage,
    SupportTicket, Transaction, User,
)

ADMIN, CASHIER, APPRAISER = Role.ADMIN, Role.CASHIER, Role.APPRAISER


# ------------------------------------------------------------------------------- plumbing
def staff_only(*roles):
    """Employees only. Administrators pass every role check."""
    def deco(view):
        @wraps(view)
        def inner(request, *a, **kw):
            u = request.user
            if not u.is_authenticated or not u.is_staff_user or not hasattr(u, "employee"):
                return redirect(f"/staff/login/?next={request.path}")
            if roles and not u.is_admin_role and u.role_name not in roles:
                messages.error(request, "Your role does not have access to that page.")
                return redirect("staff_dashboard")
            request.emp = u.employee
            return view(request, *a, **kw)
        return inner
    return deco


def paginate(request, qs, per=20):
    page = Paginator(qs, per).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page, params.urlencode()


def table(request, title, headers, rows, page=None, qs="", **extra):
    ctx = {"title": title, "headers": headers, "rows": rows, "page": page, "qs": qs}
    ctx.update(extra)
    return render(request, "staff/table.html", ctx)


def form_page(request, title, form, back, multipart=False, submit="Save", note=""):
    return render(request, "staff/form.html", {"title": title, "form": form, "back": back,
                                               "multipart": multipart, "submit": submit, "note": note})


def P(v):
    return f"₱{S.peso(v)}"


def fail(request, err):
    messages.error(request, str(err))


# ------------------------------------------------------------------------------- login
def staff_login(request):
    if request.user.is_authenticated and request.user.is_staff_user:
        return redirect("staff_dashboard")
    error = ""
    if request.method == "POST":
        name, pw = request.POST.get("username", "").strip(), request.POST.get("password", "")
        user = User.objects.filter(username=name).select_related("role").first()
        if user and user.is_locked():
            error = "Too many wrong passwords. Try again in a few minutes."
        else:
            auth = authenticate(request, username=name, password=pw)
            if auth and auth.is_staff_user and auth.is_active:
                auth.failed_pin_attempts, auth.locked_until, auth.last_login_at = 0, None, timezone.now()
                auth.save(update_fields=["failed_pin_attempts", "locked_until", "last_login_at"])
                login(request, auth)
                S.audit(auth, "LOGIN", "users", auth.pk)
                nxt = request.GET.get("next", "")
                return redirect(nxt if nxt.startswith("/staff/") else "staff_dashboard")
            if user and user.is_staff_user:
                user.failed_pin_attempts += 1
                if user.failed_pin_attempts >= 5:
                    user.locked_until = timezone.now() + timedelta(minutes=15)
                    user.failed_pin_attempts = 0
                user.save(update_fields=["failed_pin_attempts", "locked_until"])
            error = "That username and password don't match."
    return render(request, "staff/login.html", {"error": error})


def staff_logout(request):
    logout(request)
    return redirect("staff_login")


# --------------------------------------------------------------------------- dashboard
@staff_only()
def dashboard(request):
    today = timezone.localdate()
    open_t = PawnTicket.objects.filter(status__in=PawnTicket.OPEN).select_related("customer", "item")
    soon = open_t.filter(maturity_date__lte=today + timedelta(days=7)).order_by("maturity_date")
    led = CashLedger.objects.filter(recorded_at__date=today)
    if not request.user.is_admin_role:
        led = led.filter(branch=request.emp.branch)
    ctx = {
        "open_count": open_t.count(), "principal": open_t.aggregate(s=Sum("loan_amount"))["s"] or 0,
        "soon": [(t, S.compute_due(t)) for t in soon[:12]],
        "cash_in": led.filter(direction="IN").aggregate(s=Sum("amount"))["s"] or 0,
        "cash_out": led.filter(direction="OUT").aggregate(s=Sum("amount"))["s"] or 0,
        "pending": Customer.objects.filter(status="Pending Verification").count(),
        "support_open": SupportTicket.objects.exclude(status="Resolved").count(),
        "for_auction": PawnTicket.objects.filter(status="Expired").count(),
        "recent": Transaction.objects.select_related("customer", "transaction_type").order_by("-created_at")[:8],
        "pending_appraisals": Appraisal.objects.filter(result="Pending").count(),
    }
    return render(request, "staff/dashboard.html", ctx)


# --------------------------------------------------------------------------- customers
@staff_only(CASHIER, APPRAISER)
def customers(request):
    qs = Customer.objects.all()
    q = request.GET.get("q", "").strip()
    st = request.GET.get("status", "")
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(code__icontains=q) | Q(mobile_no__icontains=q))
    if st:
        qs = qs.filter(status=st)
    page, qstr = paginate(request, qs)
    rows = [{"cells": [c.code, c.full_name, c.mobile_no, c.status, f"{c.registered_at:%b %d, %Y}"], "url": f"/staff/customers/{c.pk}/",
             "badge": 3} for c in page]
    return table(request, "Customers", ["ID", "Name", "Mobile", "Status", "Registered"], rows, page, qstr,
                 search=True, filter_name="status", filter_options=["Pending Verification", "Active", "Deactivated"],
                 add_url="/staff/customers/new/", add_label="Register customer")


@staff_only(CASHIER)
def customer_new(request):
    form = F.RegistrationForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        c = form.save()
        S.audit(request.user, "CREATE", "customers", c.pk, None, {"code": c.code})
        messages.success(request, f"{c.full_name} is registered as {c.code}. Verify the ID to activate the account.")
        return redirect("customer_detail", pk=c.pk)
    return form_page(request, "Register a customer", form, "/staff/customers/", multipart=True, submit="Register",
                     note="Walk-ins finish their Google sign-up later in the app, using this customer ID and mobile number.")


@staff_only(CASHIER, APPRAISER)
def customer_detail(request, pk):
    c = get_object_or_404(Customer, pk=pk)
    ids = [(i, S.mask(S.decrypt_text(i.id_number_enc))) for i in c.ids.select_related("id_type")]
    return render(request, "staff/customer_detail.html", {
        "c": c, "ids": ids, "tickets": c.tickets.select_related("item")[:10],
        "txns": c.transactions.select_related("transaction_type")[:10]})


@staff_only(CASHIER)
def customer_edit(request, pk):
    c = get_object_or_404(Customer, pk=pk)
    form = F.CustomerEditForm(request.POST or None, instance=c)
    if request.method == "POST" and form.is_valid():
        old = {f: str(getattr(c, f)) for f in form.changed_data}
        form.save()
        S.audit(request.user, "UPDATE", "customers", c.pk, old, {f: str(getattr(c, f)) for f in form.changed_data})
        messages.success(request, "Customer updated.")
        return redirect("customer_detail", pk=c.pk)
    return form_page(request, f"Edit {c.full_name}", form, f"/staff/customers/{c.pk}/")


@staff_only(CASHIER)
@require_POST
def customer_status(request, pk):
    c = get_object_or_404(Customer, pk=pk)
    act = request.POST.get("action")
    old = c.status
    if act == "verify":
        c.status, c.verified_by, c.verified_at = "Active", request.emp, timezone.now()
        c.ids.exclude(verification_status="Rejected").update(verification_status="Verified")
        if c.user_id:
            c.user.status = "Active"
            c.user.save(update_fields=["status"])
    elif act == "reject":
        c.ids.update(verification_status="Rejected")
        messages.info(request, "ID marked as rejected. The customer stays pending until a new ID is submitted.")
        S.audit(request.user, "UPDATE", "customer_ids", c.pk, None, {"status": "Rejected"})
        return redirect("customer_detail", pk=c.pk)
    elif act == "deactivate":
        c.status = "Deactivated"
        if c.user_id:
            c.user.status = "Inactive"
            c.user.save(update_fields=["status"])
    elif act == "reactivate":
        c.status = "Active"
    c.save()
    S.audit(request.user, "UPDATE", "customers", c.pk, {"status": old}, {"status": c.status})
    messages.success(request, f"{c.full_name} is now {c.status.lower()}.")
    return redirect("customer_detail", pk=c.pk)


@staff_only(CASHIER, APPRAISER)
def id_image(request, pk, side):
    cid = get_object_or_404(CustomerID, pk=pk)
    f = {"front": cid.front_image, "back": cid.back_image, "selfie": cid.selfie_image}.get(side)
    if not f:
        raise Http404
    S.audit(request.user, "VIEW_ID", "customer_ids", cid.pk)
    return HttpResponse(S.decrypt_bytes(f.read()), content_type="image/jpeg")


# ---------------------------------------------------------------------------- appraisal
@staff_only(APPRAISER, CASHIER)
def appraisals(request):
    qs = Appraisal.objects.select_related("item", "item__customer", "appraiser")
    page, qstr = paginate(request, qs)
    rows = [{"cells": [a.transaction.transaction_no, a.item.customer.full_name, a.item.item_type, P(a.appraised_value),
                       P(a.suggested_loan), a.result], "url": f"/staff/appraisals/{a.pk}/", "badge": 5} for a in page]
    return table(request, "Appraisals", ["Transaction", "Customer", "Item", "Appraised", "Suggested loan", "Result"], rows, page, qstr,
                 add_url="/staff/appraisals/new/" if request.user.role_name in (APPRAISER, ADMIN) else None,
                 add_label="New appraisal")


@staff_only(APPRAISER)
def appraisal_new(request):
    form = F.AppraisalForm(request.POST or None, request.FILES or None,
                           initial={"customer": request.GET.get("customer")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            a = S.create_appraisal(request.emp, d["customer"], d["category"], d["item_type"], d["description"], d["item_condition"],
                                   d["appraised_value"], d["suggested_loan"], d["remarks"], d["photos"])
        except S.PawnError as e:
            fail(request, e)
        else:
            messages.success(request, "Appraisal saved. The cashier can now issue the pawn ticket.")
            return redirect("appraisal_detail", pk=a.pk)
    return form_page(request, "New appraisal", form, "/staff/appraisals/", multipart=True, submit="Save appraisal")


@staff_only(APPRAISER, CASHIER)
def appraisal_detail(request, pk):
    a = get_object_or_404(Appraisal.objects.select_related("item", "item__customer", "item__category"), pk=pk)
    form = F.TicketForm(initial={"loan_amount": a.suggested_loan})
    return render(request, "staff/appraisal_detail.html", {"a": a, "form": form, "images": a.item.images.all()})


@staff_only(CASHIER)
@require_POST
def appraisal_action(request, pk):
    a = get_object_or_404(Appraisal, pk=pk)
    try:
        if request.POST.get("action") == "decline":
            S.decline_appraisal(a, request.emp)
            messages.info(request, "Appraisal declined by the customer.")
            return redirect("appraisal_detail", pk=a.pk)
        form = F.TicketForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Check the loan amount.")
            return redirect("appraisal_detail", pk=a.pk)
        t = S.create_ticket(a, form.cleaned_data["loan_amount"], request.emp, form.cleaned_data["kind"])
        messages.success(request, f"Ticket {t.ticket_no} issued. Release PHP {S.peso(t.loan_amount)} to the customer.")
        return redirect("ticket_detail", pk=t.pk)
    except S.PawnError as e:
        fail(request, e)
    return redirect("appraisal_detail", pk=a.pk)


# ------------------------------------------------------------------------------- tickets
@staff_only(CASHIER, APPRAISER)
def tickets(request):
    qs = PawnTicket.objects.select_related("customer", "item")
    q, st = request.GET.get("q", "").strip(), request.GET.get("status", "")
    if q:
        qs = qs.filter(Q(ticket_no__icontains=q) | Q(customer__last_name__icontains=q) | Q(customer__first_name__icontains=q))
    if st:
        qs = qs.filter(status=st)
    page, qstr = paginate(request, qs)
    rows = [{"cells": [t.ticket_no, t.customer.full_name, t.item.item_type, P(t.loan_amount), f"{t.maturity_date:%b %d, %Y}", t.status],
             "url": f"/staff/tickets/{t.pk}/", "badge": 5} for t in page]
    return table(request, "Pawn tickets", ["Ticket", "Customer", "Item", "Loan", "Maturity", "Status"], rows, page, qstr, search=True,
                 filter_name="status", filter_options=["Active", "Renewed", "Redeemed", "Expired", "For Auction", "Auctioned"])


@staff_only(CASHIER, APPRAISER)
def ticket_detail(request, pk):
    t = get_object_or_404(PawnTicket.objects.select_related("customer", "item", "item__category", "transaction"), pk=pk)
    return render(request, "staff/ticket_detail.html", {
        "t": t, "due": S.compute_due(t) if t.is_open else None, "renewals": t.renewals.all(),
        "images": t.item.images.all(), "receipt": getattr(t.transaction, "receipt", None)})


def _pay(request, pk, mode):
    t = get_object_or_404(PawnTicket, pk=pk)
    due = S.compute_due(t) if t.is_open else None
    total = None if not due else (due["redeem_total"] if mode == "redeem" else due["renew_total"])
    form = F.PaymentForm(request.POST or None, initial={"tendered": total})
    if request.method == "POST" and form.is_valid():
        try:
            fn = S.redeem_ticket if mode == "redeem" else S.renew_ticket
            txn = fn(t, form.cleaned_data["tendered"], request.emp)
        except S.PawnError as e:
            fail(request, e)
        else:
            messages.success(request, f"{'Redemption' if mode == 'redeem' else 'Renewal'} recorded ({txn.transaction_no}).")
            return redirect("txn_detail", pk=txn.pk)
    return render(request, "staff/pay.html", {"t": t, "due": due, "mode": mode, "form": form, "total": total})


@staff_only(CASHIER)
def ticket_redeem(request, pk):
    return _pay(request, pk, "redeem")


@staff_only(CASHIER)
def ticket_renew(request, pk):
    return _pay(request, pk, "renew")


@staff_only(CASHIER, APPRAISER)
def txn_detail(request, pk):
    t = get_object_or_404(Transaction.objects.select_related("customer", "transaction_type", "branch", "employee", "currency"), pk=pk)
    ticket = None
    for rel in ("pawn_ticket", "redemption", "renewal"):
        o = getattr(t, rel, None)
        if o:
            ticket = o if rel == "pawn_ticket" else o.ticket
            break
    return render(request, "staff/txn_detail.html", {"t": t, "ticket": ticket, "receipt": getattr(t, "receipt", None),
                                                    "ledger": t.ledger.all()})


@staff_only(CASHIER, APPRAISER)
def lookup(request):
    term = request.GET.get("q", "").strip()
    if term:
        no = S.txn_no_from_token(term) or term
        t = Transaction.objects.filter(transaction_no__iexact=no).first()
        if t:
            return redirect("txn_detail", pk=t.pk)
        tk = PawnTicket.objects.filter(ticket_no__iexact=term).first()
        if tk:
            return redirect("ticket_detail", pk=tk.pk)
        r = Remittance.objects.filter(reference_no__iexact=term).first()
        if r:
            return redirect("txn_detail", pk=r.transaction_id)
        messages.error(request, "Nothing matches that number or QR code.")
    return render(request, "staff/lookup.html", {"q": term})


def receipt_file(request, txn_no):
    """Receipts are only handed to employees or to the customer they belong to."""
    r = get_object_or_404(Receipt.objects.select_related("transaction__customer"), transaction__transaction_no=txn_no)
    u = request.user
    ok = u.is_authenticated and (u.is_staff_user or (u.is_client and getattr(u, "customer", None) == r.transaction.customer))
    if not ok:
        raise Http404
    return FileResponse(r.file.open("rb"), content_type="application/pdf", filename=f"{r.receipt_no}.pdf")


# ------------------------------------------------------------------------- padala / load
@staff_only(CASHIER)
def padala(request):
    form = F.PadalaForm(request.POST or None, initial={"receiver_branch": request.emp.branch})
    claim = F.ClaimForm()
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            rem = S.create_padala(request.emp, d["customer"], d["currency"], d["amount"], d["receiver_name"],
                                  d["receiver_mobile"], d["receiver_branch"])
        except S.PawnError as e:
            fail(request, e)
        else:
            messages.success(request, f"Padala sent. Reference number {rem.reference_no}.")
            return redirect("txn_detail", pk=rem.transaction_id)
    recent = Remittance.objects.select_related("transaction__customer", "receiver_branch").order_by("-created_at")[:10]
    return render(request, "staff/padala.html", {"form": form, "claim": claim, "recent": recent})


@staff_only(CASHIER)
@require_POST
def padala_claim(request):
    form = F.ClaimForm(request.POST)
    if form.is_valid():
        rem = Remittance.objects.filter(reference_no__iexact=form.cleaned_data["reference_no"].strip()).first()
        if not rem or rem.receiver_mobile != form.cleaned_data["receiver_mobile"]:
            messages.error(request, "Reference number and mobile number don't match any unclaimed padala.")
        else:
            try:
                S.claim_padala(rem, request.emp)
                messages.success(request, f"Release PHP {S.peso(rem.transaction.amount)} to {rem.receiver_name} after checking their ID.")
            except S.PawnError as e:
                fail(request, e)
    return redirect("padala")


@staff_only(CASHIER)
def load(request):
    form = F.LoadForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            txn = S.create_load(request.emp, d["customer"], d["network"], d["mobile_no"], d["load_amount"])
        except S.PawnError as e:
            fail(request, e)
        else:
            messages.success(request, "Load sent (simulated).")
            return redirect("txn_detail", pk=txn.pk)
    recent = MobileLoad.objects.select_related("transaction__customer").order_by("-created_at")[:10]
    return render(request, "staff/load.html", {"form": form, "recent": recent})


# --------------------------------------------------------------------------------- auction
@staff_only(ADMIN)
def auctions(request):
    ready = PawnTicket.objects.filter(status="Expired").select_related("customer", "item")
    sched = Auction.objects.select_related("ticket", "item").order_by("-auction_date")
    return render(request, "staff/auctions.html", {"ready": [(t, S.compute_due(t)) for t in ready], "auctions": sched})


@staff_only(ADMIN)
def auction_new(request, ticket_pk):
    t = get_object_or_404(PawnTicket, pk=ticket_pk)
    d = S.compute_due(t) if t.status == "Expired" else None
    form = F.AuctionForm(request.POST or None, initial={"starting_price": d["redeem_total"] if d else None})
    if request.method == "POST" and form.is_valid():
        try:
            S.schedule_auction(t, form.cleaned_data["starting_price"], form.cleaned_data["auction_date"], request.emp)
        except S.PawnError as e:
            fail(request, e)
        else:
            messages.success(request, "Auction scheduled.")
            return redirect("auctions")
    return form_page(request, f"Schedule auction for {t.ticket_no}", form, "/staff/auctions/", submit="Schedule",
                     note="The starting price defaults to the loan plus accrued interest and fees.")


@staff_only(ADMIN)
def auction_result(request, pk):
    a = get_object_or_404(Auction, pk=pk)
    form = F.AuctionResultForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            S.record_auction(a, d["outcome"], request.emp, d["winning_bidder_name"], d["bidder_contact"], d["sale_amount"])
        except S.PawnError as e:
            fail(request, e)
        else:
            messages.success(request, "Auction result recorded.")
            return redirect("auctions")
    return form_page(request, f"Auction result: {a.ticket.ticket_no}", form, "/staff/auctions/", submit="Record result")


# ----------------------------------------------------------------------------- cash ledger
@staff_only(CASHIER)
def ledger_view(request):
    qs = CashLedger.objects.select_related("branch", "recorded_by")
    if not request.user.is_admin_role:
        qs = qs.filter(branch=request.emp.branch)
    page, qstr = paginate(request, qs, 25)
    rows = [{"cells": [f"{timezone.localtime(l.recorded_at):%b %d %I:%M %p}", l.branch.branch_name, l.direction, l.category,
                       l.description, P(l.amount), l.recorded_by.full_name], "badge": 2} for l in page]
    return table(request, "Cash ledger", ["When", "Branch", "Direction", "Category", "Description", "Amount", "By"], rows, page, qstr,
                 add_url="/staff/ledger/new/", add_label="Record expense or refund")


@staff_only(CASHIER)
def ledger_new(request):
    form = F.ExpenseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        row = S.ledger(None, request.emp, "OUT", d["category"], d["amount"], d["description"], branch=request.emp.branch)
        S.audit(request.user, "CREATE", "cash_ledger", row.pk, None, {"amount": str(row.amount), "category": row.category})
        messages.success(request, "Recorded.")
        return redirect("ledger")
    return form_page(request, "Record cash out", form, "/staff/ledger/")


# ------------------------------------------------------------------------------- reports
REPORTS = [("daily", "Daily Transaction Report", "Every transaction in a date range."),
           ("active", "Summary of Active Loans", "Open tickets and what each one costs to redeem today."),
           ("auction", "Auction and Redemption Report", "Redemptions and auction results in a date range."),
           ("cashflow", "Cash Flow per Branch", "Money in, money out and the net, per branch.")]


@staff_only(CASHIER)
def reports(request):
    allowed = REPORTS if request.user.is_admin_role else REPORTS[:1]
    return render(request, "staff/reports.html", {"reports": allowed, "today": timezone.localdate()})


@staff_only(CASHIER)
def report_view(request, name):
    if name != "daily" and not request.user.is_admin_role:
        messages.error(request, "Only the administrator can open that report.")
        return redirect("reports")
    from django.utils.dateparse import parse_date
    params = {"start": parse_date(request.GET.get("start", "")), "end": parse_date(request.GET.get("end", ""))}
    try:
        rep = S.build_report(name, request.emp, {k: v for k, v in params.items() if v})
    except S.PawnError as e:
        fail(request, e)
        return redirect("reports")
    fmt = request.GET.get("fmt")
    if fmt == "pdf":
        return HttpResponse(S.report_pdf(rep), content_type="application/pdf",
                            headers={"Content-Disposition": f'attachment; filename="{name}-report.pdf"'})
    if fmt == "xlsx":
        return HttpResponse(S.report_xlsx(rep), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f'attachment; filename="{name}-report.xlsx"'})
    rows = [{"cells": r} for r in rep["rows"]]
    return table(request, rep["title"], rep["headers"], rows, report=True, report_name=name, summary=rep["summary"],
                 start=request.GET.get("start", ""), end=request.GET.get("end", ""), date_filter=name != "active")


# ---------------------------------------------------------------------------------- support
@staff_only(CASHIER)
def support(request):
    qs = SupportTicket.objects.select_related("customer")
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET["status"])
    page, qstr = paginate(request, qs)
    rows = [{"cells": [s.subject, s.customer.full_name, s.category, s.priority, s.status, f"{s.created_at:%b %d}"],
             "url": f"/staff/support/{s.pk}/", "badge": 4} for s in page]
    return table(request, "Pawn Patrol", ["Subject", "Customer", "Category", "Priority", "Status", "Opened"], rows, page, qstr,
                 filter_name="status", filter_options=["Open", "In Progress", "Resolved"])


@staff_only(CASHIER)
def support_detail(request, pk):
    s = get_object_or_404(SupportTicket.objects.select_related("customer"), pk=pk)
    form = F.ReplyForm(request.POST or None, initial={"status": "In Progress"})
    if request.method == "POST" and form.is_valid():
        SupportMessage.objects.create(support_ticket=s, sender_user=request.user, message=form.cleaned_data["message"])
        s.assigned_to = s.assigned_to or request.emp
        s.status = form.cleaned_data["status"] or s.status
        s.resolved_at = timezone.now() if s.status == "Resolved" else None
        s.save()
        messages.success(request, "Reply sent.")
        return redirect("support_detail", pk=s.pk)
    return render(request, "staff/support_detail.html", {"s": s, "form": form})


# ----------------------------------------------------------------------------- queue counter
@staff_only(CASHIER)
@require_POST
def queue_adjust(request, pk):
    b = get_object_or_404(Branch, pk=pk)
    delta = 1 if request.POST.get("delta") == "1" else -1
    b.queue_count = max(0, b.queue_count + delta)
    b.save(update_fields=["queue_count"])
    return redirect(request.POST.get("next") or "staff_dashboard")


# ------------------------------------------------------------------- admin: simple CRUD
def make_crud(key, model, form_cls, title, headers, row_fn, roles=(ADMIN,)):
    @staff_only(*roles)
    def lst(request):
        rows = []
        for o in model.objects.all():
            rows.append({"cells": row_fn(o), "url": f"/staff/admin/{key}/{o.pk}/edit/", "delete": f"/staff/admin/{key}/{o.pk}/delete/"})
        return table(request, title, headers, rows, add_url=f"/staff/admin/{key}/new/", add_label="Add")

    @staff_only(*roles)
    def edit(request, pk=None):
        obj = get_object_or_404(model, pk=pk) if pk else None
        form = form_cls(request.POST or None, instance=obj)
        if request.method == "POST" and form.is_valid():
            saved = form.save()
            S.audit(request.user, "UPDATE" if obj else "CREATE", model._meta.db_table.replace("pawn_", ""), saved.pk)
            messages.success(request, "Saved.")
            return redirect(f"{key}_list")
        return form_page(request, f"{'Edit' if obj else 'Add'}: {title}", form, f"/staff/admin/{key}/")

    @staff_only(*roles)
    @require_POST
    def delete(request, pk):
        obj = get_object_or_404(model, pk=pk)
        try:
            obj.delete()
            S.audit(request.user, "DELETE", model._meta.db_table.replace("pawn_", ""), pk)
            messages.success(request, "Deleted.")
        except ProtectedError:
            messages.error(request, "That record is already used by other data, so it can't be deleted.")
        return redirect(f"{key}_list")

    return lst, edit, delete


CRUDS = {
    "branches": make_crud("branches", Branch, F.BranchForm, "Branches", ["Branch", "Address", "Hours", "Counters", "Status"],
                          lambda b: [b.branch_name, b.address, f"{b.open_time:%I:%M %p} - {b.close_time:%I:%M %p}", b.open_counters, b.status]),
    "categories": make_crud("categories", ItemCategory, F.CategoryForm, "Item categories and rates",
                            ["Category", "Monthly interest", "Service fee", "Grace (days)"],
                            lambda c: [c.category_name, f"{c.monthly_interest_rate * 100:.2f}%", P(c.service_fee), c.grace_period_days]),
    "currencies": make_crud("currencies", Currency, F.CurrencyForm, "Currencies", ["Code", "Name", "Symbol", "Rate to PHP"],
                            lambda c: [c.code, c.name, c.symbol, f"{c.rate():.4f}"]),
    "rates": make_crud("rates", ExchangeRate, F.RateForm, "Exchange rates", ["Currency", "Rate to PHP", "Effective"],
                       lambda r: [r.currency.code, f"{r.rate_to_php:.4f}", str(r.effective_date)]),
    "idtypes": make_crud("idtypes", IDType, F.IDTypeForm, "Accepted IDs", ["ID type"], lambda i: [i.id_type_name]),
}


@staff_only(ADMIN)
def employees(request):
    rows = [{"cells": [e.full_name, e.user.username, e.user.role_name, e.branch.branch_name, e.status],
             "url": f"/staff/admin/employees/{e.pk}/edit/", "badge": 4} for e in Employee.objects.select_related("user", "user__role", "branch")]
    return table(request, "Employees", ["Name", "Username", "Role", "Branch", "Status"], rows,
                 add_url="/staff/admin/employees/new/", add_label="Add employee")


@staff_only(ADMIN)
def employee_edit(request, pk=None):
    emp = get_object_or_404(Employee, pk=pk) if pk else None
    form = F.EmployeeForm(request.POST or None, instance=emp)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        S.audit(request.user, "UPDATE" if emp else "CREATE", "employees", saved.pk)
        messages.success(request, "Employee saved.")
        return redirect("employees")
    return form_page(request, "Edit employee" if emp else "Add employee", form, "/staff/admin/employees/")


@staff_only(ADMIN)
def audit_log(request):
    qs = AuditLog.objects.select_related("user")
    if request.GET.get("q"):
        qs = qs.filter(Q(table_name__icontains=request.GET["q"]) | Q(action__icontains=request.GET["q"]) | Q(user__username__icontains=request.GET["q"]))
    page, qstr = paginate(request, qs, 30)
    rows = [{"cells": [f"{timezone.localtime(a.logged_at):%b %d %I:%M:%S %p}", a.user.username if a.user else "system", a.action,
                       a.table_name, a.record_id or "", str(a.new_value or "")[:70]]} for a in page]
    return table(request, "Audit log", ["When", "User", "Action", "Table", "Record", "Change"], rows, page, qstr, search=True)


@staff_only(ADMIN)
def backups(request):
    if request.method == "POST":
        from django.core.management import call_command
        call_command("backup")
        messages.success(request, "Backup finished.")
        return redirect("backups")
    rows = [{"cells": [f"{timezone.localtime(b.run_at):%b %d, %Y %I:%M %p}", b.file_path, f"{b.size_mb} MB", b.status], "badge": 3}
            for b in BackupLog.objects.all()[:30]]
    return table(request, "Backups", ["Run at", "File", "Size", "Status"], rows, backup=True)


@staff_only(ADMIN)
def sms_log(request):
    page, qstr = paginate(request, SMSLog.objects.select_related("customer"), 25)
    rows = [{"cells": [f"{timezone.localtime(s.sent_at):%b %d %I:%M %p}", s.customer.full_name, s.message_type, s.message[:90], s.status],
             "badge": 4} for s in page]
    return table(request, "SMS log", ["Sent", "Customer", "Type", "Message", "Status"], rows, page, qstr)
