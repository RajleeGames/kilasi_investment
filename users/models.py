# users/models.py
from django.conf import settings
from django.db import models
from django.dispatch import receiver
from django.db.models.signals import post_save
from django.utils.translation import gettext_lazy as _

# Use settings.AUTH_USER_MODEL so this works with custom User models too
USER_MODEL = settings.AUTH_USER_MODEL

class Role(models.TextChoices):
    ADMIN = 'admin', _('Admin')
    CASHIER = 'cashier', _('Cashier')
    SELLER = 'seller', _('Seller')
    TRANSPORT = 'transport', _('Transport')

class Profile(models.Model):
    user = models.OneToOneField(USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    # Role choices: default 'seller' to make migrations painless for existing users
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.SELLER)
    # Branch is optional (admins/cashiers may be branch-less)
    branch = models.ForeignKey('inventory.Branch', null=True, blank=True, on_delete=models.SET_NULL, related_name='profiles')
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Profile"
        verbose_name_plural = "Profiles"
        ordering = ['user__username']

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    # Convenience helpers
    def is_admin(self):
        return self.role == Role.ADMIN

    def is_cashier(self):
        return self.role == Role.CASHIER

    def is_seller(self):
        return self.role == Role.SELLER

    def is_transport(self):
        return self.role == Role.TRANSPORT

    @property
    def display_name(self):
        # fallback to username if full name not available
        full_name = getattr(self.user, 'get_full_name', None)
        if callable(full_name):
            name = self.user.get_full_name()
            return name if name else self.user.username
        return self.user.username

# Signals to create / save Profile automatically
@receiver(post_save, sender=USER_MODEL)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    """
    Create a Profile when a new User is created.
    Also ensure profile is saved on user.save().
    """
    if created:
        # created=True: create a Profile with default role (seller)
        Profile.objects.create(user=instance)
    else:
        # existing user: ensure profile exists and save it
        Profile.objects.get_or_create(user=instance)
        try:
            instance.profile.save()
        except Exception:
            # profile may not be fully formed yet; ignore to avoid raising in auth flows
            pass
