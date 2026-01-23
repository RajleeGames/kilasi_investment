# sales/models.py
from decimal import Decimal, InvalidOperation
from django.db import models
from django.db.models import Sum
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from inventory.models import Product, Branch

User = get_user_model()


# -----------------------------
# Money helpers
# -----------------------------
def to_decimal(value) -> Decimal:
    """
    Convert int/str/Decimal -> Decimal safely.
    Always returns a Decimal (never raises).
    """
    try:
        if value is None:
            return Decimal("0.00")
        return Decimal(str(value))
    except (TypeError, InvalidOperation, ValueError):
        return Decimal("0.00")


def to_int_tsh(value) -> int:
    """
    Convert Decimal/str/int -> int TSH (floor by int()) safely.
    Your system mostly treats TSH as integer on receipts.
    """
    try:
        return int(to_decimal(value))
    except Exception:
        return 0


# -----------------------------
# Core models
# -----------------------------
class Customer(models.Model):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name or f"Customer #{self.pk}"


class Invoice(models.Model):
    PAYMENT_CHOICES = [
        ("cash", "Cash"),
        ("debt", "Debt"),
        ("bank", "Bank"),
    ]

    # Branch-aware sales invoices (transport has its own app)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sales_invoices",
        help_text="Sales branch where this invoice was created",
        db_index=True,
    )

    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )

    seller = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="sold_invoices",
    )

    status = models.CharField(
        max_length=10,
        choices=PAYMENT_CHOICES,
        default="debt",
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # ✅ For cash/bank you set paid=True during sale.
    # ✅ For debt it becomes True when balance becomes 0.
    paid = models.BooleanField(default=False, db_index=True)

    # Totals stored as Decimal
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    shipping_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["branch", "created_at"]),
            models.Index(fields=["branch", "status", "paid"]),
            models.Index(fields=["status", "paid", "created_at"]),
        ]

    def __str__(self):
        return f"Invoice #{self.pk} — {self.get_status_display()}"

    def get_absolute_url(self):
        return reverse("sales:invoice_detail", kwargs={"pk": self.pk})

    # -----------------------------
    # Totals & payments
    # -----------------------------
    def calculate_totals(self, save=True):
        """
        Recompute:
         - subtotal = sum(items.line_total)
         - total_amount = subtotal + tax + shipping_fee
        """
        total = Decimal("0.00")
        for item in self.items.all():
            total += to_decimal(item.line_total)

        self.subtotal = to_decimal(total)
        self.tax = to_decimal(self.tax)
        self.shipping_fee = to_decimal(self.shipping_fee)

        try:
            self.total_amount = self.subtotal + self.tax + self.shipping_fee
        except InvalidOperation:
            self.total_amount = Decimal("0.00")

        # keep "paid" consistent especially for debt invoices
        self.update_paid_status(save=False)

        if save:
            self.save(update_fields=["subtotal", "tax", "shipping_fee", "total_amount", "paid"])

    def payments_total_tsh(self) -> int:
        """
        Total payments received so far (int TSH).
        """
        paid_sum = self.payments.aggregate(t=Sum("amount"))["t"] or 0
        return int(paid_sum)

    def balance_tsh(self) -> int:
        """
        Remaining balance (int TSH).
        """
        total_int = to_int_tsh(self.total_amount)
        return max(0, total_int - self.payments_total_tsh())

    # ✅ Nice for templates (no confusion invoice.balance vs invoice.balance())
    @property
    def payments_total(self) -> int:
        return self.payments_total_tsh()

    @property
    def balance(self) -> int:
        return self.balance_tsh()

    def update_paid_status(self, save=True):
        """
        Rules:
          - cash/bank -> usually paid=True (unless you explicitly want unpaid cash/bank)
          - debt -> paid=True only when balance == 0
        """
        if self.status in ("cash", "bank"):
            # cash/bank should be paid if you use immediate payment.
            # If you ever want unpaid cash/bank, remove this.
            new_paid = True
        else:
            new_paid = (self.balance_tsh() <= 0)

        if self.paid != new_paid:
            self.paid = new_paid
            if save:
                self.save(update_fields=["paid"])


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE)

    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    discount = models.PositiveIntegerField(
        default=0,
        help_text="Flat amount (integer TSH) to subtract per unit",
    )

    class Meta:
        verbose_name = "Invoice Item"
        verbose_name_plural = "Invoice Items"
        indexes = [
            models.Index(fields=["product"]),
            models.Index(fields=["invoice", "product"]),
        ]

    def __str__(self):
        return f"{self.quantity}×{self.product.name}"

    @property
    def effective_price(self) -> Decimal:
        base = to_decimal(self.unit_price)
        disc = to_decimal(self.discount)
        eff = base - disc
        return eff if eff >= 0 else Decimal("0.00")

    @property
    def line_total(self) -> Decimal:
        return self.effective_price * int(self.quantity or 0)

    def save(self, *args, **kwargs):
        self.unit_price = to_decimal(self.unit_price)
        super().save(*args, **kwargs)
        # Recalculate invoice totals after saving this item
        if self.invoice_id:
            self.invoice.calculate_totals()

    def delete(self, *args, **kwargs):
        inv = self.invoice
        super().delete(*args, **kwargs)
        if inv:
            inv.calculate_totals()


class Payment(models.Model):
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="payments",
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_payments",
    )

    # integer TSH
    amount = models.PositiveIntegerField(help_text="Amount paid (integer TSH)")
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["timestamp"]),
            models.Index(fields=["invoice", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.amount} TSH on {self.timestamp:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # update invoice paid status after payment
        inv = self.invoice
        if inv:
            inv.update_paid_status(save=True)

    def delete(self, *args, **kwargs):
        inv = self.invoice
        super().delete(*args, **kwargs)
        if inv:
            inv.update_paid_status(save=True)


class Expense(models.Model):
    """
    Sales expenses (transport expenses are in transport app).
    """

    date = models.DateField(default=timezone.localdate, db_index=True)

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sales_expenses",
        db_index=True,
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    category = models.CharField(max_length=50, help_text="e.g. Labor, Rent, Supplies, etc.")
    description = models.CharField(max_length=255, blank=True)

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
        help_text="Who recorded this expense",
    )

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["branch", "date"]),
        ]

    def __str__(self):
        return f"{self.date}: {self.category} – Tsh {to_decimal(self.amount):.2f}"


class Sale(models.Model):
    """
    (Optional/legacy) Keep only if your project still uses it.
    """
    date = models.DateField(default=timezone.localdate, db_index=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["-date", "-id"]
