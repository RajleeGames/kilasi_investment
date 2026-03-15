from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.urls import reverse
from inventory.models import Branch


# -------------------------
# Vehicle
# -------------------------
class Vehicle(models.Model):
    plate_number = models.CharField(max_length=50, unique=True)
    model = models.CharField(max_length=100, blank=True)
    capacity = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["plate_number"]
        indexes = [
            models.Index(fields=["plate_number"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.plate_number} ({self.model})" if self.model else self.plate_number

    def get_absolute_url(self):
        return reverse("transport:vehicle_list")


# -------------------------
# Driver
# -------------------------
class Driver(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="driver_profile"
    )
    first_name = models.CharField(max_length=100, blank=True, default="")
    last_name = models.CharField(max_length=100, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True)
    license_number = models.CharField(max_length=50, blank=True)

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="drivers"
    )

    photo = models.ImageField(upload_to="drivers/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_active"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["phone"]),
        ]

    @property
    def full_name(self):
        name = f"{self.first_name} {self.last_name}".strip()
        if name and self.phone:
            return f"{name} ({self.phone})"
        if name:
            return name
        return self.user.username

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse("transport:driver_list")


# -------------------------
# Trip
# -------------------------
class Trip(models.Model):
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="transport_trips"
    )

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.PROTECT,
        related_name="trips"
    )

    driver = models.ForeignKey(
        Driver,
        on_delete=models.PROTECT,
        related_name="trips"
    )

    origin = models.CharField(max_length=255)
    destination = models.CharField(max_length=255)

    # ✅ NEW
    customer_phone = models.CharField(max_length=20, blank=True, default="")

    amount_charged = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.TextField(blank=True)

    trip_date = models.DateTimeField(default=timezone.now)

    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="created_trips"
    )

    class Meta:
        ordering = ["-trip_date"]
        indexes = [
            models.Index(fields=["trip_date"]),
            models.Index(fields=["branch", "trip_date"]),
            models.Index(fields=["vehicle"]),
            models.Index(fields=["driver"]),
            models.Index(fields=["customer_phone"]),  # ✅ NEW
        ]

    def __str__(self):
        return f"{self.vehicle} | {self.origin} → {self.destination}"

    def get_absolute_url(self):
        return reverse("transport:trip_list")

# -------------------------
# Transport Expenses
# -------------------------
class TransportExpense(models.Model):
    EXPENSE_CHOICES = [
        ("fuel", "Fuel"),
        ("repair", "Repair"),
        ("service", "Service"),
        ("office_rent", "Office Rent"),
        ("water_bill", "Water Bill"),
        ("electricity", "Electricity"),
        ("internet", "Internet / Phone"),
        ("salary", "Salary / Wages"),
        ("other", "Other"),
    ]

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="transport_expenses"
    )

    # ✅ NOW OPTIONAL
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.PROTECT,
        related_name="expenses",
        null=True,
        blank=True
    )

    expense_type = models.CharField(
        max_length=30,
        choices=EXPENSE_CHOICES
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)

    expense_date = models.DateTimeField(default=timezone.now)

    recorded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="transport_expenses"
    )

    class Meta:
        ordering = ["-expense_date"]
        indexes = [
            models.Index(fields=["expense_date"]),
            models.Index(fields=["branch", "expense_date"]),
            models.Index(fields=["vehicle"]),
            models.Index(fields=["expense_type"]),
        ]

    def __str__(self):
        if self.vehicle:
            return f"{self.vehicle} | {self.get_expense_type_display()} | {self.amount}"
        return f"General Expense | {self.get_expense_type_display()} | {self.amount}"

    def get_absolute_url(self):
        return reverse("transport:expense_list")

    @property
    def is_general_expense(self):
        return self.vehicle is None


# -------------------------
# Booking / Order
# -------------------------
class Booking(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="transport_bookings"
    )

    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=30)

    pickup_area = models.CharField(max_length=255)
    destination = models.CharField(max_length=255, blank=True)

    booking_datetime = models.DateTimeField()
    notes = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    reminder_sent = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="transport_bookings_created"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["booking_datetime", "-id"]
        indexes = [
            models.Index(fields=["booking_datetime"]),
            models.Index(fields=["status"]),
            models.Index(fields=["branch", "booking_datetime"]),
            models.Index(fields=["customer_phone"]),
        ]

    def __str__(self):
        return f"{self.customer_name} - {self.pickup_area} - {self.booking_datetime:%d %b %Y %H:%M}"

    @property
    def is_overdue(self):
        return self.status == "pending" and self.booking_datetime < timezone.now()

    def get_absolute_url(self):
        return reverse("transport:booking_list")