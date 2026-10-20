import secrets
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.hashers import check_password, make_password
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from datetime import timedelta

from . import forms as F
from . import services as S
from .models import Branch, ChatSession, Currency, Customer, PawnTicket, RecoveryKey, Role, SupportMessage, SupportTicket, Transaction, User

KEY_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def client_only(view):
    @wraps(view)
    def inner(request, *a, **kw):
        u = request.user
        if not u.is_authenticated or not u.is_client or not hasattr(u, "customer"):
            return redirect("client_login")
        request.cust = u.customer
        return view(request, *a, **kw)
    return inner


def new_recovery_key(user):
    key = "-".join("".join(secrets.choice(KEY_CHARS) for _ in range(4)) for _ in range(5))
    RecoveryKey.objects.create(user=user, key_hash=make_password(key))
    return key


def google_identity(request):
    """Real Google token when a client id is configured, plain email for the classroom demo otherwise."""
    if settings.GOOGLE_CLIENT_ID:
        return S.verify_google_token(request.POST.get("credential", ""))
    email = request.POST.get("email", "").strip().lower()
    return {"email": email, "sub": f"demo:{email}", "name": ""} if "@" in email else None


def valid_pin(pin, confirm=None):
    if not (pin.isdigit() and len(pin) == 6):
        return "Your PIN must be exactly 6 digits."
    if confirm is not None and pin != confirm:
        return "The two PINs don't match."
    return ""


# --------------------------------------------------------------------------- sign up / in
def welcome(request):
    if request.user.is_authenticated and request.user.is_client:
        return redirect("client_home")
    return render(request, "app/welcome.html")


def register(request):
    form = F.RegistrationForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        c = form.save()
        S.audit(None, "CREATE", "customers", c.pk, None, {"code": c.code, "source": "app"})
        request.session["signup_customer"] = c.pk
        return redirect("client_signup")
    return render(request, "app/register.html", {"form": form})


def signup(request):
    cust = Customer.objects.filter(pk=request.session.get("signup_customer"), user__isnull=True).first()
    error = ""
    if request.method == "POST":
        if not cust:
            cust = Customer.objects.filter(code__iexact=request.POST.get("code", "").strip(), user__isnull=True,
                                           mobile_no=request.POST.get("mobile", "").strip()).first()
        ident, pin = google_identity(request), request.POST.get("pin", "")
        error = valid_pin(pin, request.POST.get("pin2", ""))
        if not cust:
            error = error or "We couldn't find a registration with that customer ID and mobile number."
        elif not ident:
            error = error or "Google sign-in didn't go through. Try again."
        elif User.objects.filter(email=ident["email"]).exists():
            error = error or "That Google account is already linked to a customer."
        if not error:
            role = Role.objects.get(role_name=Role.CLIENT)
            user = User(username=cust.code, email=ident["email"], google_id=ident["sub"], role=role,
                        pin_hash=make_password(pin), first_name=cust.first_name, last_name=cust.last_name)
            user.set_unusable_password()
            user.save()
            cust.user = user
            cust.email = cust.email or ident["email"]
            cust.save()
            request.session.pop("signup_customer", None)
            request.session["new_key"] = new_recovery_key(user)
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("client_key")
    return render(request, "app/signup.html", {"cust": cust, "error": error, "demo": not settings.GOOGLE_CLIENT_ID})


@client_only
def recovery_key_page(request):
    key = request.session.pop("new_key", None)
    if not key:
        return redirect("client_home")
    return render(request, "app/key.html", {"key": key})


def client_login(request):
    if request.user.is_authenticated and request.user.is_client:
        return redirect("client_home")
    if request.GET.get("switch"):
        request.session.pop("pending_login", None)
        resp = redirect("client_login")
        resp.delete_cookie("op_known")
        return resp
    known = request.get_signed_cookie("op_known", default=None, salt="op-device")
    uid = request.session.get("pending_login") or known
    user = User.objects.filter(pk=uid, role__role_name=Role.CLIENT).first() if uid else None
    error = ""
    if request.method == "POST":
        if request.POST.get("step") == "google" or not user:
            ident = google_identity(request)
            user = User.objects.filter(google_id=ident["sub"], role__role_name=Role.CLIENT).first() if ident else None
            if not user and ident:
                user = User.objects.filter(email=ident["email"], role__role_name=Role.CLIENT).first()
            if user:
                request.session["pending_login"] = user.pk
            else:
                error = "We couldn't match that Google account. If you haven't registered yet, tap Register."
        else:
            pin = request.POST.get("pin", "")
            if user.is_locked():
                mins = max(1, int((user.locked_until - timezone.now()).total_seconds() // 60) + 1)
                error = f"Too many wrong PINs. Try again in about {mins} minutes, or reset with your recovery key."
            elif user.status == "Inactive" or not user.is_active:
                error = "This account is deactivated. Please visit a branch."
            elif check_password(pin, user.pin_hash):
                user.failed_pin_attempts, user.locked_until, user.last_login_at = 0, None, timezone.now()
                user.save(update_fields=["failed_pin_attempts", "locked_until", "last_login_at"])
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                request.session.pop("pending_login", None)
                S.audit(user, "LOGIN", "users", user.pk)
                resp = redirect("client_home")
                resp.set_signed_cookie("op_known", user.pk, salt="op-device", max_age=60 * 60 * 24 * 90, httponly=True, samesite="Lax")
                return resp
            else:
                user.failed_pin_attempts += 1
                left = settings.PIN_MAX_ATTEMPTS - user.failed_pin_attempts
                if left <= 0:
                    user.locked_until = timezone.now() + timedelta(minutes=settings.PIN_LOCK_MINUTES)
                    user.failed_pin_attempts = 0
                    error = f"That was the fifth wrong PIN. The app is locked for {settings.PIN_LOCK_MINUTES} minutes."
                else:
                    error = f"Wrong PIN. {left} {'try' if left == 1 else 'tries'} left."
                user.save(update_fields=["failed_pin_attempts", "locked_until"])
    return render(request, "app/login.html", {"pin_step": bool(user),
                                              "user_first": user.first_name if user else "", "error": error,
                                              "demo": not settings.GOOGLE_CLIENT_ID})


def recover(request):
    error = ""
    if request.method == "POST":
        ident = google_identity(request)
        user = User.objects.filter(email=ident["email"], role__role_name=Role.CLIENT).first() if ident else None
        key = request.POST.get("key", "").strip().upper().replace(" ", "")
        pin, err = request.POST.get("pin", ""), valid_pin(request.POST.get("pin", ""), request.POST.get("pin2", ""))
        match = None
        if user and not err:
            for rk in user.recovery_keys.filter(used_at__isnull=True):
                if check_password(key, rk.key_hash):
                    match = rk
                    break
        if err or not match:
            error = err or "That Google account and recovery key don't match."
        else:
            match.used_at = timezone.now()
            match.save(update_fields=["used_at"])
            user.pin_hash, user.failed_pin_attempts, user.locked_until = make_password(pin), 0, None
            user.save(update_fields=["pin_hash", "failed_pin_attempts", "locked_until"])
            S.audit(user, "UPDATE", "users", user.pk, None, {"pin": "reset"})
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            request.session["new_key"] = new_recovery_key(user)
            return redirect("client_key")
    return render(request, "app/recover.html", {"error": error, "demo": not settings.GOOGLE_CLIENT_ID})


def client_logout(request):
    logout(request)
    return redirect("client_welcome")


# ------------------------------------------------------------------------------- the app
@client_only
def home(request):
    c = request.cust
    open_t = c.tickets.filter(status__in=PawnTicket.OPEN).select_related("item")
    cards = [(t, S.compute_due(t)) for t in open_t]
    alerts = []
    for t, d in cards:
        if d["overdue"]:
            alerts.append((f"{t.ticket_no} is past maturity. {max(d['days_to_expiry'], 0)} days left before auction.", "bad"))
        elif d["days_to_maturity"] <= 7:
            when = "today" if d["days_to_maturity"] == 0 else f"in {d['days_to_maturity']} day{'s' if d['days_to_maturity'] != 1 else ''}"
            alerts.append((f"{t.ticket_no} matures {when}.", "warn"))
    expired = c.tickets.filter(status__in=("Expired", "For Auction"))
    for t in expired:
        alerts.append((f"{t.ticket_no} has expired and the item is up for auction.", "bad"))
    return render(request, "app/home.html", {"cards": cards, "alerts": alerts, "recent": c.transactions.select_related("transaction_type")[:4]})


@client_only
def history(request):
    qs = request.cust.transactions.select_related("transaction_type")
    kind, status = request.GET.get("type", ""), request.GET.get("status", "")
    if kind:
        qs = qs.filter(transaction_type__type_name=kind)
    if status:
        qs = qs.filter(status=status)
    types = ["Appraise", "Sangla", "Loan", "Padala", "Load", "Redemption", "Renewal"]
    return render(request, "app/history.html", {"txns": qs[:100], "types": types, "kind": kind, "status": status})


@client_only
def ticket(request, pk):
    t = get_object_or_404(PawnTicket.objects.select_related("item", "item__category", "transaction"), pk=pk, customer=request.cust)
    stages = ["Appraised", "In Vault", "Due Soon", "Redeemed"] if t.status in ("Active", "Renewed", "Redeemed") else \
        ["Appraised", "In Vault", "For Auction", "Sold"]
    current = t.tracker_stage
    idx = stages.index(current) if current in stages else 1
    return render(request, "app/ticket.html", {
        "t": t, "due": S.compute_due(t) if t.is_open else None, "stages": stages, "idx": idx, "current": current,
        "renewals": t.renewals.all(), "images": t.item.images.all()})


@client_only
def qr_image(request, txn_no):
    t = get_object_or_404(Transaction, transaction_no=txn_no, customer=request.cust)
    return HttpResponse(S.qr_png(t.qr_token), content_type="image/png")


@client_only
def wrap(request):
    period = request.GET.get("period") or str(timezone.localdate().year)
    if not (period.isdigit() and len(period) == 4):
        period = str(timezone.localdate().year)
    w = S.compute_wrap(request.cust, period)
    return render(request, "app/wrap.html", {"w": w, "period": period})


@client_only
def branches(request):
    bs = Branch.objects.filter(status="Active")
    data = [{"name": b.branch_name, "address": b.address, "lat": float(b.latitude), "lng": float(b.longitude)} for b in bs]
    return render(request, "app/branches.html", {"branches": bs, "branch_json": data})


@client_only
def chat(request):
    s = ChatSession.objects.filter(customer=request.cust).order_by("-started_at").first()
    if not s or request.GET.get("new"):
        s = ChatSession.objects.create(customer=request.cust)
    return render(request, "app/chat.html", {"session": s, "history": s.messages.all()})


@client_only
def support_list(request):
    return render(request, "app/support_list.html", {"items": request.cust.support_tickets.all()})


@client_only
def support_new(request):
    form = F.SupportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        st = form.save(commit=False)
        st.customer = request.cust
        st.save()
        SupportMessage.objects.create(support_ticket=st, sender_user=request.user, message=form.cleaned_data["message"])
        messages.success(request, "Sent. Pawn Patrol will reply here.")
        return redirect("client_support_detail", pk=st.pk)
    return render(request, "app/support_new.html", {"form": form})


@client_only
def support_detail(request, pk):
    st = get_object_or_404(SupportTicket, pk=pk, customer=request.cust)
    form = F.ReplyForm(request.POST or None)
    form.fields.pop("status")
    if request.method == "POST" and form.is_valid():
        SupportMessage.objects.create(support_ticket=st, sender_user=request.user, message=form.cleaned_data["message"])
        if st.status == "Resolved":
            st.status, st.resolved_at = "Open", None
            st.save()
        return redirect("client_support_detail", pk=st.pk)
    return render(request, "app/support_detail.html", {"s": st, "form": form})


@client_only
def profile(request):
    c = request.cust
    return render(request, "app/profile.html", {"c": c, "currencies": Currency.objects.all(), "cur": request.session.get("cur", "PHP")})


@client_only
@require_POST
def set_currency(request):
    code = request.POST.get("cur", "PHP")
    if Currency.objects.filter(code=code).exists():
        request.session["cur"] = code
    return redirect(request.POST.get("next") or "client_profile")
