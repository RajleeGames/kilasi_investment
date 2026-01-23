# sales/urls.py
from django.urls import path
from . import views

app_name = "sales"

urlpatterns = [
    # Seller POS (home for this app)
    path("", views.seller_dashboard, name="seller_dashboard"),

    # POS actions
    path("lookup/", views.lookup_product, name="lookup_product"),
    path("add/<int:product_id>/", views.add_to_cart, name="add_to_cart"),
    path("remove/<int:product_id>/", views.remove_from_cart, name="remove_from_cart"),
    path("update/<int:product_id>/", views.update_cart_qty, name="update_cart_qty"),
    path("add_selected/", views.add_selected_product_to_cart, name="add_selected_product_to_cart"),
    path("ajax/products/", views.ajax_products, name="ajax_products"),
    path("submit/", views.submit_sale, name="submit_sale"),
    
    #suppliers 
    
    path("customers/", views.customer_list, name="customer_list"),
    
    path("suppliers/", views.supplier_list, name="supplier_list"),

    
    # Invoice
    path("invoice/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoice/<int:pk>/receipt/", views.invoice_receipt, name="invoice_receipt"),


    # Dashboards / payments
    path("admin/dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("payment/<int:invoice_id>/", views.record_payment, name="record_payment"),
    path("debts/", views.outstanding_debts, name="outstanding_debts"),

    # Expenses / reports
    path("expenses/add/", views.add_expense, name="kilasi_add_expense"),
    path("expenses/history/", views.expense_history, name="expense_history"),
    path("expenses/", views.expense_overview, name="expense_overview"),

    path("sales_history/", views.sales_history, name="kilasi_sales_history"),
    path("sales-report/", views.sales_report, name="kilasi_sales_report"),
    path("enhanced-sales-report/", views.enhanced_sales_report, name="enhanced_sales_report"),

    # Products
    path("products/", views.product_list, name="product_list"),
    path("top-selling/", views.top_selling_products, name="top_selling_products"),
    path("products/<int:pk>/", views.product_detail, name="product_detail"),
    path("products/<int:pk>/details/", views.product_details_json, name="product_details_json"),

    # Admin pages
    path("customers/", views.customer_list, name="customer_list"),
    path("suppliers/", views.supplier_list, name="supplier_list"),
    path("invoices/purchase/", views.purchase_invoices, name="purchase_invoices"),
    path("invoices/sales/", views.sales_invoices, name="sales_invoices"),
    path("payments/", views.all_payments, name="all_payments"),
    path("stock/", views.stock_management, name="stock_management"),
    path("stock/search/", views.stock_search_products, name="stock_search_products"),
]
