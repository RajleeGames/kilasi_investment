from django.urls import path
from . import views
from sales.views import mark_paid  # import the view

urlpatterns = [
  path('', views.cashier_dashboard, name='cashier_dashboard'),
  path('invoice/<int:pk>/', views.cashier_invoice, name='cashier_invoice'),
  path('mark_paid/<int:pk>/', mark_paid, name='mark_paid'),
]
