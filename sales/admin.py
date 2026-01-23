from django.contrib import admin
from .models import Invoice, InvoiceItem, Customer, Payment, Expense

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display  = ('name', 'phone')
    search_fields = ('name', 'phone')


class InvoiceItemInline(admin.TabularInline):
    model           = InvoiceItem
    extra           = 0
    readonly_fields = ('product', 'quantity', 'unit_price', 'discount')


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display  = ('id', 'customer', 'seller', 'status', 'paid', 'total_amount', 'created_at')
    search_fields = ('id', 'customer__name', 'seller__username')
    ordering      = ('-created_at',)
    inlines       = (InvoiceItemInline,)

    list_filter = (
        'status',
        'paid',
        ('created_at', admin.DateFieldListFilter),  # Built-in Django filter
    )
    date_hierarchy = 'created_at'  # Adds clickable date navigation at top


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display  = ('invoice', 'amount', 'timestamp')
    search_fields = ('invoice__id',)
    list_filter   = (('timestamp', admin.DateFieldListFilter),)
    date_hierarchy = 'timestamp'


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display  = ('date', 'amount', 'category', 'created_by')
    search_fields = ('category', 'description')
    list_filter   = ('category', ('date', admin.DateFieldListFilter))
    date_hierarchy = 'date'
