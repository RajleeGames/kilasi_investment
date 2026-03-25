from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from django.urls import reverse
from django.utils import timezone

from inventory.models import Product, Branch, ProductStock, StockEntry


# ───────────────────────────────────────────────────────────────
# Helpers
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
# Branch stock helpers
# SOURCE OF TRUTH = inventory.ProductStock
# ───────────────────────────────────────────────────────────────
def get_branch_qty(product_id: int, branch_id: int) -> int:
    obj = ProductStock.objects.filter(product_id=product_id, branch_id=branch_id).first()
    return int(getattr(obj, "quantity", 0) or 0)


def set_branch_qty(product_id: int, branch_id: int, new_qty: int):
    new_qty = max(0, int(new_qty or 0))
    obj, _ = ProductStock.objects.get_or_create(product_id=product_id, branch_id=branch_id)
    obj.quantity = new_qty
    obj.save(update_fields=["quantity"])


def add_branch_qty(product_id: int, branch_id: int, delta: int):
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
    PAYMENT_EBT = "ebt"
    PAYMENT_DEBT = "debt"

    PAYMENT_CHOICES = [
        (PAYMENT_CASH, "Cash"),
        (PAYMENT_BANK, "Bank"),
        (PAYMENT_EBT, "EBT"),
        (PAYMENT_DEBT, "Debt"),
    ]

    INVOICE_ACTIVE = "active"
    INVOICE_CANCELLED = "cancelled"
    INVOICE_STATE_CHOICES = [
        (INVOICE_ACTIVE, "Active"),
        (INVOICE_CANCELLED, "Cancelled"),
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

    # payment method
    status = models.CharField(
        max_length=10,
        choices=PAYMENT_CHOICES,
        default=PAYMENT_DEBT,
        db_index=True,
    )

    # lifecycle
    invoice_state = models.CharField(
        max_length=20,
        choices=INVOICE_STATE_CHOICES,
        default=INVOICE_ACTIVE,
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
            models.Index(fields=["invoice_state", "created_at"]),
        ]

    def __str__(self):
        return f"Invoice #{self.pk} — {self.get_status_display()}"

    def get_absolute_url(self):
        return reverse("sales:invoice_detail", kwargs={"pk": self.pk})

    def is_cancelled(self):
        return self.invoice_state == self.INVOICE_CANCELLED

    def returns_total_decimal(self) -> Decimal:
        try:
            t = self.returns.filter(status=SalesReturn.STATUS_POSTED).aggregate(
                s=Sum("total_amount")
            )["s"] or Decimal("0.00")
            return to_decimal(t)
        except Exception:
            return Decimal("0.00")

    def total_effective_decimal(self) -> Decimal:
        base = to_decimal(self.total_amount)
        eff = base - self.returns_total_decimal()
        if eff < Decimal("0.00"):
            eff = Decimal("0.00")
        return eff

    def total_effective_tsh(self) -> int:
        return to_int_tsh(self.total_effective_decimal())

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
        try:
            paid_sum = self.payments.aggregate(t=Sum("amount"))["t"] or 0
            return int(paid_sum)
        except Exception:
            return 0

    def balance_tsh(self) -> int:
        if self.invoice_state == self.INVOICE_CANCELLED:
            return 0
        if self.status != self.PAYMENT_DEBT:
            return 0
        total_int = self.total_effective_tsh()
        return max(0, total_int - self.payments_total_tsh())

    @property
    def payments_total(self) -> int:
        return self.payments_total_tsh()

    @property
    def balance(self) -> int:
        return self.balance_tsh()

    @property
    def returns_total(self) -> int:
        return to_int_tsh(self.returns_total_decimal())

    @property
    def total_effective(self) -> int:
        return self.total_effective_tsh()

    def update_paid_status(self, save=True):
        if self.invoice_state == self.INVOICE_CANCELLED:
            new_paid = False
        elif self.status in (self.PAYMENT_CASH, self.PAYMENT_BANK, self.PAYMENT_EBT):
            new_paid = True
        else:
            new_paid = (self.balance_tsh() <= 0)

        if self.paid != new_paid:
            self.paid = new_paid
            if save:
                self.save(update_fields=["paid"])

    def cancel_invoice(self, user=None):
        if self.invoice_state == self.INVOICE_CANCELLED:
            raise ValidationError("This invoice is already cancelled.")

        if not self.branch_id:
            raise ValidationError("Invoice branch is missing, cannot restore stock.")

        with transaction.atomic():
            Invoice.objects.select_for_update().filter(pk=self.pk).exists()

            posted_returns = self.returns.filter(status=SalesReturn.STATUS_POSTED).exists()
            if posted_returns:
                raise ValidationError(
                    "Cannot cancel this invoice because it already has posted returns."
                )

            for item in self.items.select_related("product").all():
                qty = int(item.quantity or 0)
                if qty > 0:
                    # restore stock ONLY ONCE
                    StockEntry.objects.create(
                        product=item.product,
                        branch_id=self.branch_id,
                        change=qty,
                        note=f"Stock restored from cancelled Invoice #{self.pk}",
                    )

            self.payments.all().delete()

            self.invoice_state = self.INVOICE_CANCELLED
            self.paid = False
            self.save(update_fields=["invoice_state", "paid"])

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
# Payment
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
# Stock Transfer
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
        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Only draft transfers can be posted.")

        if self.from_branch_id == self.to_branch_id:
            raise ValidationError("From branch and To branch must be different.")

        items = list(self.items.select_related("product").all())
        if not items:
            raise ValidationError("Add at least one item to transfer.")

        with transaction.atomic():
            for it in items:
                if it.quantity <= 0:
                    raise ValidationError("Invalid item quantity.")
                available = get_branch_qty(it.product_id, self.from_branch_id)
                if available < it.quantity:
                    raise ValidationError(
                        f"Not enough stock for {it.product.name}. Available {available}, requested {it.quantity}"
                    )

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


# ───────────────────────────────────────────────────────────────
# Debtor
# ───────────────────────────────────────────────────────────────
class Debtor(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=50, blank=True, null=True)
    location = models.CharField(max_length=120, blank=True, null=True)

    amount_owed = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    note = models.TextField(blank=True, null=True)

    is_active = models.BooleanField(default=True)

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
    method = models.CharField(max_length=20, blank=True, null=True)
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


# ───────────────────────────────────────────────────────────────
# Sales Return
# ───────────────────────────────────────────────────────────────
class SalesReturn(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_POSTED = "posted"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_POSTED, "Posted"),
    )

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="returns"
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="sales_returns"
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    note = models.TextField(blank=True, default="")

    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="returns_created"
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="returns_posted"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]

    def recompute_total(self):
        t = self.items.aggregate(s=Sum("line_total"))["s"] or Decimal("0.00")
        self.total_amount = t
        self.save(update_fields=["total_amount"])
        return t

    def post(self, user):
        """
        Posting does:
        1) Validate return quantities
        2) Add stock back to branch
        3) Mark return posted
        4) Update invoice paid status
        """
        if self.status == self.STATUS_POSTED:
            raise ValueError("This return is already posted.")

        inv = self.invoice

        inv_branch = getattr(inv, "branch", None)
        if not self.branch:
            self.branch = inv_branch

        if not self.branch:
            raise ValueError("Invoice branch not found. Set return.branch or invoice.branch.")

        with transaction.atomic():
            Invoice.objects.select_for_update().filter(pk=inv.pk).exists()

            rows = list(self.items.select_related("product"))
            if not rows:
                raise ValueError("Add at least one return item.")

            for r in rows:
                sold_qty = (
                    InvoiceItem.objects
                    .filter(invoice=inv, product=r.product)
                    .aggregate(s=Sum("quantity"))["s"] or 0
                )

                already_returned = (
                    SalesReturnItem.objects
                    .filter(
                        sales_return__invoice=inv,
                        sales_return__status=SalesReturn.STATUS_POSTED,
                        product=r.product
                    )
                    .aggregate(s=Sum("quantity"))["s"] or 0
                )

                # exclude current draft return items from already returned when posting first time
                if self.pk:
                    current_draft_qty = (
                        SalesReturnItem.objects
                        .filter(
                            sales_return=self,
                            product=r.product
                        )
                        .aggregate(s=Sum("quantity"))["s"] or 0
                    )
                    already_returned = max(0, int(already_returned) - int(current_draft_qty))

                available = int(sold_qty) - int(already_returned)

                if r.quantity <= 0:
                    raise ValueError(f"Invalid qty for {r.product.name}.")
                if r.quantity > available:
                    raise ValueError(
                        f"Return qty too high for '{r.product.name}'. "
                        f"Sold: {sold_qty}, Already returned: {already_returned}, Available to return: {available}"
                    )

                # IMPORTANT:
                # restore stock ONLY ONCE through StockEntry
                # do NOT also call add_branch_qty here
                StockEntry.objects.create(
                    product=r.product,
                    branch=self.branch,
                    change=+int(r.quantity),
                    note=f"Return #{self.id or 'draft'} for Invoice #{inv.id}"
                )

            self.recompute_total()
            self.status = self.STATUS_POSTED
            self.posted_by = user
            self.posted_at = timezone.now()
            self.save(update_fields=["status", "posted_by", "posted_at", "branch", "total_amount"])

            inv.update_paid_status(save=True)


class SalesReturnItem(models.Model):
    sales_return = models.ForeignKey(
        SalesReturn,
        on_delete=models.CASCADE,
        related_name="items"
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)

    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    discount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["id"]

    def save(self, *args, **kwargs):
        price = to_decimal(self.unit_price) - to_decimal(self.discount)
        if price < 0:
            price = Decimal("0.00")
        self.line_total = (price * Decimal(int(self.quantity or 0))).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

        if self.sales_return_id:
            self.sales_return.recompute_total()

    def delete(self, *args, **kwargs):
        sr = self.sales_return
        super().delete(*args, **kwargs)
        if sr:
            sr.recompute_total()

# ───────────────────────────────────────────────────────────────
# Genji Sale
# ───────────────────────────────────────────────────────────────
class GenjiSale(models.Model):
    customer_name = models.CharField(max_length=150, blank=True, default="")
    customer_phone = models.CharField(max_length=50, blank=True, default="")
    item_name = models.CharField(max_length=200)
    notes = models.TextField(blank=True, default="")

    buy_date = models.DateField()
    sell_date = models.DateField()

    buying_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    selling_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    quantity = models.PositiveIntegerField(default=1)

    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="genji_sales"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="genji_sales_created"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-sell_date", "-id"]

    def __str__(self):
        return f"Genji #{self.id} - {self.item_name}"

    @property
    def total_buying(self):
        return Decimal(self.buying_cost or 0) * Decimal(self.quantity or 0)

    @property
    def total_selling(self):
        return Decimal(self.selling_price or 0) * Decimal(self.quantity or 0)

    @property
    def profit(self):
        return self.total_selling - self.total_buying
    
    
    
    from decimal import Decimal
from django.conf import settings
from django.db import models


class MyDebt(models.Model):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=50, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    amount_owed = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_my_debts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.name

    def total_paid(self):
        total = self.payments.aggregate(t=models.Sum("amount"))["t"] or Decimal("0.00")
        return total

    def balance(self):
        bal = Decimal(str(self.amount_owed or 0)) - Decimal(str(self.total_paid() or 0))
        return bal if bal > 0 else Decimal("0.00")


class MyDebtPayment(models.Model):
    debt = models.ForeignKey(
        MyDebt,
        on_delete=models.CASCADE,
        related_name="payments"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=255, blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="my_debt_payments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.debt.name} - {self.amount}"