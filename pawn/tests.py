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
