from datetime import timedelta

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from inventory.models import Branch


# =========================================================
# VEHICLE
# =========================================================

class Vehicle(models.Model):
    plate_number = models.CharField(
        max_length=50,
        unique=True,
    )

    model = models.CharField(
        max_length=100,
        blank=True,
    )

    capacity = models.CharField(
        max_length=100,
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    class Meta:
        ordering = [
            "plate_number",
        ]

        indexes = [
            models.Index(
                fields=["plate_number"],
            ),
            models.Index(
                fields=["is_active"],
            ),
        ]

    def __str__(self):
        if self.model:
            return f"{self.plate_number} ({self.model})"

        return self.plate_number

    def get_absolute_url(self):
        return reverse(
            "transport:vehicle_list"
        )


# =========================================================
# DRIVER
# =========================================================

class Driver(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="driver_profile",
    )

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transport_drivers",
        help_text=(
            "The branch this driver belongs to."
        ),
    )

    first_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    last_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    phone = models.CharField(
        max_length=20,
        blank=True,
    )

    license_number = models.CharField(
        max_length=50,
        blank=True,
    )

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="drivers",
    )

    photo = models.ImageField(
        upload_to="drivers/",
        blank=True,
        null=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "first_name",
            "last_name",
            "user__username",
        ]

        indexes = [
            models.Index(
                fields=["is_active"],
            ),
            models.Index(
                fields=["created_at"],
            ),
            models.Index(
                fields=["phone"],
            ),
            models.Index(
                fields=[
                    "branch",
                    "is_active",
                ],
            ),
        ]

    @property
    def full_name(self):
        name = (
            f"{self.first_name} "
            f"{self.last_name}"
        ).strip()

        return (
            name
            or self.user.get_full_name()
            or self.user.username
        )

    @property
    def display_name(self):
        if self.phone:
            return (
                f"{self.full_name} "
                f"({self.phone})"
            )

        return self.full_name

    def __str__(self):
        return self.display_name

    def get_absolute_url(self):
        return reverse(
            "transport:driver_list"
        )


# =========================================================
# TRIP
# =========================================================

class Trip(models.Model):
    STATUS_PENDING = "pending"
    STATUS_IN_TRANSIT = "in_transit"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (
            STATUS_PENDING,
            "Pending",
        ),
        (
            STATUS_IN_TRANSIT,
            "In Transit",
        ),
        (
            STATUS_COMPLETED,
            "Completed",
        ),
        (
            STATUS_CANCELLED,
            "Cancelled",
        ),
    ]

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="transport_trips",
    )

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.PROTECT,
        related_name="trips",
    )

    driver = models.ForeignKey(
        Driver,
        on_delete=models.PROTECT,
        related_name="trips",
    )

    origin = models.CharField(
        max_length=255,
    )

    destination = models.CharField(
        max_length=255,
    )

    customer_name = models.CharField(
        max_length=150,
    )

    customer_phone = models.CharField(
        max_length=20,
    )

    amount_charged = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    notes = models.TextField(
        blank=True,
    )

    trip_date = models.DateTimeField(
        default=timezone.now,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    pending_sms_sent = models.BooleanField(
        default=False,
    )

    pending_sms_sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    pending_sms_error = models.TextField(
        blank=True,
    )

    completed_sms_sent = models.BooleanField(
        default=False,
    )

    completed_sms_sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    completed_sms_error = models.TextField(
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_trips",
    )

    created_at = models.DateTimeField(
        default=timezone.now,
        editable=False,
    )

    updated_at = models.DateTimeField(
        default=timezone.now,
    )

    class Meta:
        ordering = [
            "-trip_date",
            "-id",
        ]

        indexes = [
            models.Index(
                fields=["trip_date"],
            ),
            models.Index(
                fields=[
                    "branch",
                    "trip_date",
                ],
            ),
            models.Index(
                fields=["vehicle"],
            ),
            models.Index(
                fields=["driver"],
            ),
            models.Index(
                fields=[
                    "driver",
                    "status",
                    "trip_date",
                ],
            ),
            models.Index(
                fields=[
                    "status",
                    "trip_date",
                ],
            ),
            models.Index(
                fields=["customer_name"],
            ),
            models.Index(
                fields=["customer_phone"],
            ),
        ]

    def save(self, *args, **kwargs):
        self.updated_at = timezone.now()

        return super().save(
            *args,
            **kwargs,
        )

    def __str__(self):
        customer = (
            self.customer_name
            or self.customer_phone
        )

        return (
            f"{customer} | "
            f"{self.vehicle} | "
            f"{self.origin} to "
            f"{self.destination}"
        )

    def get_absolute_url(self):
        return reverse(
            "transport:trip_detail",
            kwargs={
                "pk": self.pk,
            },
        )

    @property
    def can_start(self):
        return (
            self.status
            == self.STATUS_PENDING
        )

    @property
    def can_complete(self):
        return (
            self.status
            == self.STATUS_IN_TRANSIT
        )

    @property
    def is_closed(self):
        return self.status in {
            self.STATUS_COMPLETED,
            self.STATUS_CANCELLED,
        }


# =========================================================
# AUTOMATIC TRANSPORT SMS TEMPLATE
# =========================================================

class TransportSMSTemplate(models.Model):
    # Customer receives this when a trip is created.
    EVENT_PENDING = "trip_pending"

    # Customer receives this when a trip is completed.
    EVENT_COMPLETED = "trip_completed"

    # Boss receives this when a new pending trip is created.
    EVENT_BOSS_PENDING = "boss_trip_pending"

    # Customer receives this immediately after a booking is created.
    EVENT_BOOKING_CONFIRMED = "booking_confirmed"

    # Boss receives this immediately after a booking is created.
    EVENT_BOOKING_BOSS = "booking_boss"

    # Customer receives this approximately one hour before departure.
    EVENT_BOOKING_REMINDER = "booking_reminder"

    EVENT_CHOICES = [
        (
            EVENT_PENDING,
            "Trip Created / Customer",
        ),
        (
            EVENT_COMPLETED,
            "Trip Completed / Customer",
        ),
        (
            EVENT_BOSS_PENDING,
            "New Trip Notification / Boss",
        ),
        (
            EVENT_BOOKING_CONFIRMED,
            "Booking Confirmed / Customer",
        ),
        (
            EVENT_BOOKING_BOSS,
            "New Booking Notification / Boss",
        ),
        (
            EVENT_BOOKING_REMINDER,
            "One Hour Booking Reminder / Customer",
        ),
    ]

    event = models.CharField(
        max_length=30,
        choices=EVENT_CHOICES,
        unique=True,
    )

    message = models.TextField(
        help_text=(
            "Enter the SMS message. New lines and "
            "blank lines will be preserved."
        ),
    )

    sender_id = models.ForeignKey(
        "sms.SenderID",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name=(
            "transport_auto_templates"
        ),
    )

    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Only active templates are sent "
            "automatically."
        ),
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name=(
            "transport_sms_templates_updated"
        ),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "event",
        ]

        verbose_name = (
            "Transport automatic SMS template"
        )

        verbose_name_plural = (
            "Transport automatic SMS templates"
        )

    def __str__(self):
        return self.get_event_display()


# =========================================================
# TRANSPORT EXPENSE
# =========================================================

class TransportExpense(models.Model):
    EXPENSE_CHOICES = [
        (
            "fuel",
            "Fuel",
        ),
        (
            "repair",
            "Repair",
        ),
        (
            "service",
            "Service",
        ),
        (
            "office_rent",
            "Office Rent",
        ),
        (
            "water_bill",
            "Water Bill",
        ),
        (
            "electricity",
            "Electricity",
        ),
        (
            "internet",
            "Internet / Phone",
        ),
        (
            "salary",
            "Salary / Wages",
        ),
        (
            "other",
            "Other",
        ),
    ]

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="transport_expenses",
    )

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.PROTECT,
        related_name="expenses",
        null=True,
        blank=True,
    )

    expense_type = models.CharField(
        max_length=30,
        choices=EXPENSE_CHOICES,
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    description = models.CharField(
        max_length=255,
        blank=True,
    )

    expense_date = models.DateTimeField(
        default=timezone.now,
    )

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name=(
            "transport_expenses"
        ),
    )

    class Meta:
        ordering = [
            "-expense_date",
        ]

        indexes = [
            models.Index(
                fields=["expense_date"],
            ),
            models.Index(
                fields=[
                    "branch",
                    "expense_date",
                ],
            ),
            models.Index(
                fields=["vehicle"],
            ),
            models.Index(
                fields=["expense_type"],
            ),
        ]

    def __str__(self):
        if self.vehicle:
            return (
                f"{self.vehicle} | "
                f"{self.get_expense_type_display()} | "
                f"{self.amount}"
            )

        return (
            "General Expense | "
            f"{self.get_expense_type_display()} | "
            f"{self.amount}"
        )

    def get_absolute_url(self):
        return reverse(
            "transport:expense_list"
        )

    @property
    def is_general_expense(self):
        return self.vehicle is None


# =========================================================
# BOOKING
# =========================================================

class Booking(models.Model):
    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (
            STATUS_PENDING,
            "Pending",
        ),
        (
            STATUS_COMPLETED,
            "Completed",
        ),
        (
            STATUS_CANCELLED,
            "Cancelled",
        ),
    ]

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name=(
            "transport_bookings"
        ),
    )

    customer_name = models.CharField(
        max_length=150,
    )

    customer_phone = models.CharField(
        max_length=30,
    )

    pickup_area = models.CharField(
        max_length=255,
    )

    destination = models.CharField(
        max_length=255,
        blank=True,
    )

    booking_datetime = models.DateTimeField()

    notes = models.TextField(
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    # Customer booking confirmation SMS.
    confirmation_sms_sent = models.BooleanField(
        default=False,
    )

    confirmation_sms_sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    confirmation_sms_error = models.TextField(
        blank=True,
        default="",
    )

    # Boss notification for a newly created booking.
    boss_sms_sent = models.BooleanField(
        default=False,
    )

    boss_sms_sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    boss_sms_error = models.TextField(
        blank=True,
        default="",
    )

    # Customer reminder approximately one hour before departure.
    reminder_sent = models.BooleanField(
        default=False,
    )

    reminder_sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    reminder_last_attempt_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    reminder_sms_error = models.TextField(
        blank=True,
        default="",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name=(
            "transport_bookings_created"
        ),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "booking_datetime",
            "-id",
        ]

        indexes = [
            models.Index(
                fields=[
                    "booking_datetime",
                ],
            ),
            models.Index(
                fields=["status"],
            ),
            models.Index(
                fields=[
                    "branch",
                    "booking_datetime",
                ],
            ),
            models.Index(
                fields=[
                    "customer_phone",
                ],
            ),
            models.Index(
                fields=[
                    "status",
                    "reminder_sent",
                    "booking_datetime",
                ],
            ),
        ]

    def __str__(self):
        destination = (
            self.destination
            or "Not specified"
        )

        return (
            f"{self.customer_name} - "
            f"{self.pickup_area} to "
            f"{destination} - "
            f"{self.booking_datetime:%d %b %Y %H:%M}"
        )

    @property
    def is_overdue(self):
        return (
            self.status == self.STATUS_PENDING
            and self.booking_datetime
            < timezone.now()
        )

    @property
    def is_reminder_due(self):
        now = timezone.now()

        return (
            self.status == self.STATUS_PENDING
            and not self.reminder_sent
            and now < self.booking_datetime
            <= now + timedelta(hours=1)
        )

    def get_absolute_url(self):
        return reverse(
            "transport:booking_list"
        )