from decimal import Decimal
import uuid

from django.db import models, transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models.signals import post_delete
from django.dispatch import receiver


# -------------------------
# Branch model
# -------------------------
class Branch(models.Model):
    name = models.CharField(max_length=120, unique=True)
    code = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @classmethod
    def get_default_branch(cls):
        return (
            cls.objects.filter(is_active=True).order_by("id").first()
            or cls.objects.order_by("id").first()
        )


# -------------------------
# Category / Supplier / Product
# -------------------------
class Category(models.Model):
    name = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField(max_length=255)
    contact = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    BUSINESS_TYPE_ELECTRONICS = "electronics"
    BUSINESS_TYPE_FURNITURE = "furniture"
    BUSINESS_TYPE_MAGODORO = "magodoro"
    BUSINESS_TYPE_UNASSIGNED = "unassigned"

    BUSINESS_TYPE_CHOICES = [
        (BUSINESS_TYPE_ELECTRONICS, "Electronics"),
        (BUSINESS_TYPE_FURNITURE, "Furniture"),
        (BUSINESS_TYPE_MAGODORO, "Magodoro"),
        (BUSINESS_TYPE_UNASSIGNED, "Unassigned"),
    ]

    name = models.CharField(max_length=255)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)

    business_type = models.CharField(
        max_length=20,
        choices=BUSINESS_TYPE_CHOICES,
        default=BUSINESS_TYPE_UNASSIGNED,
        db_index=True,
    )

    buying_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    stock = models.IntegerField(
        default=0,
        help_text="(Auto) Total stock across branches — do not edit manually."
    )

    barcode = models.CharField(max_length=50, unique=True, blank=True)
    reorder_level = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.name

    def profit_per_unit(self):
        return (self.selling_price or Decimal("0.00")) - (self.buying_price or Decimal("0.00"))

    def _generate_unique_barcode(self):
        for _ in range(10):
            code = str(uuid.uuid4().int)[:12]
            if not Product.objects.filter(barcode=code).exists():
                return code
        return str(uuid.uuid4().int)[:12]

    @staticmethod
    def recalc_total_stock(product_id: int) -> int:
        total = ProductStock.objects.filter(product_id=product_id).aggregate(
            total=Sum("quantity")
        )["total"] or 0
        Product.objects.filter(pk=product_id).update(stock=int(total))
        return int(total)

    def save(self, *args, **kwargs):
        if not self.barcode:
            self.barcode = self._generate_unique_barcode()
        super().save(*args, **kwargs)


# -------------------------
# Per-branch stock
# -------------------------
class ProductStock(models.Model):
    """
    Holds quantity of a product per branch.
    Unique per (product, branch).
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="branch_stocks")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="product_stocks")
    quantity = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "branch"], name="uniq_product_branch_stock")
        ]
        ordering = ["product_id", "branch_id"]

    def __str__(self):
        return f"{self.product.name} @ {self.branch.name}: {self.quantity}"

    def clean(self):
        if self.quantity < 0:
            raise ValidationError("Branch stock quantity cannot be negative.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        Product.recalc_total_stock(self.product_id)


# -------------------------
# Signal: recalc cached total when ProductStock is deleted
# -------------------------
@receiver(post_delete, sender=ProductStock)
def recalc_product_stock_after_delete(sender, instance, **kwargs):
    Product.recalc_total_stock(instance.product_id)


# -------------------------
# StockEntry (history + ONLY place that changes stock)
# -------------------------
class StockEntry(models.Model):
    """
    Records every change in stock.

    RULES:
    - Creating a StockEntry adjusts ProductStock.quantity ONLY HERE.
    - Editing existing entry's product/branch/change is FORBIDDEN.
    - Deleting a StockEntry reverses its effect.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="stock_entries")
    change = models.IntegerField(help_text="Positive if stock was added, negative if removed")
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_entries"
    )
    timestamp = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-timestamp", "-id"]

    def __str__(self):
        sign = "+" if self.change >= 0 else ""
        branch_name = self.branch.name if self.branch else "Auto"
        return f"{self.product.name} ({branch_name}): {sign}{self.change} @ {self.timestamp:%Y-%m-%d %H:%M}"

    def _effective_branch(self):
        if self.branch_id:
            return self.branch
        return Branch.get_default_branch()

    def save(self, *args, **kwargs):
        is_new = self._state.adding

        if not is_new:
            old = StockEntry.objects.get(pk=self.pk)
            if (
                old.change != self.change
                or old.product_id != self.product_id
                or old.branch_id != self.branch_id
            ):
                raise ValidationError(
                    "You cannot edit product/branch/change of an existing StockEntry. "
                    "Create a new StockEntry adjustment instead."
                )
            return super().save(*args, **kwargs)

        self.branch = self._effective_branch()
        if not self.branch:
            raise ValidationError("No branch found. Create at least one Branch before adding stock.")

        with transaction.atomic():
            super().save(*args, **kwargs)

            ps, _ = ProductStock.objects.select_for_update().get_or_create(
                product_id=self.product_id,
                branch_id=self.branch_id,
                defaults={"quantity": 0},
            )

            new_qty = int(ps.quantity) + int(self.change)
            if new_qty < 0:
                raise ValidationError(
                    f"Insufficient stock in branch '{self.branch.name}' for '{self.product.name}'. "
                    f"Available: {ps.quantity}, change: {self.change}"
                )

            ps.quantity = new_qty
            ps.save(update_fields=["quantity"])

    def delete(self, *args, **kwargs):
        branch = self._effective_branch()
        if not branch:
            raise ValidationError("Cannot delete StockEntry because no branch exists.")

        with transaction.atomic():
            ps = ProductStock.objects.select_for_update().filter(
                product=self.product,
                branch=branch
            ).first()

            if not ps:
                raise ValidationError("Cannot delete: branch stock row is missing.")

            new_qty = int(ps.quantity) - int(self.change)
            if new_qty < 0:
                raise ValidationError("Cannot delete this StockEntry because it would make stock negative.")

            ps.quantity = new_qty
            ps.save(update_fields=["quantity"])

            return super().delete(*args, **kwargs)