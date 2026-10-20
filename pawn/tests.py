from datetime import date, timedelta
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from . import services as S
from .models import Customer, PawnTicket, Role, User


class InterestEngine(TestCase):
    def test_months_round_up(self):
        start = date(2026, 10, 19)
        self.assertEqual(S.months_elapsed(start, date(2026, 10, 19)), 1)
        self.assertEqual(S.months_elapsed(start, date(2026, 11, 19)), 1)
        self.assertEqual(S.months_elapsed(start, date(2026, 11, 20)), 2)
        self.assertEqual(S.months_elapsed(start, date(2026, 12, 19)), 2)
        self.assertEqual(S.months_elapsed(start, date(2026, 12, 20)), 3)

    def test_add_months_clamps_to_month_end(self):
        self.assertEqual(S.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(S.add_months(date(2026, 12, 15), 1), date(2027, 1, 15))


class FullCycle(TestCase):
    def setUp(self):
        call_command("seed", verbosity=0)
        self.cashier = User.objects.get(username="cashier").employee
        self.ticket = PawnTicket.objects.get(customer__last_name="Santos")

    def test_demo_example_from_the_guide(self):
        # loan 10,000 at 3%, redeemed in month 2 -> interest 600
        on = S.add_months(self.ticket.loan_date, 1) + timedelta(days=1)
        d = S.compute_due(self.ticket, on)
        self.assertEqual(d["months"], 2)
        self.assertEqual(d["interest_due"], Decimal("600.00"))
        self.assertEqual(d["redeem_total"], Decimal("10650.00"))   # + 50 service fee

    def test_renewal_then_redemption(self):
        S.renew_ticket(self.ticket, Decimal("300"), self.cashier)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "Renewed")
        self.assertEqual(self.ticket.renewals.count(), 1)
        d = S.compute_due(self.ticket)
        self.assertEqual(d["interest_due"], Decimal("0.00"))        # already paid for month one
        S.redeem_ticket(self.ticket, Decimal("20000"), self.cashier)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "Redeemed")
        self.assertEqual(self.ticket.item.status, "Released")

    def test_short_payment_is_refused(self):
        with self.assertRaises(S.PawnError):
            S.redeem_ticket(self.ticket, Decimal("100"), self.cashier)

    def test_expired_ticket_goes_to_auction(self):
        expired = PawnTicket.objects.get(customer__last_name="Dela Cruz")
        self.assertEqual(expired.status, "Expired")
        self.assertEqual(expired.item.status, "For Auction")
        a = S.schedule_auction(expired, Decimal("6000"), timezone.localdate(), self.cashier)
        S.record_auction(a, "Sold", self.cashier, "Pedro Lim", "09170000000", Decimal("6500"))
        expired.refresh_from_db()
        self.assertEqual(expired.status, "Auctioned")
        self.assertEqual(expired.item.status, "Sold")


class Access(TestCase):
    def setUp(self):
        call_command("seed", verbosity=0)

    def test_clients_cannot_open_staff_pages(self):
        self.client.login(username="admin", password="OnlyPawns#2026")
        self.assertEqual(self.client.get("/staff/").status_code, 200)
        self.client.logout()
        maria = Customer.objects.get(last_name="Santos").user
        self.client.force_login(maria)
        self.assertEqual(self.client.get("/staff/", follow=False).status_code, 302)
        self.assertEqual(self.client.get("/app/home/").status_code, 200)

    def test_cashier_cannot_see_admin_reports(self):
        self.client.login(username="cashier", password="OnlyPawns#2026")
        self.assertEqual(self.client.get("/staff/reports/cashflow/").status_code, 302)
        self.assertEqual(self.client.get("/staff/reports/daily/").status_code, 200)


class ListsSearchSortPaging(TestCase):
    def setUp(self):
        call_command("seed", verbosity=0)
        self.client.login(username="admin", password="OnlyPawns#2026")

    def test_search_finds_and_misses(self):
        hit = self.client.get("/staff/customers/", {"q": "Santos"})
        self.assertContains(hit, "Maria")
        miss = self.client.get("/staff/customers/", {"q": "zzzz-nobody"})
        self.assertContains(miss, "No records match")

    def test_sort_whitelist_ignores_unknown_fields(self):
        self.assertEqual(self.client.get("/staff/customers/", {"sort_by": "last_name"}).status_code, 200)
        self.assertEqual(self.client.get("/staff/customers/", {"sort_by": "user__password"}).status_code, 200)
        self.assertEqual(self.client.get("/staff/tickets/", {"sort_by": "-loan_amount"}).status_code, 200)

    def test_sort_dropdown_keeps_selection(self):
        r = self.client.get("/staff/customers/", {"sort_by": "status"})
        self.assertContains(r, '<option value="status" selected>')

    def test_every_list_page_opens(self):
        for url in ("/staff/appraisals/", "/staff/tickets/", "/staff/ledger/", "/staff/support/", "/staff/admin/employees/",
                    "/staff/admin/audit/", "/staff/admin/sms/", "/staff/admin/branches/", "/staff/admin/categories/",
                    "/staff/admin/currencies/", "/staff/admin/rates/", "/staff/admin/idtypes/"):
            self.assertEqual(self.client.get(url, {"q": "a", "sort_by": "pk"}).status_code, 200, url)

    def test_dashboard_shows_real_counts(self):
        r = self.client.get("/staff/")
        self.assertEqual(r.context["customers_total"], Customer.objects.count())
        self.assertIn("customers_this_year", r.context)


class ClassBasedCrud(TestCase):
    def setUp(self):
        call_command("seed", verbosity=0)

    def test_idtype_add_edit_delete(self):
        from .models import IDType
        self.client.login(username="admin", password="OnlyPawns#2026")
        self.assertEqual(self.client.get("/staff/admin/idtypes/new/").status_code, 200)
        r = self.client.post("/staff/admin/idtypes/new/", {"id_type_name": "Test ID"})
        self.assertEqual(r.status_code, 302)
        obj = IDType.objects.get(id_type_name="Test ID")
        r = self.client.post(f"/staff/admin/idtypes/{obj.pk}/edit/", {"id_type_name": "Test ID 2"})
        self.assertEqual(r.status_code, 302)
        obj.refresh_from_db()
        self.assertEqual(obj.id_type_name, "Test ID 2")
        self.assertEqual(self.client.get(f"/staff/admin/idtypes/{obj.pk}/delete/").status_code, 405)
        self.client.post(f"/staff/admin/idtypes/{obj.pk}/delete/")
        self.assertFalse(IDType.objects.filter(pk=obj.pk).exists())

    def test_cashier_cannot_open_admin_crud(self):
        self.client.login(username="cashier", password="OnlyPawns#2026")
        self.assertEqual(self.client.get("/staff/admin/branches/").status_code, 302)
