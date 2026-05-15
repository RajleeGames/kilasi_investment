from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from inventory.models import Branch, Product


class CustomerOrder(models.Model):
    ORDER_STATUS_PENDING = "pending"
    ORDER_STATUS_IN_PROGRESS = "in_progress"
    ORDER_STATUS_COMPLETED = "completed"
    ORDER_STATUS_CANCELLED = "cancelled"

    ORDER_STATUS_CHOICES = [
        (ORDER_STATUS_PENDING, "Pending"),
        (ORDER_STATUS_IN_PROGRESS, "In Progress"),
        (ORDER_STATUS_COMPLETED, "Completed"),
        (ORDER_STATUS_CANCELLED, "Cancelled"),
    ]

    PAYMENT_STATUS_UNPAID = "unpaid"
    PAYMENT_STATUS_PARTIAL = "partial"
    PAYMENT_STATUS_PAID = "paid"

    PAYMENT_STATUS_CHOICES = [
        (PAYMENT_STATUS_UNPAID, "Unpaid"),
        (PAYMENT_STATUS_PARTIAL, "Partial"),
        (PAYMENT_STATUS_PAID, "Paid"),
    ]

    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=30, blank=True)

    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_orders",
        help_text="Optional. Choose product type like bed, sofa, table etc."
    )

    custom_product_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="Use this if product is not already in inventory."
    )

    description = models.TextField(
        help_text="Example: 6x6 bed, black color, modern design, drawer included."
    )

    quantity = models.PositiveIntegerField(default=1)

    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    order_status = models.CharField(
        max_length=20,
        choices=ORDER_STATUS_CHOICES,
        default=ORDER_STATUS_PENDING
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default=PAYMENT_STATUS_UNPAID
    )

    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_orders"
    )

    expected_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_customer_orders"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        product_name = self.product_name
        return f"Order #{self.id} - {self.customer_name} - {product_name}"

    @property
    def product_name(self):
        if self.product:
            return self.product.name
        return self.custom_product_name or "Custom Order"

    @property
    def paid_amount(self):
        total = self.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        return total

    @property
    def balance(self):
        bal = self.total_amount - self.paid_amount
        if bal < 0:
            return Decimal("0.00")
        return bal

    def refresh_payment_status(self):
        paid = self.paid_amount

        if paid <= 0:
            self.payment_status = self.PAYMENT_STATUS_UNPAID
        elif paid < self.total_amount:
            self.payment_status = self.PAYMENT_STATUS_PARTIAL
        else:
            self.payment_status = self.PAYMENT_STATUS_PAID

        self.save(update_fields=["payment_status", "updated_at"])

    def mark_completed(self):
        self.order_status = self.ORDER_STATUS_COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=["order_status", "completed_at", "updated_at"])


class CustomerOrderPayment(models.Model):
    METHOD_CASH = "cash"
    METHOD_BANK = "bank"
    METHOD_EBT = "ebt"

    METHOD_CHOICES = [
        (METHOD_CASH, "Cash"),
        (METHOD_BANK, "Bank"),
        (METHOD_EBT, "EBT"),
    ]

    order = models.ForeignKey(
        CustomerOrder,
        on_delete=models.CASCADE,
        related_name="payments"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default=METHOD_CASH)
    note = models.CharField(max_length=255, blank=True)

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    paid_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.order} - {self.amount}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.order.refresh_payment_status()