from django.core.management.base import BaseCommand

from pawn import services


class Command(BaseCommand):
    help = "Nightly job: send due-date reminders and flag tickets past their grace period for auction."

    def handle(self, *args, **opts):
        r = services.run_alerts()
        self.stdout.write(f"{r['sms']} messages logged, {r['expired']} tickets moved to Expired / For Auction.")
