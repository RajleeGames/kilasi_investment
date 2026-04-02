from django import forms
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