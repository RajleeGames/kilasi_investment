# users/models.py

from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _


# Use settings.AUTH_USER_MODEL so this also works with a custom User model.
USER_MODEL = settings.AUTH_USER_MODEL


class Role(models.TextChoices):
    ADMIN = "admin", _("Admin")
    CASHIER = "cashier", _("Cashier")
    SELLER = "seller", _("Seller")

    # Transport manager/staff.
    TRANSPORT = "transport", _("Transport")

    # Individual vehicle driver.
    DRIVER = "driver", _("Driver")


class Profile(models.Model):
    user = models.OneToOneField(
        USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    # Keep seller as the default so existing behavior is not disturbed.
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.SELLER,
        db_index=True,
    )

    # Drivers should normally have a branch too.
    branch = models.ForeignKey(
        "inventory.Branch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="profiles",
    )

    phone_number = models.CharField(
        max_length=20,
        blank=True,
        null=True,
    )

    picture = models.ImageField(
        upload_to="profile_pictures/",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Profile"
        verbose_name_plural = "Profiles"
        ordering = ["user__username"]
        indexes = [
            models.Index(fields=["role"]),
            models.Index(fields=["branch", "role"]),
        ]

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    # -----------------------------------------------------
    # Role helpers
    # -----------------------------------------------------
    def is_admin(self):
        return self.role == Role.ADMIN

    def is_cashier(self):
        return self.role == Role.CASHIER

    def is_seller(self):
        return self.role == Role.SELLER

    def is_transport(self):
        """
        True only for transport managers/staff.

        Drivers use the separate DRIVER role.
        """
        return self.role == Role.TRANSPORT

    def is_driver(self):
        return self.role == Role.DRIVER

    def is_transport_user(self):
        """
        Useful when both transport managers and drivers are allowed.

        Example:
            if request.user.profile.is_transport_user():
                ...
        """
        return self.role in {
            Role.TRANSPORT,
            Role.DRIVER,
        }

    @property
    def display_name(self):
        """
        Return the user's full name when available.
        Otherwise, return the username.
        """
        get_full_name = getattr(self.user, "get_full_name", None)

        if callable(get_full_name):
            name = get_full_name().strip()
            if name:
                return name

        return self.user.username


# ---------------------------------------------------------
# Automatically create a Profile for every new user
# ---------------------------------------------------------
@receiver(post_save, sender=USER_MODEL)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    """
    Create a Profile when a new user is created.

    For an existing user, make sure a Profile exists.
    """
    if created:
        Profile.objects.get_or_create(
            user=instance,
            defaults={
                "role": Role.SELLER,
            },
        )
        return

    Profile.objects.get_or_create(
        user=instance,
        defaults={
            "role": Role.SELLER,
        },
    )