from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from inventory.models import Product, Branch, ProductStock  # ✅ use inventory stock table


# ───────────────────────────────────────────────────────────────
# Helpers (safe decimal + integer TSH)
# ───────────────────────────────────────────────────────────────
def to_decimal(value, default=Decimal("0.00")) -> Decimal:
    try:
        if value is None:
            return default
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def to_int_tsh(value) -> int:
    d = to_decimal(value, default=Decimal("0.00"))
    try:
        return int(d)
    except Exception:
        return 0


# ───────────────────────────────────────────────────────────────
# ✅ Branch stock helpers (SOURCE OF TRUTH = inventory.ProductStock)
# ───────────────────────────────────────────────────────────────
def get_branch_qty(product_id: int, branch_id: int) -> int:
    """
    Returns branch qty from inventory.ProductStock
    """
    obj = ProductStock.objects.filter(product_id=product_id, branch_id=branch_id).first()
    return int(getattr(obj, "quantity", 0) or 0)


def set_branch_qty(product_id: int, branch_id: int, new_qty: int):
    """
    Sets branch qty in inventory.ProductStock
    """
    new_qty = max(0, int(new_qty or 0))
    obj, _ = ProductStock.objects.get_or_create(product_id=product_id, branch_id=branch_id)
    obj.quantity = new_qty
    obj.save(update_fields=["quantity"])


def add_branch_qty(product_id: int, branch_id: int, delta: int):
    """
    Adds/subtracts branch qty safely in inventory.ProductStock
    """
    obj, _ = ProductStock.objects.get_or_create(product_id=product_id, branch_id=branch_id)
    obj.quantity = max(0, int(obj.quantity or 0) + int(delta or 0))
    obj.save(update_fields=["quantity"])


# ───────────────────────────────────────────────────────────────
# Customer
# ───────────────────────────────────────────────────────────────
class Customer(models.Model):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name or f"Customer #{self.pk}"


# ───────────────────────────────────────────────────────────────
# Invoice
# ───────────────────────────────────────────────────────────────
class Invoice(models.Model):
    PAYMENT_CASH = "cash"
    PAYMENT_BANK = "bank"
    PAYMENT_EBT  = "ebt"
    PAYMENT_DEBT = "debt"

    PAYMENT_CHOICES = [
        (PAYMENT_CASH, "Cash"),
        (PAYMENT_BANK, "Bank"),
        (PAYMENT_EBT,  "EBT"),
        (PAYMENT_DEBT, "Debt"),
    ]

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sales_invoices",
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
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sold_invoices",
    )

    status = models.CharField(
        max_length=10,
        choices=PAYMENT_CHOICES,
        default=PAYMENT_DEBT,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    paid = models.BooleanField(default=False, db_index=True)

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

        self.update_paid_status(save=False)

        if save:
            self.save(update_fields=["subtotal", "tax", "shipping_fee", "total_amount", "paid"])

    def payments_total_tsh(self) -> int:
        paid_sum = self.payments.aggregate(t=Sum("amount"))["t"] or 0
        return int(paid_sum)

    def balance_tsh(self) -> int:
        total_int = to_int_tsh(self.total_amount)
        return max(0, total_int - self.payments_total_tsh())

    @property
    def payments_total(self) -> int:
        return self.payments_total_tsh()

    @property
    def balance(self) -> int:
        return self.balance_tsh()

    def update_paid_status(self, save=True):
        """
        cash/bank/ebt -> always paid True
        debt -> paid True only if balance <= 0
        """
        if self.status in (self.PAYMENT_CASH, self.PAYMENT_BANK, self.PAYMENT_EBT):
            new_paid = True
        else:
            new_paid = (self.balance_tsh() <= 0)

        if self.paid != new_paid:
            self.paid = new_paid
            if save:
                self.save(update_fields=["paid"])


# ───────────────────────────────────────────────────────────────
# Invoice Item
# ───────────────────────────────────────────────────────────────
class InvoiceItem(models.Model):
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="items",
    )

    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    # snapshot at sale time
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    buying_cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    discount = models.PositiveIntegerField(
        default=0,
        help_text="Flat amount (integer TSH) to subtract per unit",
    )

    # cached for fast profit reports
    line_cost_cache = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        indexes = [
            models.Index(fields=["product"]),
            models.Index(fields=["invoice", "product"]),
        ]

    def __str__(self):
        return f"{self.quantity}×{self.product}"

    @property
    def unit_price(self) -> Decimal:
        return to_decimal(self.selling_price)

    @property
    def effective_price(self) -> Decimal:
        base = to_decimal(self.selling_price)
        disc = to_decimal(self.discount)
        eff = base - disc
        return eff if eff >= 0 else Decimal("0.00")

    @property
    def line_total(self) -> Decimal:
        return to_decimal(self.effective_price) * int(self.quantity or 0)

    @property
    def line_cost(self) -> Decimal:
        return to_decimal(self.buying_cost) * int(self.quantity or 0)

    def save(self, *args, **kwargs):
        # fill snapshot values if missing
        if not self.selling_price or to_decimal(self.selling_price) <= 0:
            self.selling_price = to_decimal(getattr(self.product, "selling_price", 0) or 0)

        if not self.buying_cost or to_decimal(self.buying_cost) <= 0:
            self.buying_cost = to_decimal(getattr(self.product, "buying_price", 0) or 0)

        self.selling_price = to_decimal(self.selling_price)
        self.buying_cost = to_decimal(self.buying_cost)

        self.line_cost_cache = to_decimal(self.buying_cost) * int(self.quantity or 0)

        super().save(*args, **kwargs)

        if self.invoice_id:
            self.invoice.calculate_totals(save=True)

    def delete(self, *args, **kwargs):
        inv = self.invoice
        super().delete(*args, **kwargs)
        if inv:
            inv.calculate_totals(save=True)


# ───────────────────────────────────────────────────────────────
# Payment (supports partial payments for debt)
# ───────────────────────────────────────────────────────────────
class Payment(models.Model):
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="payments",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_payments",
    )

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
        if self.invoice_id:
            self.invoice.update_paid_status(save=True)

    def delete(self, *args, **kwargs):
        inv = self.invoice
        super().delete(*args, **kwargs)
        if inv:
            inv.update_paid_status(save=True)


# ───────────────────────────────────────────────────────────────
# Expense
# ───────────────────────────────────────────────────────────────
class Expense(models.Model):
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
    category = models.CharField(max_length=50)
    description = models.CharField(max_length=255, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
    )

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["branch", "date"]),
        ]

    def __str__(self):
        return f"{self.date}: {self.category} – Tsh {to_decimal(self.amount):.2f}"


# ───────────────────────────────────────────────────────────────
# ✅ STOCK TRANSFER (Branch → Branch) using inventory.ProductStock
# ───────────────────────────────────────────────────────────────
class StockTransfer(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_POSTED = "posted"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_POSTED, "Posted"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    from_branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="stock_transfers_out",
        db_index=True,
    )
    to_branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="stock_transfers_in",
        db_index=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="stock_transfers_created",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_transfers_posted",
    )
    posted_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )

    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["from_branch", "created_at"]),
            models.Index(fields=["to_branch", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"Transfer #{self.id} {self.from_branch} → {self.to_branch}"

    def clean(self):
        if self.from_branch_id and self.to_branch_id and self.from_branch_id == self.to_branch_id:
            raise ValidationError("From branch and To branch must be different.")

    @property
    def items_total_qty(self) -> int:
        return int(self.items.aggregate(s=Sum("quantity"))["s"] or 0)

    def post(self, user):
        """
        Finalize transfer (atomic):
          - Decrease stock from from_branch (inventory.ProductStock)
          - Increase stock to to_branch (inventory.ProductStock)
        """
        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Only draft transfers can be posted.")

        if self.from_branch_id == self.to_branch_id:
            raise ValidationError("From branch and To branch must be different.")

        items = list(self.items.select_related("product").all())
        if not items:
            raise ValidationError("Add at least one item to transfer.")

        with transaction.atomic():
            # validate enough stock in from_branch
            for it in items:
                if it.quantity <= 0:
                    raise ValidationError("Invalid item quantity.")
                available = get_branch_qty(it.product_id, self.from_branch_id)
                if available < it.quantity:
                    raise ValidationError(
                        f"Not enough stock for {it.product.name}. Available {available}, requested {it.quantity}"
                    )

            # apply movements
            for it in items:
                add_branch_qty(it.product_id, self.from_branch_id, -it.quantity)
                add_branch_qty(it.product_id, self.to_branch_id, +it.quantity)

            self.status = self.STATUS_POSTED
            self.posted_by = user
            self.posted_at = timezone.now()
            self.save(update_fields=["status", "posted_by", "posted_at"])


class StockTransferItem(models.Model):
    transfer = models.ForeignKey(
        StockTransfer,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ("transfer", "product")
        indexes = [
            models.Index(fields=["transfer", "product"]),
        ]

    def __str__(self):
        return f"{self.product} x {self.quantity}"


# sales/models.py
from django.db import models
from django.conf import settings
from django.db.models import Sum
from decimal import Decimal

class Debtor(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=50, blank=True, null=True)
    location = models.CharField(max_length=120, blank=True, null=True)

    amount_owed = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    note = models.TextField(blank=True, null=True)

    is_active = models.BooleanField(default=True)  # Active = still owes

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_debtors"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.name

    def total_paid(self):
        total = self.payments.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
        return Decimal(total)

    def balance(self):
        bal = Decimal(self.amount_owed or 0) - self.total_paid()
        return bal if bal > 0 else Decimal("0.00")


class DebtorPayment(models.Model):
    debtor = models.ForeignKey(Debtor, related_name="payments", on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    method = models.CharField(max_length=20, blank=True, null=True)  # cash / bank / ebt etc optional
    note = models.CharField(max_length=255, blank=True, null=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.debtor.name} paid {self.amount}"
