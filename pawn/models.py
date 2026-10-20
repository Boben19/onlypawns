from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils import timezone

def private_storage():
    """Callable so migrations don't hard-code this machine's folder path."""
    return FileSystemStorage(location=str(settings.PRIVATE_ROOT))

ZERO = Decimal("0.00")


def money(**kw):
    return models.DecimalField(max_digits=12, decimal_places=2, default=kw.pop("default", ZERO), **kw)


# --------------------------------------------------------------------------------- accounts
class Role(models.Model):
    CLIENT, ADMIN, CASHIER, APPRAISER = "Client", "Administrator", "Cashier", "Appraiser"
    role_name = models.CharField(max_length=30, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.role_name


class User(AbstractUser):
    email = models.EmailField(unique=True, null=True, blank=True)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, null=True, related_name="users")
    google_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    pin_hash = models.CharField(max_length=160, blank=True)
    failed_pin_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, default="Active",
                              choices=[("Active", "Active"), ("Inactive", "Inactive"), ("Locked", "Locked")])
    last_login_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    @property
    def role_name(self):
        return self.role.role_name if self.role_id else ""

    @property
    def is_client(self):
        return self.role_name == Role.CLIENT

    @property
    def is_staff_user(self):
        return self.role_name in (Role.ADMIN, Role.CASHIER, Role.APPRAISER)

    @property
    def is_admin_role(self):
        return self.role_name == Role.ADMIN

    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())


class RecoveryKey(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_keys")
    key_hash = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)


class Branch(models.Model):
    branch_name = models.CharField(max_length=80, unique=True)
    address = models.CharField(max_length=200)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    contact_no = models.CharField(max_length=30, blank=True)
    open_time = models.TimeField()
    close_time = models.TimeField()
    open_counters = models.PositiveSmallIntegerField(default=1)
    queue_count = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=10, default="Active", choices=[("Active", "Active"), ("Closed", "Closed")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["branch_name"]

    def __str__(self):
        return self.branch_name


class Employee(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="employee")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="employees")
    first_name = models.CharField(max_length=60)
    last_name = models.CharField(max_length=60)
    position = models.CharField(max_length=60, blank=True)
    contact_no = models.CharField(max_length=30, blank=True)
    date_hired = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=10, default="Active", choices=[("Active", "Active"), ("Resigned", "Resigned")])
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def __str__(self):
        return self.full_name


class IDType(models.Model):
    id_type_name = models.CharField(max_length=60, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ID type"

    def __str__(self):
        return self.id_type_name


class Customer(models.Model):
    code = models.CharField(max_length=20, unique=True, blank=True)
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="customer")
    first_name = models.CharField(max_length=60)
    middle_name = models.CharField(max_length=60, blank=True)
    last_name = models.CharField(max_length=60)
    birthdate = models.DateField()
    sex = models.CharField(max_length=10, choices=[("Female", "Female"), ("Male", "Male")])
    civil_status = models.CharField(max_length=12, choices=[(x, x) for x in ("Single", "Married", "Widowed", "Separated")])
    nationality = models.CharField(max_length=40, default="Filipino")
    address = models.CharField(max_length=200)
    mobile_no = models.CharField(max_length=15, unique=True)
    email = models.EmailField(blank=True)
    source_of_income = models.CharField(max_length=100)
    status = models.CharField(max_length=24, default="Pending Verification",
                              choices=[(x, x) for x in ("Pending Verification", "Active", "Deactivated")])
    verified_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="verified_customers")
    verified_at = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-registered_at"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.code:
            self.code = f"PPS-C-{self.pk:06d}"
            super().save(update_fields=["code"])

    @property
    def full_name(self):
        return " ".join(p for p in (self.first_name, self.middle_name, self.last_name) if p)

    @property
    def age(self):
        t = timezone.localdate()
        b = self.birthdate
        return t.year - b.year - ((t.month, t.day) < (b.month, b.day))

    def __str__(self):
        return f"{self.code} - {self.full_name}"


class CustomerID(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="ids")
    id_type = models.ForeignKey(IDType, on_delete=models.PROTECT)
    id_number_enc = models.TextField()                     # encrypted at rest
    id_hash = models.CharField(max_length=64)               # for duplicate checks only
    front_image = models.FileField(storage=private_storage, upload_to="ids/", blank=True)
    back_image = models.FileField(storage=private_storage, upload_to="ids/", blank=True)
    selfie_image = models.FileField(storage=private_storage, upload_to="ids/", blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    verification_status = models.CharField(max_length=10, default="Pending",
                                           choices=[(x, x) for x in ("Pending", "Verified", "Rejected")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("id_type", "id_hash")]


# ------------------------------------------------------------------------------- currencies
class Currency(models.Model):
    code = models.CharField(max_length=3, unique=True)
    name = models.CharField(max_length=40)
    symbol = models.CharField(max_length=4)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "currencies"

    def __str__(self):
        return self.code

    def rate(self):
        if self.code == "PHP":
            return Decimal("1")
        r = self.rates.order_by("-effective_date", "-id").first()
        return r.rate_to_php if r else Decimal("1")


class ExchangeRate(models.Model):
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name="rates")
    rate_to_php = models.DecimalField(max_digits=12, decimal_places=6)
    effective_date = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)


# ----------------------------------------------------------------------------- transactions
class TransactionType(models.Model):
    APPRAISE, SANGLA, LOAN, PADALA, LOAD, REDEMPTION, RENEWAL = (
        "Appraise", "Sangla", "Loan", "Padala", "Load", "Redemption", "Renewal")
    type_name = models.CharField(max_length=20, unique=True)

    def __str__(self):
        return self.type_name


class Transaction(models.Model):
    transaction_no = models.CharField(max_length=24, unique=True)
    transaction_type = models.ForeignKey(TransactionType, on_delete=models.PROTECT)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="transactions")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="transactions")
    employee = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    exchange_rate_used = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("1"))
    original_amount = money()          # amount in the currency the customer used
    amount = money()                   # always PHP
    fee = money()
    status = models.CharField(max_length=10, default="Completed",
                              choices=[(x, x) for x in ("Pending", "Completed", "Cancelled", "Voided")])
    qr_token = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.transaction_no


class ItemCategory(models.Model):
    category_name = models.CharField(max_length=40, unique=True)
    monthly_interest_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.0300"),
                                                help_text="0.0300 means 3% per month")
    service_fee = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    grace_period_days = models.PositiveIntegerField(default=90)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "item categories"

    def __str__(self):
        return self.category_name


class Item(models.Model):
    STATUSES = ["Appraised", "In Vault", "Released", "For Auction", "Sold", "Returned to Inventory"]
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="items")
    category = models.ForeignKey(ItemCategory, on_delete=models.PROTECT, related_name="items")
    item_type = models.CharField(max_length=60)
    description = models.TextField()
    item_condition = models.CharField(max_length=10, default="Good",
                                      choices=[(x, x) for x in ("Excellent", "Good", "Fair", "Poor")])
    appraised_value = money()
    status = models.CharField(max_length=24, default="Appraised", choices=[(s, s) for s in STATUSES])
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.item_type} ({self.category})"


class ItemImage(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="items/")
    uploaded_at = models.DateTimeField(auto_now_add=True)


class Appraisal(models.Model):
    transaction = models.OneToOneField(Transaction, on_delete=models.PROTECT, related_name="appraisal")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="appraisals")
    appraiser = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="appraisals")
    appraised_value = money()
    suggested_loan = money()
    result = models.CharField(max_length=10, default="Pending",
                              choices=[(x, x) for x in ("Pending", "Accepted", "Declined", "Expired")])
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class PawnTicket(models.Model):
    OPEN = ("Active", "Renewed")
    ticket_no = models.CharField(max_length=24, unique=True)
    transaction = models.OneToOneField(Transaction, on_delete=models.PROTECT, related_name="pawn_ticket")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="tickets")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="tickets")
    loan_amount = money()
    interest_rate = models.DecimalField(max_digits=5, decimal_places=4)   # snapshot on loan date
    service_fee = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    loan_date = models.DateField()
    maturity_date = models.DateField()
    expiry_date = models.DateField()
    status = models.CharField(max_length=12, default="Active",
                              choices=[(x, x) for x in ("Active", "Renewed", "Redeemed", "Expired", "For Auction", "Auctioned")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_open(self):
        return self.status in self.OPEN

    @property
    def days_to_maturity(self):
        return (self.maturity_date - timezone.localdate()).days

    @property
    def tracker_stage(self):
        i = self.item.status
        if self.status == "Redeemed":
            return "Redeemed"
        if i in ("For Auction", "Sold", "Returned to Inventory"):
            return {"For Auction": "For Auction", "Sold": "Sold"}.get(i, "For Auction")
        if self.is_open and self.days_to_maturity <= 7:
            return "Due Soon"
        return "In Vault"

    def __str__(self):
        return self.ticket_no


class Renewal(models.Model):
    ticket = models.ForeignKey(PawnTicket, on_delete=models.PROTECT, related_name="renewals")
    transaction = models.OneToOneField(Transaction, on_delete=models.PROTECT, related_name="renewal")
    interest_paid = money()
    old_maturity_date = models.DateField()
    new_maturity_date = models.DateField()
    renewal_no = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class Redemption(models.Model):
    ticket = models.OneToOneField(PawnTicket, on_delete=models.PROTECT, related_name="redemption")
    transaction = models.OneToOneField(Transaction, on_delete=models.PROTECT, related_name="redemption")
    principal_paid = money()
    interest_paid = money()
    penalty_paid = money()
    total_paid = money()
    redeemed_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)


class Remittance(models.Model):
    transaction = models.OneToOneField(Transaction, on_delete=models.PROTECT, related_name="remittance")
    reference_no = models.CharField(max_length=20, unique=True)
    receiver_name = models.CharField(max_length=100)
    receiver_mobile = models.CharField(max_length=15)
    receiver_branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="remittances")
    claim_status = models.CharField(max_length=10, default="Unclaimed",
                                    choices=[(x, x) for x in ("Unclaimed", "Claimed", "Cancelled")])
    claimed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MobileLoad(models.Model):
    transaction = models.OneToOneField(Transaction, on_delete=models.PROTECT, related_name="mobile_load")
    network = models.CharField(max_length=20)
    mobile_no = models.CharField(max_length=15)
    load_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, default="Success", choices=[("Success", "Success"), ("Failed", "Failed")])
    created_at = models.DateTimeField(auto_now_add=True)


class Auction(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="auctions")
    ticket = models.OneToOneField(PawnTicket, on_delete=models.PROTECT, related_name="auction")
    starting_price = money()
    auction_date = models.DateField()
    status = models.CharField(max_length=24, default="Scheduled",
                              choices=[(x, x) for x in ("Scheduled", "Sold", "Returned to Inventory")])
    created_by = models.ForeignKey(Employee, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class AuctionSale(models.Model):
    auction = models.OneToOneField(Auction, on_delete=models.PROTECT, related_name="sale")
    winning_bidder_name = models.CharField(max_length=100)
    bidder_contact = models.CharField(max_length=30, blank=True)
    sale_amount = money()
    sold_at = models.DateTimeField(default=timezone.now)


class CashLedger(models.Model):
    CATEGORIES = ["Loan Release", "Redemption", "Renewal", "Padala", "Load", "Auction Sale", "Refund", "Expense"]
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="ledger")
    transaction = models.ForeignKey(Transaction, on_delete=models.PROTECT, null=True, blank=True, related_name="ledger")
    direction = models.CharField(max_length=3, choices=[("IN", "IN"), ("OUT", "OUT")])
    category = models.CharField(max_length=20, choices=[(c, c) for c in CATEGORIES])
    description = models.CharField(max_length=200, blank=True)
    amount = money()
    recorded_by = models.ForeignKey(Employee, on_delete=models.PROTECT)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-recorded_at", "-id"]


class Receipt(models.Model):
    transaction = models.OneToOneField(Transaction, on_delete=models.PROTECT, related_name="receipt")
    receipt_no = models.CharField(max_length=24, unique=True)
    receipt_type = models.CharField(max_length=20, choices=[(x, x) for x in ("Pawn Ticket", "Redemption", "Renewal", "Padala", "Load")])
    file = models.FileField(upload_to="receipts/")
    issued_at = models.DateTimeField(default=timezone.now)


class SMSLog(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="sms_logs")
    ticket = models.ForeignKey(PawnTicket, on_delete=models.SET_NULL, null=True, blank=True, related_name="sms_logs")
    message_type = models.CharField(max_length=10, choices=[(x, x) for x in ("Reminder", "Due Today", "Expired", "OTP", "Receipt")])
    message = models.TextField()
    status = models.CharField(max_length=6, default="Sent", choices=[("Sent", "Sent"), ("Failed", "Failed")])
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-sent_at"]


# ------------------------------------------------------------------------------- service
class ChatSession(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="chat_sessions")
    started_at = models.DateTimeField(default=timezone.now)
    escalated = models.BooleanField(default=False)


class ChatMessage(models.Model):
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    sender = models.CharField(max_length=10, choices=[("Customer", "Customer"), ("Bot Star", "Bot Star")])
    message = models.TextField()
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["sent_at", "id"]


class SupportTicket(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="support_tickets")
    assigned_to = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="support_tickets")
    session = models.ForeignKey(ChatSession, on_delete=models.SET_NULL, null=True, blank=True)
    subject = models.CharField(max_length=120)
    category = models.CharField(max_length=12, default="Inquiry", choices=[(x, x) for x in ("Account", "Transaction", "Complaint", "Inquiry")])
    priority = models.CharField(max_length=8, default="Normal", choices=[(x, x) for x in ("Low", "Normal", "High")])
    status = models.CharField(max_length=12, default="Open", choices=[(x, x) for x in ("Open", "In Progress", "Resolved")])
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class SupportMessage(models.Model):
    support_ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    sender_user = models.ForeignKey(User, on_delete=models.PROTECT)
    message = models.TextField()
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["sent_at", "id"]


class WrapSummary(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="wraps")
    period_label = models.CharField(max_length=10)
    total_pawned = money()
    total_redeemed = money()
    items_pawned = models.PositiveIntegerField(default=0)
    items_redeemed = models.PositiveIntegerField(default=0)
    on_time_rate = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    top_category = models.ForeignKey(ItemCategory, on_delete=models.SET_NULL, null=True, blank=True)
    star_level = models.CharField(max_length=10, default="Rookie", choices=[(x, x) for x in ("Rookie", "Regular", "VIP Star")])
    generated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [("customer", "period_label")]


# -------------------------------------------------------------------------------- system
class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    action = models.CharField(max_length=10)
    table_name = models.CharField(max_length=40)
    record_id = models.PositiveBigIntegerField(null=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    logged_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-logged_at", "-id"]


class BackupLog(models.Model):
    file_path = models.CharField(max_length=300)
    size_mb = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=8, choices=[("Success", "Success"), ("Failed", "Failed")])
    run_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-run_at"]
