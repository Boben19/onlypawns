from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand
from django.utils import timezone

from pawn import services as S
from pawn.models import (
    Branch, Currency, Customer, CustomerID, Employee, ExchangeRate, IDType, ItemCategory, PawnTicket, Role, TransactionType,
    User,
)

STAFF_PASSWORD = "OnlyPawns#2026"


class Command(BaseCommand):
    help = "Load roles, rates, branches, staff accounts and a small demo story (safe to run twice)."

    def handle(self, *args, **opts):
        roles = {n: Role.objects.get_or_create(role_name=n)[0] for n in (Role.CLIENT, Role.ADMIN, Role.CASHIER, Role.APPRAISER)}
        for n in ("Appraise", "Sangla", "Loan", "Padala", "Load", "Redemption", "Renewal"):
            TransactionType.objects.get_or_create(type_name=n)
        id_types = {n: IDType.objects.get_or_create(id_type_name=n)[0]
                    for n in ("Driver's License", "PhilSys ID", "Passport", "Birth Certificate")}
        php, _ = Currency.objects.get_or_create(code="PHP", defaults={"name": "Philippine Peso", "symbol": "₱"})
        for code, name, sym, rate in (("USD", "US Dollar", "$", "58.00"), ("EUR", "Euro", "€", "63.00")):
            cur, made = Currency.objects.get_or_create(code=code, defaults={"name": name, "symbol": sym})
            if made:
                ExchangeRate.objects.create(currency=cur, rate_to_php=Decimal(rate))
        for name, rate, fee in (("Jewelry", "0.0300", "50"), ("Gadgets", "0.0350", "50"), ("Watches", "0.0300", "50"), ("Appliances", "0.0400", "75")):
            ItemCategory.objects.get_or_create(category_name=name, defaults={"monthly_interest_rate": Decimal(rate), "service_fee": Decimal(fee), "grace_period_days": 90})

        branches = []
        for name, addr, lat, lng, phone, counters in (
            ("Puerto Princesa Main", "Rizal Avenue, Puerto Princesa City", "9.739200", "118.735300", "0917-555-0101", 3),
            ("Puerto Princesa Public Market", "Malvar Street, Puerto Princesa City", "9.745100", "118.737900", "0917-555-0102", 2),
            ("Roxas", "Poblacion, Roxas, Palawan", "10.325300", "119.343600", "0917-555-0103", 1),
            ("Brooke's Point", "Poblacion, Brooke's Point, Palawan", "8.774700", "117.838600", "0917-555-0104", 1)):
            b, _ = Branch.objects.get_or_create(branch_name=name, defaults=dict(
                address=addr, latitude=Decimal(lat), longitude=Decimal(lng), contact_no=phone, open_time=time(8, 30),
                close_time=time(17, 30), open_counters=counters))
            branches.append(b)

        staff = {}
        for username, role, first, last, pos in (
            ("admin", Role.ADMIN, "Grace", "Manalo", "Branch Manager"),
            ("cashier", Role.CASHIER, "Paolo", "Ramos", "Cashier"),
            ("appraiser", Role.APPRAISER, "Lita", "Dizon", "Appraiser")):
            u, made = User.objects.get_or_create(username=username, defaults=dict(
                role=roles[role], first_name=first, last_name=last, is_staff=role == Role.ADMIN, is_superuser=role == Role.ADMIN))
            if made:
                u.set_password(STAFF_PASSWORD)
                u.save()
            staff[role] = Employee.objects.get_or_create(user=u, defaults=dict(
                branch=branches[0], first_name=first, last_name=last, position=pos))[0]

        if PawnTicket.objects.exists():
            self.stdout.write("Demo story already loaded. Staff logins: admin / cashier / appraiser, password " + STAFF_PASSWORD)
            return

        def customer(first, last, mobile, email, bd, income, number):
            c = Customer.objects.create(first_name=first, last_name=last, birthdate=bd, sex="Female" if first in ("Maria", "Ana") else "Male",
                                        civil_status="Single", address="Puerto Princesa City, Palawan", mobile_no=mobile,
                                        email=email, source_of_income=income, status="Active", verified_by=staff[Role.CASHIER],
                                        verified_at=timezone.now())
            t = id_types["PhilSys ID"]
            CustomerID.objects.create(customer=c, id_type=t, id_number_enc=S.encrypt_text(number), id_hash=S.id_hash(t.pk, number),
                                      verification_status="Verified")
            u = User(username=c.code, email=email, google_id=f"demo:{email}", role=roles[Role.CLIENT], first_name=first,
                     last_name=last, pin_hash=make_password("123456"))
            u.set_unusable_password()
            u.save()
            c.user = u
            c.save()
            return c

        ap, ca = staff[Role.APPRAISER], staff[Role.CASHIER]
        cat = lambda n: ItemCategory.objects.get(category_name=n)
        maria = customer("Maria", "Santos", "09171230001", "maria.santos@example.com", date(1994, 5, 12), "Sari-sari store", "1234-5678-9012-3456")
        juan = customer("Juan", "Dela Cruz", "09171230002", "juan.delacruz@example.com", date(1988, 9, 3), "Tricycle driver", "2234-5678-9012-3456")
        ana = customer("Ana", "Reyes", "09171230003", "ana.reyes@example.com", date(1999, 1, 25), "Call center agent", "3234-5678-9012-3456")

        # 1. Maria's necklace: the ticket we walk through renewal and redemption in the demo
        a = S.create_appraisal(ap, maria, cat("Jewelry"), "18k gold necklace", "18k gold, 12 grams, no engraving", "Excellent",
                               15000, 10000, "Clean piece, no repairs.", [])
        S.create_ticket(a, 10000, ca)

        # 2. Ana's phone: matures in three days so the dashboard shows a warning badge
        a = S.create_appraisal(ap, ana, cat("Gadgets"), "Smartphone", "Samsung Galaxy A54, 128GB, with charger", "Good", 12000, 7000, "", [])
        t = S.create_ticket(a, 7000, ca, "Loan")
        PawnTicket.objects.filter(pk=t.pk).update(loan_date=timezone.localdate() - timedelta(days=27),
                                                  maturity_date=timezone.localdate() + timedelta(days=3),
                                                  expiry_date=timezone.localdate() + timedelta(days=93))

        # 3. Juan's watch: ticket is five months old, so the nightly job sends it to auction
        a = S.create_appraisal(ap, juan, cat("Watches"), "Seiko 5 automatic", "Seiko 5 automatic, steel strap", "Good", 9000, 5000, "", [])
        t = S.create_ticket(a, 5000, ca)
        old = timezone.localdate() - timedelta(days=150)
        PawnTicket.objects.filter(pk=t.pk).update(loan_date=old, maturity_date=S.add_months(old, 1),
                                                  expiry_date=S.add_months(old, 1) + timedelta(days=90))
        result = S.run_alerts()
        self.stdout.write(self.style.SUCCESS("Demo data loaded."))
        self.stdout.write(f"Alert run: {result}")
        self.stdout.write(f"Staff logins (admin / cashier / appraiser), password: {STAFF_PASSWORD}")
        self.stdout.write("Client demo: maria.santos@example.com, PIN 123456 (also juan.delacruz@ and ana.reyes@example.com)")
