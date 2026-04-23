from django.contrib import admin
from .models import TopupTransaction, UsedTopupStock, UsedTopupSale


@admin.register(TopupTransaction)
class TopupTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "created_at",
        "branch",
        "customer",
        "used_item_name",
        "new_product",
        "new_product_qty",
        "used_item_buying_price",
        "addon_amount",
        "status",
    )
    list_filter = ("status", "branch", "created_at")
    search_fields = ("used_item_name", "customer__name", "new_product__name")
    date_hierarchy = "created_at"


@admin.register(UsedTopupStock)
class UsedTopupStockAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "item_name",
        "branch",
        "buying_price",
        "asking_price",
        "sold_price",
        "status",
        "created_at",
    )
    list_filter = ("status", "branch", "created_at")
    search_fields = ("item_name", "item_category", "condition")
    date_hierarchy = "created_at"


@admin.register(UsedTopupSale)
class UsedTopupSaleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "used_stock",
        "branch",
        "sold_price",
        "profit",
        "sold_by",
        "created_at",
    )
    list_filter = ("branch", "created_at")
    search_fields = ("used_stock__item_name", "customer__name")
    date_hierarchy = "created_at"