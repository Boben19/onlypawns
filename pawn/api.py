"""REST API (Django REST Framework). The web screens and the PWA both use it for chat, due amounts and live queue counts."""
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response

from . import services as S
from .models import Branch, ChatMessage, ChatSession, Customer, ItemCategory, PawnTicket, Role, Transaction


class IsStaffUser(permissions.BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u.is_authenticated and u.is_staff_user)


class IsAdminRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user.is_authenticated and request.user.is_admin_role)


class AdminWriteAuthRead(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user.is_authenticated and request.user.is_admin_role)


class CustomerSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Customer
        fields = ["id", "code", "full_name", "first_name", "middle_name", "last_name", "birthdate", "sex", "civil_status",
                  "nationality", "address", "mobile_no", "email", "source_of_income", "status", "registered_at"]
        read_only_fields = ["code", "status", "registered_at"]


class TicketSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    item = serializers.CharField(source="item.item_type", read_only=True)

    class Meta:
        model = PawnTicket
        fields = ["id", "ticket_no", "customer", "customer_name", "item", "loan_amount", "interest_rate", "service_fee",
                  "loan_date", "maturity_date", "expiry_date", "status"]


class TransactionSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source="transaction_type.type_name", read_only=True)

    class Meta:
        model = Transaction
        fields = ["id", "transaction_no", "type", "customer", "branch", "amount", "fee", "status", "created_at"]


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "branch_name", "address", "latitude", "longitude", "contact_no", "open_time", "close_time",
                  "open_counters", "queue_count", "status"]
        read_only_fields = ["queue_count"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemCategory
        fields = ["id", "category_name", "monthly_interest_rate", "service_fee", "grace_period_days"]


class CustomerViewSet(viewsets.ModelViewSet):
    """Staff only. DELETE deactivates the customer, it never erases the record."""
    serializer_class = CustomerSerializer
    permission_classes = [IsStaffUser]
    queryset = Customer.objects.all()

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.query_params.get("q")
        return qs.filter(last_name__icontains=q) | qs.filter(code__icontains=q) if q else qs

    def perform_create(self, serializer):
        c = serializer.save()
        S.audit(self.request.user, "CREATE", "customers", c.pk, None, {"via": "api"})

    def destroy(self, request, *a, **kw):
        c = self.get_object()
        c.status = "Deactivated"
        c.save(update_fields=["status"])
        S.audit(request.user, "UPDATE", "customers", c.pk, None, {"status": "Deactivated"})
        return Response(status=status.HTTP_204_NO_CONTENT)


class TicketViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TicketSerializer

    def get_queryset(self):
        u = self.request.user
        qs = PawnTicket.objects.select_related("customer", "item")
        if u.is_staff_user:
            return qs
        if u.is_client and hasattr(u, "customer"):
            return qs.filter(customer=u.customer)
        return qs.none()

    @action(detail=True, methods=["get"])
    def due(self, request, pk=None):
        """GET /api/tickets/<id>/due/?date=2026-12-31 - what it costs to redeem or renew on that day."""
        t = self.get_object()
        if not t.is_open:
            return Response({"detail": f"Ticket is {t.status}."}, status=400)
        on = parse_date(request.query_params.get("date", "")) or None
        d = S.compute_due(t, on)
        return Response({k: (str(v) if not isinstance(v, (int, bool)) else v) for k, v in d.items()})


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TransactionSerializer

    def get_queryset(self):
        u = self.request.user
        qs = Transaction.objects.select_related("transaction_type")
        if u.is_staff_user:
            return qs
        if u.is_client and hasattr(u, "customer"):
            return qs.filter(customer=u.customer)
        return qs.none()


class BranchViewSet(viewsets.ModelViewSet):
    serializer_class = BranchSerializer
    permission_classes = [AdminWriteAuthRead]
    queryset = Branch.objects.all()
    pagination_class = None

    @action(detail=True, methods=["post"], permission_classes=[IsStaffUser])
    def queue(self, request, pk=None):
        """POST {"delta": 1} or {"delta": -1} to move the waiting line."""
        b = self.get_object()
        b.queue_count = max(0, b.queue_count + (1 if int(request.data.get("delta", 1)) > 0 else -1))
        b.save(update_fields=["queue_count"])
        return Response({"queue_count": b.queue_count})


class CategoryViewSet(viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    permission_classes = [AdminWriteAuthRead]
    queryset = ItemCategory.objects.all()
    pagination_class = None


@api_view(["POST"])
def bot(request):
    """Bot Star. Body: {"message": "...", "session": <id or null>}"""
    u = request.user
    if not (u.is_authenticated and u.is_client and hasattr(u, "customer")):
        return Response({"detail": "Log in to chat."}, status=403)
    text = str(request.data.get("message", "")).strip()[:500]
    if not text:
        return Response({"detail": "Say something first."}, status=400)
    session = ChatSession.objects.filter(pk=request.data.get("session"), customer=u.customer).first() \
        or ChatSession.objects.create(customer=u.customer)
    ChatMessage.objects.create(session=session, sender="Customer", message=text)
    reply, human = S.bot_reply(u.customer, text)
    ChatMessage.objects.create(session=session, sender="Bot Star", message=reply)
    ticket = S.escalate_chat(session, u.customer) if human and not session.escalated else None
    return Response({"reply": reply, "session": session.pk, "escalated": session.escalated,
                     "support_ticket": ticket.pk if ticket else None})
