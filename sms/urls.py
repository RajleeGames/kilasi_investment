from django.urls import path
from . import views

app_name = "sms"

urlpatterns = [
    path("", views.sms_dashboard, name="dashboard"),

    path("contacts/", views.contact_list, name="contact_list"),
    path("contacts/add/", views.contact_create, name="contact_create"),
    path("contacts/<int:pk>/edit/", views.contact_update, name="contact_update"),
    path("contacts/<int:pk>/delete/", views.contact_delete, name="contact_delete"),
    path("contacts/import/", views.contact_import, name="contact_import"),

    path("send/", views.send_sms_view, name="send_sms"),

    path("templates/", views.template_list, name="template_list"),
    path("templates/add/", views.template_create, name="template_create"),
    path("templates/<int:pk>/edit/", views.template_update, name="template_update"),

    path("senders/", views.sender_list, name="sender_list"),
    path("senders/add/", views.sender_create, name="sender_create"),
    path("senders/<int:pk>/edit/", views.sender_update, name="sender_update"),

    path("campaigns/", views.campaign_list, name="campaign_list"),
    path("campaigns/add/", views.campaign_create, name="campaign_create"),
    path("campaigns/<int:pk>/edit/", views.campaign_update, name="campaign_update"),
    path("campaigns/<int:pk>/send/", views.campaign_send_view, name="campaign_send"),
    path("campaigns/<int:pk>/sync-delivery/", views.campaign_sync_delivery_view, name="campaign_sync_delivery"),
    path("contacts/delete-all/", views.contact_delete_all, name="contact_delete_all"),
    path("logs/", views.sms_logs, name="sms_logs"),
]