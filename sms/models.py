from django.conf import settings
from django.db import models
from django.utils import timezone


class ContactGroup(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Contact(models.Model):
    name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(
        max_length=20,
        unique=True,
        help_text="International format without +, e.g. 255712345678"
    )
    email = models.EmailField(blank=True)
    groups = models.ManyToManyField(ContactGroup, blank=True, related_name="contacts")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_contacts_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "phone"]

    def __str__(self):
        return f"{self.name or self.phone} ({self.phone})"


class SenderID(models.Model):
    name = models.CharField(max_length=50, unique=True, help_text="Example: KILASI, WORLDLINK")
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_senderids_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_default", "name"]
        verbose_name = "Sender ID"
        verbose_name_plural = "Sender IDs"

    def __str__(self):
        return self.name


class SMSTemplate(models.Model):
    title = models.CharField(max_length=120)
    message = models.TextField()
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_templates_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class SMSCampaign(models.Model):
    STATUS_CHOICES = [
        ("DRAFT", "Draft"),
        ("QUEUED", "Queued"),
        ("SENT", "Sent"),
        ("PARTIAL", "Partially Sent"),
        ("FAILED", "Failed"),
    ]

    title = models.CharField(max_length=150)
    template = models.ForeignKey(
        SMSTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns"
    )
    message = models.TextField(blank=True)
    sender_id = models.ForeignKey(
        SenderID,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns"
    )
    groups = models.ManyToManyField(ContactGroup, blank=True, related_name="campaigns")
    contacts = models.ManyToManyField(Contact, blank=True, related_name="campaigns")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT")
    total_recipients = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    delivered_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_campaigns_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def get_message_text(self):
        return (self.template.message if self.template else self.message) or ""


class ContactImport(models.Model):
    SOURCE_CHOICES = [
        ("CSV", "CSV"),
        ("MANUAL", "Manual"),
        ("PASTE", "Paste"),
        ("GOOGLE", "Google"),
    ]

    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="CSV")
    file_name = models.CharField(max_length=255, blank=True)
    imported_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_imports_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.source} import - {self.created_at:%Y-%m-%d %H:%M}"


class SMSMessage(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("SENT", "Sent"),
        ("DELIVERED", "Delivered"),
        ("UNDELIVERED", "Undelivered"),
        ("FAILED", "Failed"),
    ]

    campaign = models.ForeignKey(
        SMSCampaign,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages"
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages"
    )
    sender_id = models.ForeignKey(
        SenderID,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages"
    )

    dest_addr = models.CharField(max_length=20, help_text="International format without +, e.g. 255712345678")
    message = models.TextField()
    recipient_id = models.CharField(max_length=200, blank=True, null=True)
    request_id = models.CharField(max_length=200, blank=True, null=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    response_raw = models.JSONField(null=True, blank=True)
    error_text = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_messages_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["dest_addr"]),
            models.Index(fields=["request_id"]),
        ]

    def __str__(self):
        return f"{self.dest_addr} - {self.status}"