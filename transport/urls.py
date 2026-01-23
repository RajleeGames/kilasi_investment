# transport/urls.py
from django.urls import path
from . import views

app_name = 'transport'

urlpatterns = [
    path('', views.index, name='index'),

    # Vehicles
    path('vehicles/', views.vehicle_list, name='vehicle_list'),
    path('vehicles/add/', views.vehicle_create, name='vehicle_create'),

    # Drivers
    path('drivers/', views.driver_list, name='driver_list'),
    path('drivers/add/', views.driver_create, name='driver_create'),

    # Trips
    path('trips/', views.trip_list, name='trip_list'),
    path('trips/add/', views.trip_create, name='trip_create'),

    # Expenses
    path('expenses/', views.expense_list, name='expense_list'),
    path('expenses/add/', views.expense_create, name='expense_create'),
]
