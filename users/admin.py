# users/admin.py
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Profile, Role

User = get_user_model()

# ─── Profile Inline for User Admin ─────────────────────────────
class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'Profile'
    fk_name = 'user'
    extra = 0

# ─── Custom User Admin ────────────────────────────────────────
class UserAdmin(BaseUserAdmin):
    inlines = (ProfileInline,)
    list_display = (
        'username',
        'email',
        'first_name',
        'last_name',
        'get_role',
        'is_staff',
        'is_active',
    )
    list_filter = ('is_staff', 'is_active', 'profile__role', 'profile__branch')
    search_fields = ('username', 'email', 'first_name', 'last_name', 'profile__phone_number')
    ordering = ('username',)

    def get_role(self, obj):
        return obj.profile.role if hasattr(obj, 'profile') else '-'
    get_role.short_description = 'Role'

# ─── Profile Admin ────────────────────────────────────────────
@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'branch', 'phone_number', 'created_at', 'updated_at')
    list_filter = ('role', 'branch')
    search_fields = ('user__username', 'user__email', 'phone_number')
    raw_id_fields = ('user',)
    ordering = ('user__username',)

# ─── Unregister default User and register our custom one ───────
admin.site.unregister(User)
admin.site.register(User, UserAdmin)
