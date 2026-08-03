from django.contrib import admin

from .models import Booking, Driver, TransportExpense, TransportSMSTemplate, Trip, Vehicle


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("plate_number", "model", "capacity", "is_active")
    list_filter = ("is_active",)
    search_fields = ("plate_number", "model")


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ("full_name", "user", "branch", "vehicle", "phone", "license_number", "is_active")
    list_filter = ("is_active", "branch", "vehicle")
    search_fields = (
        "first_name",
        "last_name",
        "user__username",
        "phone",
        "license_number",
        "vehicle__plate_number",
    )
    list_select_related = ("user", "branch", "vehicle")


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = (
        "customer_name",
        "customer_phone",
        "driver",
        "vehicle",
        "origin",
        "destination",
        "status",
        "amount_charged",
        "trip_date",
        "pending_sms_sent",
        "completed_sms_sent",
        "branch",
    )
    list_filter = ("status", "branch", "driver", "vehicle", "pending_sms_sent", "completed_sms_sent")
    search_fields = (
        "customer_name",
        "customer_phone",
        "origin",
        "destination",
        "vehicle__plate_number",
        "driver__first_name",
        "driver__last_name",
        "driver__user__username",
    )
    date_hierarchy = "trip_date"
    list_select_related = ("driver__user", "vehicle", "branch")
    readonly_fields = (
        "started_at",
        "completed_at",
        "cancelled_at",
        "pending_sms_sent",
        "pending_sms_sent_at",
        "pending_sms_error",
        "completed_sms_sent",
        "completed_sms_sent_at",
        "completed_sms_error",
        "created_at",
        "updated_at",
    )


@admin.register(TransportSMSTemplate)
class TransportSMSTemplateAdmin(admin.ModelAdmin):
    list_display = ("event", "sender_id", "is_active", "updated_at", "updated_by")
    list_filter = ("event", "is_active", "sender_id")
    search_fields = ("event", "message")
    readonly_fields = ("created_at", "updated_at")


@admin.register(TransportExpense)
class TransportExpenseAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "expense_type", "amount", "expense_date", "branch", "recorded_by")
    list_filter = ("expense_type", "branch", "vehicle")
    search_fields = ("description", "vehicle__plate_number")
    date_hierarchy = "expense_date"

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "customer_name",
        "customer_phone",
        "pickup_area",
        "destination",
        "booking_datetime",
        "status",
        "confirmation_sms_sent",
        "boss_sms_sent",
        "reminder_sent",
        "branch",
        "created_by",
    )

    list_filter = (
        "status",
        "confirmation_sms_sent",
        "boss_sms_sent",
        "reminder_sent",
        "branch",
        "booking_datetime",
    )

    search_fields = (
        "customer_name",
        "customer_phone",
        "pickup_area",
        "destination",
    )

    date_hierarchy = "booking_datetime"

    list_select_related = (
        "branch",
        "created_by",
    )

    readonly_fields = (
        "confirmation_sms_sent",
        "confirmation_sms_sent_at",
        "confirmation_sms_error",
        "boss_sms_sent",
        "boss_sms_sent_at",
        "boss_sms_error",
        "reminder_sent",
        "reminder_sent_at",
        "reminder_last_attempt_at",
        "reminder_sms_error",
        "created_at",
    )