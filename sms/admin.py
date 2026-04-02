from django.contrib import admin
from .models import Contact, ContactGroup, SenderID, SMSTemplate, SMSCampaign, SMSMessage, ContactImport


@admin.register(ContactGroup)
class ContactGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "description")
    search_fields = ("name",)


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "is_active", "created_at")
    search_fields = ("name", "phone", "email")
    list_filter = ("is_active", "groups")


@admin.register(SenderID)
class SenderIDAdmin(admin.ModelAdmin):
    list_display = ("name", "is_default", "is_active", "created_at")
    list_filter = ("is_default", "is_active")
    search_fields = ("name",)


@admin.register(SMSTemplate)
class SMSTemplateAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "created_at")
    search_fields = ("title", "message")
    list_filter = ("is_active",)


@admin.register(SMSCampaign)
class SMSCampaignAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "total_recipients", "sent_count", "created_at", "sent_at")
    list_filter = ("status", "sender_id")
    search_fields = ("title", "message")


@admin.register(SMSMessage)
class SMSMessageAdmin(admin.ModelAdmin):
    list_display = ("dest_addr", "status", "sender_id", "campaign", "sent_at")
    list_filter = ("status", "sender_id", "campaign")
    search_fields = ("dest_addr", "message", "request_id")


@admin.register(ContactImport)
class ContactImportAdmin(admin.ModelAdmin):
    list_display = ("source", "file_name", "imported_count", "skipped_count", "created_at")
    list_filter = ("source",)