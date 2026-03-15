from django.contrib import admin
from .models import Vehicle, Driver, Trip, TransportExpense, Booking


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
        'customer_phone',   # ✅ NEW
        'amount_charged',
        'trip_date',
        'branch',
    )
    list_filter = ('branch', 'vehicle')
    search_fields = (
        'origin',
        'destination',
        'vehicle__plate_number',
        'customer_phone',   # ✅ NEW
    )


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


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        'customer_name',
        'customer_phone',
        'pickup_area',
        'destination',
        'booking_datetime',
        'status',
        'branch',
        'created_by',
    )
    list_filter = ('status', 'branch', 'booking_datetime')
    search_fields = ('customer_name', 'customer_phone', 'pickup_area', 'destination')