from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("", views.order_dashboard, name="dashboard"),
    path("list/", views.order_list, name="order_list"),
    path("create/", views.order_create, name="order_create"),
    path("<int:pk>/", views.order_detail, name="order_detail"),
    path("<int:pk>/payment/", views.order_add_payment, name="order_add_payment"),
    path("<int:pk>/status/", views.order_update_status, name="order_update_status"),
]