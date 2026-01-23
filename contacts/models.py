from django.db import models

class Contact(models.Model):
    ROLE_CHOICES = [
        ("boss", "Boss"),
        ("secretary", "Secretary"),
        ("staff", "Staff"),
        ("driver", "Driver"),
        ("supplier", "Supplier"),
        ("customer", "Customer"),
        ("other", "Other"),
    ]

    full_name = models.CharField(max_length=120)
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, default="other")
    phone = models.CharField(max_length=30, blank=True, null=True)
    whatsapp = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    company = models.CharField(max_length=120, blank=True, null=True)
    address = models.CharField(max_length=200, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.full_name
