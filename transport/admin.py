# transport/admin.py
from django.contrib import admin
from .models import Vehicle, Driver, Trip, TransportExpense

@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ('plate_number', 'model', 'is_active')
    search_fields = ('plate_number',)

@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ('user', 'phone', 'license_number', 'is_active')
    search_fields = ('user__username', 'phone')

@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = (
        'vehicle',
        'driver',
        'origin',
        'destination',
        'amount_charged',
        'trip_date',
        'branch',
    )
    list_filter = ('branch', 'vehicle')
    search_fields = ('origin', 'destination', 'vehicle__plate_number')

@admin.register(TransportExpense)
class TransportExpenseAdmin(admin.ModelAdmin):
    list_display = (
        'vehicle',
        'expense_type',
        'amount',
        'expense_date',
        'branch',
    )
    list_filter = ('expense_type', 'branch', 'vehicle')
