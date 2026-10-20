import re
import uuid
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.core.files.base import ContentFile
from django.db import transaction as db_transaction
from django.utils import timezone

from . import services
from .models import (
    Auction, Branch, CashLedger, Currency, Customer, CustomerID, Employee, ExchangeRate, IDType, ItemCategory,
    Role, SupportTicket, TransactionType, User,
)

MOBILE_RE = re.compile(r"^(?:\+63|0)9\d{9}$")
NETWORKS = [("Globe", "Globe"), ("Smart", "Smart"), ("DITO", "DITO"), ("TNT", "TNT"), ("TM", "TM")]
LOAD_AMOUNTS = [(Decimal(x), f"PHP {x}") for x in (20, 50, 100, 200, 300, 500)]


def normalize_mobile(value):
    v = re.sub(r"[\s-]", "", value or "")
    if not MOBILE_RE.match(v):
        raise forms.ValidationError("Enter a Philippine mobile number like 09171234567.")
    return "0" + v[3:] if v.startswith("+63") else v


class StyledMixin:
    """Adds the css classes and date pickers once so every form looks the same."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, (forms.CheckboxInput, forms.RadioSelect)):
                continue
            w.attrs.setdefault("class", "in")
            if isinstance(w, forms.DateInput):
                w.input_type = "date"
                w.format = "%Y-%m-%d"
            if isinstance(f, forms.DateField):
                f.widget = forms.DateInput(attrs={"class": "in", "type": "date"}, format="%Y-%m-%d")


class Styled(StyledMixin, forms.Form):
    pass


class StyledModelForm(StyledMixin, forms.ModelForm):
    pass


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "image/*"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data if d]
        return [single(data, initial)] if data else []


def store_private(upload):
    """ID photos are encrypted before they touch the disk."""
    return ContentFile(services.encrypt_bytes(upload.read()), name=f"{uuid.uuid4().hex}.enc")


# ---------------------------------------------------------------------------------- customers
class RegistrationForm(StyledModelForm):
    id_type = forms.ModelChoiceField(IDType.objects.all(), label="Valid ID")
    id_number = forms.CharField(max_length=40, label="ID number")
    id_expiry = forms.DateField(required=False, label="ID expiry date")
    id_front = forms.ImageField(label="ID photo (front)")
    id_back = forms.ImageField(required=False, label="ID photo (back, if it has one)")
    id_selfie = forms.ImageField(required=False, label="Selfie holding the ID")

    class Meta:
        model = Customer
        fields = ["first_name", "middle_name", "last_name", "birthdate", "sex", "civil_status", "nationality",
                  "address", "mobile_no", "email", "source_of_income"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["id_type"].queryset = IDType.objects.all()
        self.fields["birthdate"].widget = forms.DateInput(attrs={"class": "in", "type": "date"}, format="%Y-%m-%d")

    def clean_birthdate(self):
        b = self.cleaned_data["birthdate"]
        t = timezone.localdate()
        if t.year - b.year - ((t.month, t.day) < (b.month, b.day)) < 18:
            raise forms.ValidationError("Customers must be 18 or older.")
        return b

    def clean_mobile_no(self):
        m = normalize_mobile(self.cleaned_data["mobile_no"])
        if Customer.objects.filter(mobile_no=m).exists():
            raise forms.ValidationError("This mobile number is already registered.")
        return m

    def clean(self):
        data = super().clean()
        t, n = data.get("id_type"), data.get("id_number")
        if t and n and CustomerID.objects.filter(id_type=t, id_hash=services.id_hash(t.pk, n)).exists():
            self.add_error("id_number", "This ID number was already used by another customer.")
        return data

    @db_transaction.atomic
    def save(self, commit=True):
        customer = super().save()
        d = self.cleaned_data
        cid = CustomerID(customer=customer, id_type=d["id_type"], id_number_enc=services.encrypt_text(d["id_number"]),
                         id_hash=services.id_hash(d["id_type"].pk, d["id_number"]), expiry_date=d.get("id_expiry"))
        cid.front_image.save("front.enc", store_private(d["id_front"]), save=False)
        if d.get("id_back"):
            cid.back_image.save("back.enc", store_private(d["id_back"]), save=False)
        if d.get("id_selfie"):
            cid.selfie_image.save("selfie.enc", store_private(d["id_selfie"]), save=False)
        cid.save()
        return customer


class CustomerEditForm(StyledModelForm):
    class Meta:
        model = Customer
        fields = ["first_name", "middle_name", "last_name", "birthdate", "sex", "civil_status", "nationality",
                  "address", "mobile_no", "email", "source_of_income"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["birthdate"].widget = forms.DateInput(attrs={"class": "in", "type": "date"}, format="%Y-%m-%d")

    def clean_mobile_no(self):
        m = normalize_mobile(self.cleaned_data["mobile_no"])
        if Customer.objects.filter(mobile_no=m).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This mobile number belongs to another customer.")
        return m


# -------------------------------------------------------------------------- appraisal / pawn
def active_customers():
    return Customer.objects.filter(status="Active").order_by("last_name", "first_name")


class AppraisalForm(Styled):
    customer = forms.ModelChoiceField(Customer.objects.none())
    category = forms.ModelChoiceField(ItemCategory.objects.all())
    item_type = forms.CharField(max_length=60, label="Item", help_text="Example: 18k gold necklace")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), help_text="Brand, model, serial, karat, weight")
    item_condition = forms.ChoiceField(choices=[(x, x) for x in ("Excellent", "Good", "Fair", "Poor")], initial="Good")
    appraised_value = forms.DecimalField(min_value=Decimal("1"), max_digits=12, decimal_places=2)
    suggested_loan = forms.DecimalField(required=False, min_value=Decimal("1"), max_digits=12, decimal_places=2,
                                        help_text="Leave blank to use 60% of the appraised value")
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    photos = MultipleFileField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = active_customers()
        self.fields["category"].queryset = ItemCategory.objects.all()


class TicketForm(Styled):
    kind = forms.ChoiceField(choices=[("Sangla", "Sangla (item as collateral)"), ("Loan", "Loan (non-jewelry items)")], label="Type")
    loan_amount = forms.DecimalField(min_value=Decimal("1"), max_digits=12, decimal_places=2)


class PaymentForm(Styled):
    tendered = forms.DecimalField(min_value=Decimal("0"), max_digits=12, decimal_places=2, label="Cash received")


class PadalaForm(Styled):
    customer = forms.ModelChoiceField(Customer.objects.none(), label="Sender")
    currency = forms.ModelChoiceField(Currency.objects.all(), initial=lambda: Currency.objects.filter(code="PHP").first())
    amount = forms.DecimalField(min_value=Decimal("1"), max_digits=12, decimal_places=2)
    receiver_name = forms.CharField(max_length=100)
    receiver_mobile = forms.CharField(max_length=15)
    receiver_branch = forms.ModelChoiceField(Branch.objects.none(), label="Claim at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = active_customers()
        self.fields["currency"].queryset = Currency.objects.all()
        self.fields["receiver_branch"].queryset = Branch.objects.filter(status="Active")

    def clean_receiver_mobile(self):
        return normalize_mobile(self.cleaned_data["receiver_mobile"])


class ClaimForm(Styled):
    reference_no = forms.CharField(max_length=20)
    receiver_mobile = forms.CharField(max_length=15, label="Receiver's mobile number")

    def clean_receiver_mobile(self):
        return normalize_mobile(self.cleaned_data["receiver_mobile"])


class LoadForm(Styled):
    customer = forms.ModelChoiceField(Customer.objects.none())
    network = forms.ChoiceField(choices=NETWORKS)
    mobile_no = forms.CharField(max_length=15, label="Number to load")
    load_amount = forms.TypedChoiceField(choices=LOAD_AMOUNTS, coerce=Decimal, label="Amount")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = active_customers()
        self.fields["load_amount"].choices = [(str(a), l) for a, l in LOAD_AMOUNTS]

    def clean_mobile_no(self):
        return normalize_mobile(self.cleaned_data["mobile_no"])


class AuctionForm(Styled):
    starting_price = forms.DecimalField(min_value=Decimal("1"), max_digits=12, decimal_places=2)
    auction_date = forms.DateField(initial=lambda: timezone.localdate() + timedelta(days=7))


class AuctionResultForm(Styled):
    outcome = forms.ChoiceField(choices=[("Sold", "Sold"), ("Returned", "Nobody bought it (return to inventory)")],
                                widget=forms.RadioSelect, initial="Sold")
    winning_bidder_name = forms.CharField(max_length=100, required=False)
    bidder_contact = forms.CharField(max_length=30, required=False)
    sale_amount = forms.DecimalField(required=False, min_value=Decimal("1"), max_digits=12, decimal_places=2)


class ExpenseForm(Styled):
    category = forms.ChoiceField(choices=[("Expense", "Branch expense"), ("Refund", "Refund")])
    description = forms.CharField(max_length=200)
    amount = forms.DecimalField(min_value=Decimal("1"), max_digits=12, decimal_places=2)


# ---------------------------------------------------------------------------- admin screens
class BranchForm(StyledModelForm):
    class Meta:
        model = Branch
        exclude = ["queue_count"]
        widgets = {"open_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
                   "close_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}


class CategoryForm(StyledModelForm):
    class Meta:
        model = ItemCategory
        fields = ["category_name", "monthly_interest_rate", "service_fee", "grace_period_days"]


class CurrencyForm(StyledModelForm):
    class Meta:
        model = Currency
        fields = ["code", "name", "symbol"]


class RateForm(StyledModelForm):
    class Meta:
        model = ExchangeRate
        fields = ["currency", "rate_to_php", "effective_date"]


class IDTypeForm(StyledModelForm):
    class Meta:
        model = IDType
        fields = ["id_type_name"]


class EmployeeForm(Styled):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput(render_value=False), required=False,
                               help_text="At least 8 characters. Leave blank to keep the current password when editing.")
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60)
    role = forms.ModelChoiceField(Role.objects.none())
    branch = forms.ModelChoiceField(Branch.objects.all())
    position = forms.CharField(max_length=60, required=False)
    contact_no = forms.CharField(max_length=30, required=False)
    date_hired = forms.DateField(initial=timezone.localdate)
    status = forms.ChoiceField(choices=[("Active", "Active"), ("Resigned", "Resigned")])

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        if instance and not args and "initial" not in kwargs:
            kwargs["initial"] = dict(username=instance.user.username, first_name=instance.first_name,
                                     last_name=instance.last_name, role=instance.user.role, branch=instance.branch,
                                     position=instance.position, contact_no=instance.contact_no,
                                     date_hired=instance.date_hired, status=instance.status)
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = Role.objects.exclude(role_name=Role.CLIENT)

    def clean_username(self):
        u = self.cleaned_data["username"]
        qs = User.objects.filter(username=u)
        if self.instance:
            qs = qs.exclude(pk=self.instance.user_id)
        if qs.exists():
            raise forms.ValidationError("That username is taken.")
        return u

    def clean_password(self):
        p = self.cleaned_data["password"]
        if not self.instance and len(p) < 8:
            raise forms.ValidationError("Set a password with at least 8 characters.")
        if p and len(p) < 8:
            raise forms.ValidationError("Use at least 8 characters.")
        return p

    @db_transaction.atomic
    def save(self):
        d = self.cleaned_data
        if self.instance:
            emp, user = self.instance, self.instance.user
        else:
            user, emp = User(), Employee()
        user.username, user.role = d["username"], d["role"]
        user.first_name, user.last_name = d["first_name"], d["last_name"]
        user.is_active = d["status"] == "Active"
        user.is_staff = d["role"].role_name == Role.ADMIN
        user.status = "Active" if user.is_active else "Inactive"
        if d["password"]:
            user.set_password(d["password"])
        user.save()
        emp.user, emp.branch = user, d["branch"]
        for f in ("first_name", "last_name", "position", "contact_no", "date_hired", "status"):
            setattr(emp, f, d[f])
        emp.save()
        return emp


class ReplyForm(Styled):
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    status = forms.ChoiceField(choices=[("Open", "Open"), ("In Progress", "In Progress"), ("Resolved", "Resolved")], required=False)


class SupportForm(StyledModelForm):
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))

    class Meta:
        model = SupportTicket
        fields = ["subject", "category"]
