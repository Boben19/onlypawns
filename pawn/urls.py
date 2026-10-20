from django.http import FileResponse, HttpResponse
from django.shortcuts import redirect, render
from django.urls import include, path
from django.conf import settings
from rest_framework.routers import DefaultRouter

from . import api, geo, views_client as C, views_staff as V

router = DefaultRouter()
router.register("customers", api.CustomerViewSet, basename="api-customers")
router.register("tickets", api.TicketViewSet, basename="api-tickets")
router.register("transactions", api.TransactionViewSet, basename="api-transactions")
router.register("branches", api.BranchViewSet, basename="api-branches")
router.register("categories", api.CategoryViewSet, basename="api-categories")


def service_worker(request):
    p = settings.BASE_DIR / "pawn" / "static" / "pawn" / "js" / "sw.js"
    return HttpResponse(p.read_text(), content_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


def manifest(request):
    p = settings.BASE_DIR / "pawn" / "static" / "pawn" / "manifest.webmanifest"
    return HttpResponse(p.read_text(), content_type="application/manifest+json")


def offline(request):
    return render(request, "app/offline.html")


staff = [
    path("login/", V.staff_login, name="staff_login"),
    path("logout/", V.staff_logout, name="staff_logout"),
    path("", V.dashboard, name="staff_dashboard"),
    path("customers/", V.customers, name="customers"),
    path("customers/new/", V.customer_new, name="customer_new"),
    path("customers/<int:pk>/", V.customer_detail, name="customer_detail"),
    path("customers/<int:pk>/edit/", V.customer_edit, name="customer_edit"),
    path("customers/<int:pk>/status/", V.customer_status, name="customer_status"),
    path("ids/<int:pk>/<str:side>/", V.id_image, name="id_image"),
    path("appraisals/", V.appraisals, name="appraisals"),
    path("appraisals/new/", V.appraisal_new, name="appraisal_new"),
    path("appraisals/<int:pk>/", V.appraisal_detail, name="appraisal_detail"),
    path("appraisals/<int:pk>/action/", V.appraisal_action, name="appraisal_action"),
    path("tickets/", V.tickets, name="tickets"),
    path("tickets/<int:pk>/", V.ticket_detail, name="ticket_detail"),
    path("tickets/<int:pk>/redeem/", V.ticket_redeem, name="ticket_redeem"),
    path("tickets/<int:pk>/renew/", V.ticket_renew, name="ticket_renew"),
    path("transactions/<int:pk>/", V.txn_detail, name="txn_detail"),
    path("lookup/", V.lookup, name="lookup"),
    path("padala/", V.padala, name="padala"),
    path("padala/claim/", V.padala_claim, name="padala_claim"),
    path("load/", V.load, name="load"),
    path("auctions/", V.auctions, name="auctions"),
    path("auctions/new/<int:ticket_pk>/", V.auction_new, name="auction_new"),
    path("auctions/<int:pk>/result/", V.auction_result, name="auction_result"),
    path("ledger/", V.ledger_view, name="ledger"),
    path("ledger/new/", V.ledger_new, name="ledger_new"),
    path("reports/", V.reports, name="reports"),
    path("reports/<str:name>/", V.report_view, name="report_view"),
    path("support/", V.support, name="support"),
    path("support/<int:pk>/", V.support_detail, name="support_detail"),
    path("queue/<int:pk>/", V.queue_adjust, name="queue_adjust"),
    path("admin/employees/", V.employees, name="employees"),
    path("admin/employees/new/", V.employee_edit, name="employee_new"),
    path("admin/employees/<int:pk>/edit/", V.employee_edit, name="employee_edit"),
    path("admin/audit/", V.audit_log, name="audit_log"),
    path("admin/backups/", V.backups, name="backups"),
    path("admin/sms/", V.sms_log, name="sms_log"),
]
for key, (lst, new, edit, delete) in V.CRUDS.items():
    staff += [
        path(f"admin/{key}/", lst, name=f"{key}_list"),
        path(f"admin/{key}/new/", new, name=f"{key}_new"),
        path(f"admin/{key}/<int:pk>/edit/", edit, name=f"{key}_edit"),
        path(f"admin/{key}/<int:pk>/delete/", delete, name=f"{key}_delete"),
    ]

client = [
    path("", C.welcome, name="client_welcome"),
    path("home/", C.home, name="client_home"),
    path("register/", C.register, name="client_register"),
    path("signup/", C.signup, name="client_signup"),
    path("key/", C.recovery_key_page, name="client_key"),
    path("login/", C.client_login, name="client_login"),
    path("recover/", C.recover, name="client_recover"),
    path("logout/", C.client_logout, name="client_logout"),
    path("history/", C.history, name="client_history"),
    path("tickets/<int:pk>/", C.ticket, name="client_ticket"),
    path("qr/<str:txn_no>.png", C.qr_image, name="client_qr"),
    path("wrap/", C.wrap, name="client_wrap"),
    path("branches/", C.branches, name="client_branches"),
    path("chat/", C.chat, name="client_chat"),
    path("help/", C.support_list, name="client_support"),
    path("help/new/", C.support_new, name="client_support_new"),
    path("help/<int:pk>/", C.support_detail, name="client_support_detail"),
    path("profile/", C.profile, name="client_profile"),
    path("currency/", C.set_currency, name="client_currency"),
]

urlpatterns = [
    path("", lambda r: redirect("client_welcome")),
    path("staff/", include(staff)),
    path("app/", include(client)),
    path("geo/search/", geo.search, name="geo_search"),
    path("geo/weather/", geo.weather, name="geo_weather"),
    path("api/bot/", api.bot, name="api_bot"),
    path("api/", include(router.urls)),
    path("receipts/<str:txn_no>.pdf", V.receipt_file, name="receipt_file"),
    path("sw.js", service_worker),
    path("manifest.webmanifest", manifest),
    path("offline/", offline, name="offline"),
]
