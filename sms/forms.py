from django import forms
from django.utils import timezone

from .models import Contact, ContactGroup, SMSTemplate, SMSCampaign, SenderID


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ["name", "phone", "email", "groups", "is_active", "notes"]
        widgets = {
            "groups": forms.CheckboxSelectMultiple,
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class ContactImportForm(forms.Form):
    csv_file = forms.FileField(required=False)
    group = forms.ModelChoiceField(
        queryset=ContactGroup.objects.all(),
        required=False
    )
    overwrite_names = forms.BooleanField(required=False)
    pasted_contacts = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text="Paste one per line. Example: John,255712345678 or just 255712345678"
    )


class SMSTemplateForm(forms.ModelForm):
    class Meta:
        model = SMSTemplate
        fields = ["title", "message", "is_active"]
        widgets = {
            "message": forms.Textarea(attrs={"rows": 6}),
        }


class SenderIDForm(forms.ModelForm):
    class Meta:
        model = SenderID
        fields = ["name", "is_default", "is_active", "notes"]


class SMSCampaignForm(forms.ModelForm):
    class Meta:
        model = SMSCampaign
        fields = ["title", "template", "message", "sender_id", "groups", "contacts"]
        widgets = {
            "groups": forms.CheckboxSelectMultiple,
            "contacts": forms.SelectMultiple(attrs={"size": 12}),
            "message": forms.Textarea(attrs={"rows": 6}),
        }


class QuickSMSForm(forms.Form):
    sender_id = forms.ModelChoiceField(
        queryset=SenderID.objects.filter(is_active=True),
        required=False
    )
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}))
    contacts = forms.ModelMultipleChoiceField(
        queryset=Contact.objects.filter(is_active=True),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 12})
    )
    manual_numbers = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text="One number per line"
    )


class SalesCustomersSMSForm(forms.Form):
    PAYMENT_CHOICES = [
        ("", "All Payment Methods"),
        ("cash", "Cash"),
        ("bank", "Bank"),
        ("ebt", "EBT"),
        ("debt", "Debt"),
    ]

    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    payment_method = forms.ChoiceField(
        choices=PAYMENT_CHOICES,
        required=False,
    )

    sender_id = forms.ModelChoiceField(
        queryset=SenderID.objects.filter(is_active=True),
        required=False,
        label="Sender ID",
    )

    message = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Type one message and send to all selected sales customers.",
    )

    def clean(self):
        cleaned = super().clean()

        start_date = cleaned.get("start_date")
        end_date = cleaned.get("end_date")

        today = timezone.localdate()

        if not start_date:
            start_date = today

        if not end_date:
            end_date = today

        if start_date > end_date:
            start_date, end_date = end_date, start_date

        cleaned["start_date"] = start_date
        cleaned["end_date"] = end_date

        return cleaned