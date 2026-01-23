# transport/models.py
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
        # Example: "T 123 ABC (Hiace)"
        return f"{self.plate_number} ({self.model})" if self.model else self.plate_number

    def get_absolute_url(self):
        # adjust if you have details pages later
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
        # include phone if available
        if name and self.phone:
            return f"{name} ({self.phone})"
        if name:
            return name
        # fallback to username if no name
        return self.user.username

    def __str__(self):
        # dropdown will show: "John Doe (+2557...)" or username fallback
        return self.full_name

    def get_absolute_url(self):
        return reverse("transport:driver_list")


# -------------------------
# Trip (MAIN TRANSPORT SALE)
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
        ("other", "Other"),
    ]

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="transport_expenses"
    )

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.PROTECT,
        related_name="expenses"
    )

    expense_type = models.CharField(
        max_length=20,
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
        return f"{self.vehicle} | {self.get_expense_type_display()} | {self.amount}"

    def get_absolute_url(self):
        return reverse("transport:expense_list")
