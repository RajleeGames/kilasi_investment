from django import forms
from .models import Contact

class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ["full_name", "role", "phone", "whatsapp", "email", "company", "address", "notes"]
        widgets = {
            "full_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Full name"}),
            "role": forms.Select(attrs={"class": "form-select"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Phone"}),
            "whatsapp": forms.TextInput(attrs={"class": "form-control", "placeholder": "WhatsApp"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "Email"}),
            "company": forms.TextInput(attrs={"class": "form-control", "placeholder": "Company"}),
            "address": forms.TextInput(attrs={"class": "form-control", "placeholder": "Address"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Notes"}),
        }
