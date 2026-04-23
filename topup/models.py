from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from inventory.models import Product, Branch, ProductStock, StockEntry
from sales.models import Customer


def to_decimal(value, default=Decimal("0.00")):
    try:
        if value is None:
            return default
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def to_int_tsh(value):
    d = to_decimal(value, Decimal("0.00"))
    try:
        return int(d)
    except Exception:
        return 0


class TopupTransaction(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="topup_transactions",
        db_index=True,
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="topup_transactions",
    )

    # new item leaving normal stock
    new_product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="topup_outgoing_transactions",
    )
    new_product_qty = models.PositiveIntegerField(default=1)

    # used item coming in
    used_item_name = models.CharField(max_length=255)
    used_item_category = models.CharField(max_length=255, blank=True)
    used_item_condition = models.CharField(max_length=255, blank=True)

    # what Kilasi accepts as value/cost of the used item
    used_item_buying_price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    # cash added by customer during top-up
    addon_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    note = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_COMPLETED, db_index=True)

    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_topup_transactions",
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Top-up #{self.pk} - {self.used_item_name}"

    @property
    def addon_amount_int(self):
        return to_int_tsh(self.addon_amount)

    @property
    def used_item_buying_price_int(self):
        return to_int_tsh(self.used_item_buying_price)

    def clean(self):
        if self.new_product_qty < 1:
            raise ValidationError("Quantity must be at least 1.")
        if to_decimal(self.addon_amount) < 0:
            raise ValidationError("Addon amount cannot be negative.")
        if to_decimal(self.used_item_buying_price) < 0:
            raise ValidationError("Used item buying price cannot be negative.")

    def get_absolute_url(self):
        return reverse("topup:transaction_detail", args=[self.pk])


class UsedTopupStock(models.Model):
    STATUS_AVAILABLE = "available"
    STATUS_SOLD = "sold"
    STATUS_CHOICES = [
        (STATUS_AVAILABLE, "Available"),
        (STATUS_SOLD, "Sold"),
    ]

    transaction = models.OneToOneField(
        TopupTransaction,
        on_delete=models.CASCADE,
        related_name="used_stock",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="used_topup_items",
        db_index=True,
    )

    item_name = models.CharField(max_length=255)
    item_category = models.CharField(max_length=255, blank=True)
    condition = models.CharField(max_length=255, blank=True)

    buying_price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    asking_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    sold_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_AVAILABLE, db_index=True)
    note = models.TextField(blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    sold_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.item_name} ({self.get_status_display()})"

    @property
    def profit(self):
        if self.sold_price is None:
            return Decimal("0.00")
        return to_decimal(self.sold_price) - to_decimal(self.buying_price)

    @property
    def profit_int(self):
        return to_int_tsh(self.profit)

    def mark_sold(self, sold_price, note=""):
        self.sold_price = to_decimal(sold_price)
        self.status = self.STATUS_SOLD
        self.sold_at = timezone.now()
        if note:
            self.note = f"{self.note}\n{note}".strip()
        self.save(update_fields=["sold_price", "status", "sold_at", "note"])


class UsedTopupSale(models.Model):
    used_stock = models.OneToOneField(
        UsedTopupStock,
        on_delete=models.CASCADE,
        related_name="sale",
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="used_topup_purchases",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="used_topup_sales",
        db_index=True,
    )
    sold_price = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.TextField(blank=True)
    sold_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="used_topup_sales_created",
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Used sale #{self.pk} - {self.used_stock.item_name}"

    @property
    def profit(self):
        return to_decimal(self.sold_price) - to_decimal(self.used_stock.buying_price)

    @property
    def profit_int(self):
        return to_int_tsh(self.profit)


def create_topup_transaction(
    *,
    branch,
    customer,
    new_product,
    new_product_qty,
    used_item_name,
    used_item_category,
    used_item_condition,
    used_item_buying_price,
    addon_amount,
    created_by,
    note="",
):
    """
    Atomic helper:
    1. Validate available normal stock
    2. Create TopupTransaction
    3. Create ONE StockEntry with negative change
       (this is what reduces ProductStock in this project)
    4. Create UsedTopupStock
    """
    with transaction.atomic():
        ps = (
            ProductStock.objects.select_for_update()
            .filter(product=new_product, branch=branch)
            .first()
        )

        available = int(getattr(ps, "quantity", 0) or 0)
        qty = int(new_product_qty or 0)

        if qty < 1:
            raise ValidationError("Quantity must be at least 1.")

        if not ps:
            raise ValidationError(
                f"No stock record found for '{new_product.name}' in {branch.name}."
            )

        if qty > available:
            raise ValidationError(
                f"Insufficient stock for '{new_product.name}' in {branch.name}. Available: {available}"
            )

        tx = TopupTransaction.objects.create(
            branch=branch,
            customer=customer,
            new_product=new_product,
            new_product_qty=qty,
            used_item_name=used_item_name,
            used_item_category=used_item_category,
            used_item_condition=used_item_condition,
            used_item_buying_price=to_decimal(used_item_buying_price),
            addon_amount=to_decimal(addon_amount),
            note=note,
            status=TopupTransaction.STATUS_COMPLETED,
            created_by=created_by,
        )

        # IMPORTANT:
        # Do NOT manually reduce ProductStock here.
        # StockEntry.save() already adjusts ProductStock quantity in your inventory system.
        StockEntry.objects.create(
            product=new_product,
            branch=branch,
            change=-qty,
            note=f"Top-up TXN #{tx.pk} - exchanged for used item: {used_item_name}",
        )

        UsedTopupStock.objects.create(
            transaction=tx,
            branch=branch,
            item_name=used_item_name,
            item_category=used_item_category,
            condition=used_item_condition,
            buying_price=to_decimal(used_item_buying_price),
            note=f"Received from Top-up TXN #{tx.pk}",
        )

        return tx