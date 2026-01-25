from django.contrib import admin
from .models import Invoice, InvoiceItem, Customer, Payment, Expense


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone")
    search_fields = ("name", "phone")


# ✅ Inlines
class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0

    # ✅ Use REAL model fields, not property "unit_price"
    # InvoiceItem has: product, quantity, selling_price, buying_cost, discount, line_cost_cache
    readonly_fields = ("product", "quantity", "selling_price", "buying_cost", "discount", "line_total_display", "line_cost_display")
    fields = ("product", "quantity", "selling_price", "discount", "line_total_display", "buying_cost", "line_cost_display")

    # ✅ show totals as admin "methods"
    @admin.display(description="Line Total (TZS)")
    def line_total_display(self, obj):
        try:
            return int(obj.line_total)
        except Exception:
            return 0

    @admin.display(description="Line Cost (TZS)")
    def line_cost_display(self, obj):
        try:
            return int(obj.line_cost)
        except Exception:
            return 0


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ("timestamp", "created_by")
    fields = ("amount", "timestamp", "created_by")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "seller", "status", "paid", "total_amount", "payments_total_display", "balance_display", "created_at", "branch")
    search_fields = ("id", "customer__name", "seller__username")
    ordering = ("-created_at",)

    # ✅ include both items & payments so debt history is visible
    inlines = (InvoiceItemInline, PaymentInline)

    list_filter = (
        "status",
        "paid",
        "branch",
        ("created_at", admin.DateFieldListFilter),
    )
    date_hierarchy = "created_at"

    @admin.display(description="Paid (TZS)")
    def payments_total_display(self, obj):
        try:
            return int(obj.payments_total)
        except Exception:
            return 0

    @admin.display(description="Balance (TZS)")
    def balance_display(self, obj):
        try:
            return int(obj.balance)
        except Exception:
            return 0


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("invoice", "amount", "timestamp", "created_by")
    search_fields = ("invoice__id", "invoice__customer__name", "invoice__seller__username")
    list_filter = (("timestamp", admin.DateFieldListFilter),)
    date_hierarchy = "timestamp"


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("date", "amount", "category", "created_by", "branch")
    search_fields = ("category", "description")
    list_filter = ("category", "branch", ("date", admin.DateFieldListFilter))
    date_hierarchy = "date"
