from django.db import models, transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.utils import timezone
import uuid


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
        """
        Default branch used when StockEntry.branch is not provided.
        Prefer active branch.
        """
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
    name = models.CharField(max_length=255)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)

    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Cached total stock across branches (auto-updated)
    stock = models.IntegerField(default=0, help_text="(Auto) Total stock across branches — do not edit manually.")

    barcode = models.CharField(max_length=50, unique=True, blank=True)
    reorder_level = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.name

    def _generate_unique_barcode(self):
        """
        Generate numeric-ish unique barcode if missing.
        """
        for _ in range(10):
            code = str(uuid.uuid4().int)[:12]
            if not Product.objects.filter(barcode=code).exists():
                return code
        # extreme fallback
        return str(uuid.uuid4().int)[:12]

    @staticmethod
    def recalc_total_stock(product_id: int) -> int:
        """
        Recalculate total stock from ProductStock and update Product.stock cache.
        Returns total.
        """
        total = ProductStock.objects.filter(product_id=product_id).aggregate(
            total=Sum("quantity")
        )["total"] or 0
        Product.objects.filter(pk=product_id).update(stock=total)
        return int(total)

    def save(self, *args, **kwargs):
        if not self.barcode:
            self.barcode = self._generate_unique_barcode()

        super().save(*args, **kwargs)

        # keep cached total stock synced (safe, uses update() to avoid recursion)
        Product.recalc_total_stock(self.pk)


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

        # update cached Product.stock
        Product.recalc_total_stock(self.product_id)


# -------------------------
# StockEntry (history) (branch-aware)
# -------------------------
class StockEntry(models.Model):
    """
    Records every change in stock (branch-aware).
    IMPORTANT:
    - Creating a StockEntry will automatically adjust ProductStock.quantity.
    - Do NOT manually adjust ProductStock elsewhere if you also create StockEntry.
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="stock_entries")
    change = models.IntegerField(help_text="Positive if stock was added, negative if removed")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, null=True, blank=True, related_name="stock_entries")
    timestamp = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-timestamp", "-id"]

    def __str__(self):
        sign = "+" if self.change >= 0 else ""
        branch_name = self.branch.name if self.branch else "Auto"
        return f"{self.product.name} ({branch_name}): {sign}{self.change} @ {self.timestamp:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        """
        Apply stock movement ONLY on create (new entry).
        Prevent editing change/product/branch after created (it breaks stock).
        """
        is_new = self._state.adding

        if not is_new:
            old = StockEntry.objects.get(pk=self.pk)
            # allow editing note/timestamp only
            if old.change != self.change or old.product_id != self.product_id or old.branch_id != self.branch_id:
                raise ValidationError(
                    "You cannot edit product/branch/change of an existing StockEntry. "
                    "Create a new StockEntry adjustment instead."
                )
            return super().save(*args, **kwargs)

        # if branch not set, auto-pick default branch (keeps system consistent)
        if self.branch_id is None:
            self.branch = Branch.get_default_branch()

        # Apply stock in ONE atomic transaction
        with transaction.atomic():
            super().save(*args, **kwargs)

            # If we still have no branch (no branches exist), fallback to cached Product.stock
            if not self.branch:
                # Protect from negative
                p = Product.objects.select_for_update().get(pk=self.product_id)
                new_total = int(p.stock) + int(self.change)
                if new_total < 0:
                    raise ValidationError(
                        f"Insufficient total stock for '{p.name}'. Cannot apply change {self.change}."
                    )
                Product.objects.filter(pk=p.pk).update(stock=new_total)
                return

            # Branch stock update (protected)
            ps, _ = ProductStock.objects.select_for_update().get_or_create(
                product_id=self.product_id,
                branch_id=self.branch_id,
                defaults={"quantity": 0},
            )
            new_qty = int(ps.quantity) + int(self.change)
            if new_qty < 0:
                raise ValidationError(
                    f"Insufficient stock in branch '{self.branch.name}' for product '{self.product.name}'. "
                    f"Available: {ps.quantity}, change: {self.change}"
                )

            ps.quantity = new_qty
            ps.save()

    def delete(self, *args, **kwargs):
        """
        Optional safety: deleting a StockEntry reverses its effect.
        (Keeps stock consistent if admin deletes an entry.)
        """
        with transaction.atomic():
            # reverse effect only if branch exists
            branch = self.branch or Branch.get_default_branch()

            if branch:
                ps = ProductStock.objects.select_for_update().filter(product=self.product, branch=branch).first()
                if ps:
                    new_qty = int(ps.quantity) - int(self.change)
                    if new_qty < 0:
                        raise ValidationError("Cannot delete this StockEntry because it would make stock negative.")
                    ps.quantity = new_qty
                    ps.save()
            else:
                # fallback cached total
                p = Product.objects.select_for_update().get(pk=self.product_id)
                new_total = int(p.stock) - int(self.change)
                if new_total < 0:
                    raise ValidationError("Cannot delete this StockEntry because it would make total stock negative.")
                Product.objects.filter(pk=p.pk).update(stock=new_total)

            return super().delete(*args, **kwargs)
