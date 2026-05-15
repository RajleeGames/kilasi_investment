from django.contrib import admin

from .models import CustomerOrder, CustomerOrderPayment


class CustomerOrderPaymentInline(admin.TabularInline):
    model = CustomerOrderPayment
    extra = 0
    readonly_fields = ["paid_at"]


@admin.register(CustomerOrder)
class CustomerOrderAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "customer_name",
        "customer_phone",
        "product_name",
        "total_amount",
        "paid_amount",
        "balance",
        "order_status",
        "payment_status",
        "branch",
        "created_at",
    ]
    list_filter = ["order_status", "payment_status", "branch", "created_at"]
    search_fields = ["customer_name", "customer_phone", "custom_product_name", "description"]
    inlines = [CustomerOrderPaymentInline]


@admin.register(CustomerOrderPayment)
class CustomerOrderPaymentAdmin(admin.ModelAdmin):
    list_display = ["id", "order", "amount", "method", "received_by", "paid_at"]
    list_filter = ["method", "paid_at"]
    search_fields = ["order__customer_name", "order__customer_phone"]