from django.urls import path

from . import views

app_name = "transport"

urlpatterns = [
    path("", views.index, name="index"),

    # Private driver portal
    path("driver/", views.driver_dashboard, name="driver_dashboard"),

    # Vehicles
    path("vehicles/", views.vehicle_list, name="vehicle_list"),
    path("vehicles/add/", views.vehicle_create, name="vehicle_create"),

    # Drivers
    path("drivers/", views.driver_list, name="driver_list"),
    path("drivers/add/", views.driver_create, name="driver_create"),
    path("drivers/<int:pk>/edit/", views.driver_edit, name="driver_edit"),

    # Trips
    path("trips/", views.trip_list, name="trip_list"),
    path("trips/add/", views.trip_create, name="trip_create"),
    path("trips/<int:pk>/", views.trip_detail, name="trip_detail"),
    path("trips/<int:pk>/status/", views.trip_status_update, name="trip_status_update"),
    path("trips/<int:pk>/sms/<str:event>/retry/", views.trip_retry_sms, name="trip_retry_sms"),

    # Admin-controlled automatic transport SMS
    path("automatic-sms/", views.sms_template_list, name="sms_template_list"),
    path("automatic-sms/<int:pk>/edit/", views.sms_template_edit, name="sms_template_edit"),

    # Expenses
    path("expenses/", views.expense_list, name="expense_list"),
    path("expenses/add/", views.expense_create, name="expense_create"),

    # Bookings
    path("bookings/", views.booking_list, name="booking_list"),
    path("bookings/add/", views.booking_create, name="booking_create"),
    path("bookings/<int:pk>/edit/", views.booking_edit, name="booking_edit"),
    path(
    "bookings/<int:pk>/sms/<str:event>/retry/",
    views.booking_retry_sms,
    name="booking_retry_sms",
),
    path("bookings/<int:pk>/delete/", views.booking_delete, name="booking_delete"),
]
