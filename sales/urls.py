from django.urls import path
from . import views

app_name = "sales"

urlpatterns = [
    # Seller POS (home for this app)
    path("", views.seller_dashboard, name="seller_dashboard"),

    # Customer debt statement
    path("debts/customer/<int:customer_id>/", views.customer_debt_statement, name="customer_debt_statement"),

    # POS actions
    path("lookup/", views.lookup_product, name="lookup_product"),
    path("add/<int:product_id>/", views.add_to_cart, name="add_to_cart"),
    path("remove/<int:product_id>/", views.remove_from_cart, name="remove_from_cart"),
    path("update/<int:product_id>/", views.update_cart_qty, name="update_cart_qty"),
    path("add_selected/", views.add_selected_product_to_cart, name="add_selected_product_to_cart"),
    path("ajax/products/", views.ajax_products, name="ajax_products"),
    path("submit/", views.submit_sale, name="submit_sale"),

    # Customers / suppliers
    path("customers/", views.customer_list, name="customer_list"),
    path("suppliers/", views.supplier_list, name="supplier_list"),

    # Invoice
    path("invoice/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoice/<int:pk>/receipt/", views.invoice_receipt, name="invoice_receipt"),
    path("invoice/<int:pk>/cancel/", views.cancel_invoice, name="cancel_invoice"),

    # Dashboards / payments
    path("admin/dashboard/", views.admin_dashboard, name="admin_dashboard"),

    # New split business dashboards
    path("admin/dashboard/electronics/", views.electronics_dashboard, name="electronics_dashboard"),
    path("admin/dashboard/furniture/", views.furniture_dashboard, name="furniture_dashboard"),
    path("admin/dashboard/magodoro/", views.magodoro_dashboard, name="magodoro_dashboard"),

    path("payment/<int:invoice_id>/", views.record_payment, name="record_payment"),
    path("debts/", views.outstanding_debts, name="outstanding_debts"),

    # Expenses / reports
    path("expenses/add/", views.add_expense, name="kilasi_add_expense"),
    path("expenses/history/", views.expense_history, name="expense_history"),
    path("expenses/", views.expense_overview, name="expense_overview"),

    path("sales_history/", views.sales_history, name="kilasi_sales_history"),
    path("sales-report/", views.sales_report, name="kilasi_sales_report"),
    path("enhanced-sales-report/", views.enhanced_sales_report, name="enhanced_sales_report"),
    path("reports/progress/", views.period_progress_report, name="period_progress_report"),

    # Products
    path("products/", views.product_list, name="product_list"),
    path("products/<int:pk>/", views.product_detail, name="product_detail"),
    path("products/<int:pk>/details/", views.product_details_json, name="product_details_json"),
    path("products/export/excel/", views.export_products_excel, name="export_products_excel"),
    path("top-selling/", views.top_selling_products, name="top_selling_products"),

    # Admin pages
    path("invoices/purchase/", views.purchase_invoices, name="purchase_invoices"),
    path("invoices/sales/", views.sales_invoices, name="sales_invoices"),
    path("payments/", views.all_payments, name="all_payments"),

    # Stock
    path("stock/", views.stock_management, name="stock_management"),
    path("stock/search/", views.stock_search_products, name="stock_search_products"),
    path("stock/transfers/", views.stock_transfer_list, name="stock_transfer_list"),
    path("stock/transfers/new/", views.stock_transfer_create, name="stock_transfer_create"),
    path("stock/transfers/<int:pk>/", views.stock_transfer_detail, name="stock_transfer_detail"),
    path("stock/transfers/<int:pk>/post/", views.stock_transfer_post, name="stock_transfer_post"),
    path("stock-transfers/new/", views.stock_transfer_create, name="stock_transfer_create"),
    path("api/branch-stock/", views.api_branch_stock, name="api_branch_stock"),

    # Debtors
    path("debtors/", views.debtor_list, name="debtor_list"),
    path("debtors/add/", views.debtor_create, name="debtor_create"),
    path("debtors/<int:pk>/", views.debtor_detail, name="debtor_detail"),
    path("debtors/<int:pk>/edit/", views.debtor_update, name="debtor_update"),
    path("debtors/<int:pk>/delete/", views.debtor_delete, name="debtor_delete"),
    path("debtors/<int:pk>/pay/", views.debtor_add_payment, name="debtor_add_payment"),
    path("debtors/<int:pk>/payments/<int:payment_id>/delete/", views.debtor_delete_payment, name="debtor_delete_payment"),

    # Sales returns
    path("returns/", views.sales_return_list, name="sales_return_list"),
    path("returns/new/<int:invoice_id>/", views.sales_return_create, name="sales_return_create"),
    path("returns/<int:pk>/", views.sales_return_detail, name="sales_return_detail"),
    path("returns/<int:pk>/post/", views.sales_return_post, name="sales_return_post"),

    # Genji
    path("genji/", views.genji_dashboard, name="genji_dashboard"),
    path("genji/add/", views.genji_create, name="genji_create"),
    path("genji/<int:pk>/edit/", views.genji_update, name="genji_update"),
    path("genji/<int:pk>/delete/", views.genji_delete, name="genji_delete"),

    # My debts
    path("my-debts/", views.my_debt_list, name="my_debt_list"),
    path("my-debts/add/", views.my_debt_create, name="my_debt_create"),
    path("my-debts/<int:pk>/", views.my_debt_detail, name="my_debt_detail"),
    path("my-debts/<int:pk>/edit/", views.my_debt_update, name="my_debt_update"),
    path("my-debts/<int:pk>/delete/", views.my_debt_delete, name="my_debt_delete"),
    path("my-debts/<int:pk>/add-payment/", views.my_debt_add_payment, name="my_debt_add_payment"),
]