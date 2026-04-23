from django.contrib import admin
from .models import Branch, Category, Supplier, Product, ProductStock, StockEntry


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "contact")
    search_fields = ("name", "contact")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "business_type",
        "category",
        "supplier",
        "buying_price",
        "selling_price",
        "profit_per_unit",
        "stock",
        "reorder_level",
        "barcode",
    )
    list_filter = ("business_type", "category", "supplier")
    search_fields = ("name", "barcode")
    readonly_fields = ("stock", "barcode")

    fieldsets = (
        ("Basic Info", {
            "fields": ("name", "business_type", "category", "supplier", "barcode")
        }),
        ("Pricing", {
            "fields": ("buying_price", "selling_price", "reorder_level")
        }),
        ("Stock (Auto)", {
            "fields": ("stock",)
        }),
    )

    def profit_per_unit(self, obj):
        try:
            return obj.profit_per_unit()
        except Exception:
            return 0
    profit_per_unit.short_description = "Profit/Unit"


@admin.register(ProductStock)
class ProductStockAdmin(admin.ModelAdmin):
    list_display = ("product", "branch", "quantity")
    list_filter = ("branch",)
    search_fields = ("product__name", "branch__name")
    autocomplete_fields = ("product", "branch")


@admin.register(StockEntry)
class StockEntryAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "product", "branch", "change", "note")
    list_filter = ("branch", "timestamp")
    search_fields = ("product__name", "note", "branch__name")
    autocomplete_fields = ("product", "branch")
    readonly_fields = ("timestamp",)

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return True