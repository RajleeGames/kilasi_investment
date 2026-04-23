from django.urls import path
from . import views

app_name = "topup"

urlpatterns = [
    path("", views.topup_dashboard, name="dashboard"),
    path("new/", views.topup_create, name="create"),
    path("history/", views.topup_history, name="history"),
    path("transaction/<int:pk>/", views.topup_transaction_detail, name="transaction_detail"),

    path("used-stock/", views.used_stock_list, name="used_stock_list"),
    path("used-stock/<int:pk>/", views.used_stock_detail, name="used_stock_detail"),
    path("used-stock/<int:pk>/sell/", views.used_stock_sell, name="used_stock_sell"),
]