from django.core.management.base import BaseCommand

from transport.services import (
    process_due_booking_reminders,
)


class Command(BaseCommand):
    help = (
        "Send reminders for pending transport bookings "
        "taking place within the next hour."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Maximum reminders to process.",
        )

    def handle(self, *args, **options):
        result = process_due_booking_reminders(
            limit=options["limit"],
        )

        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Checked: {result['checked']} | "
                    f"Sent: {result['sent']} | "
                    f"Failed: {result['failed']} | "
                    f"Skipped: {result['skipped']}"
                )
            )
        )