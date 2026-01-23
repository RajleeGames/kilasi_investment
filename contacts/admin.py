from django.contrib import admin
from .models import Contact

@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("full_name", "role", "phone", "whatsapp", "company", "created_at")
    search_fields = ("full_name", "phone", "whatsapp", "company", "email")
    list_filter = ("role",)
