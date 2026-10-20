from decimal import Decimal

from django import template

from pawn.models import Currency

register = template.Library()


@register.filter
def peso(value):
    try:
        return f"₱{Decimal(value):,.2f}"
    except Exception:
        return value


@register.simple_tag(takes_context=True)
def amt(context, value):
    """Shows a PHP amount in the currency the client picked (display only, the record stays in PHP)."""
    code = context["request"].session.get("cur", "PHP")
    cur = Currency.objects.filter(code=code).first()
    if not cur or code == "PHP":
        return f"₱{Decimal(value):,.2f}"
    return f"{cur.symbol}{Decimal(value) / cur.rate():,.2f}"


@register.filter
def badge(status):
    s = str(status).lower()
    if s in ("active", "completed", "verified", "success", "redeemed", "resolved", "claimed", "sold"):
        return "ok"
    if s in ("expired", "for auction", "rejected", "failed", "voided", "deactivated", "locked", "high", "cancelled"):
        return "bad"
    if s in ("pending", "pending verification", "open", "in progress", "renewed", "scheduled", "unclaimed", "due soon"):
        return "warn"
    return "mute"


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except Exception:
        return None
