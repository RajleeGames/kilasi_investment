# inventory/admin.py
from django.contrib import admin
from .models import Category, Supplier, Product, ProductStock, StockEntry, Branch

@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_active')
    search_fields = ('name', 'code')

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact')
    search_fields = ('name', 'contact')

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'supplier', 'price', 'stock', 'reorder_level')
    list_filter = ('category', 'supplier')
    search_fields = ('name', 'barcode', 'supplier__name', 'category__name')

@admin.register(ProductStock)
class ProductStockAdmin(admin.ModelAdmin):
    list_display = ('product', 'branch', 'quantity')
    list_filter = ('branch', 'product__category')
    search_fields = ('product__name', 'branch__name')

@admin.register(StockEntry)
class StockEntryAdmin(admin.ModelAdmin):
    list_display = ('product', 'branch', 'change', 'timestamp', 'note')
    list_filter = ('branch', 'product')
    search_fields = ('product__name', 'note')
    date_hierarchy = 'timestamp'
