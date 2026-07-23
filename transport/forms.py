from django import forms
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from inventory.models import Branch

from .models import (
    Booking,
    Driver,
    TransportExpense,
    TransportSMSTemplate,
    Trip,
    Vehicle,
)

User = get_user_model()


def _add_bootstrap_classes(form):
    for field in form.fields.values():
        if isinstance(field.widget, forms.CheckboxInput):
            css = "form-check-input"
        elif isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
            css = "form-select"
        else:
            css = "form-control"
        existing = field.widget.attrs.get("class", "").strip()
        field.widget.attrs["class"] = f"{existing} {css}".strip()


def _sync_driver_user_profile(user, driver):
    """Update the existing users.Profile created by the project's signal."""
    profile = getattr(user, "profile", None)
    if not profile:
        return

    update_fields = []
    if hasattr(profile, "role") and getattr(profile, "role", None) != "driver":
        profile.role = "driver"
        update_fields.append("role")
    if hasattr(profile, "branch") and getattr(profile, "branch_id", None) != driver.branch_id:
        profile.branch = driver.branch
        update_fields.append("branch")

    if update_fields:
        profile.save(update_fields=update_fields)


class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = ["plate_number", "model", "capacity", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _add_bootstrap_classes(self)


class DriverForm(forms.ModelForm):
    username = forms.CharField(max_length=150, label="Login username")
    password1 = forms.CharField(
        required=False,
        label="Temporary password",
        widget=forms.PasswordInput(render_value=False),
        help_text="Required for a new driver. Leave blank while editing to keep the current password.",
    )
    password2 = forms.CharField(
        required=False,
        label="Confirm password",
        widget=forms.PasswordInput(render_value=False),
    )

    class Meta:
        model = Driver
        fields = [
            "username",
            "password1",
            "password2",
            "first_name",
            "last_name",
            "phone",
            "license_number",
            "branch",
            "vehicle",
            "photo",
            "is_active",
        ]
        widgets = {
            "phone": forms.TextInput(attrs={"placeholder": "Example: 0712345678"}),
            "license_number": forms.TextInput(attrs={"placeholder": "Driving licence number"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].required = True
        self.fields["vehicle"].queryset = Vehicle.objects.filter(is_active=True).order_by("plate_number")
        self.fields["branch"].queryset = Branch.objects.all().order_by("name")

        if self.instance and self.instance.pk:
            self.fields["username"].initial = self.instance.user.username
        else:
            self.fields["password1"].required = True
            self.fields["password2"].required = True

        _add_bootstrap_classes(self)

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        qs = User.objects.filter(username__iexact=username)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.user_id)
        if qs.exists():
            raise forms.ValidationError("That username is already in use.")
        return username

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1") or ""
        p2 = cleaned.get("password2") or ""
        if p1 or p2:
            if p1 != p2:
                self.add_error("password2", "Passwords do not match.")
            elif len(p1) < 8:
                self.add_error("password1", "Use at least 8 characters.")
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        driver = super().save(commit=False)
        username = self.cleaned_data["username"]
        password = self.cleaned_data.get("password1") or ""

        if driver.pk:
            user = driver.user
        else:
            user = User(username=username)

        user.username = username
        user.first_name = self.cleaned_data.get("first_name") or ""
        user.last_name = self.cleaned_data.get("last_name") or ""
        user.is_active = bool(self.cleaned_data.get("is_active", True))
        if password:
            user.set_password(password)
        user.save()

        driver.user = user
        driver.is_active = bool(self.cleaned_data.get("is_active", True))
        if commit:
            driver.save()
            self.save_m2m()
            _sync_driver_user_profile(user, driver)
        return driver


class AdminTripForm(forms.ModelForm):
    class Meta:
        model = Trip
        fields = [
            "branch",
            "trip_date",
            "vehicle",
            "driver",
            "customer_name",
            "customer_phone",
            "origin",
            "destination",
            "amount_charged",
            "notes",
        ]
        widgets = {
            "trip_date": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "customer_name": forms.TextInput(attrs={"placeholder": "Customer full name"}),
            "customer_phone": forms.TextInput(attrs={"placeholder": "Example: 0712345678"}),
            "origin": forms.TextInput(attrs={"placeholder": "Origin / pickup area"}),
            "destination": forms.TextInput(attrs={"placeholder": "Destination"}),
            "amount_charged": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Optional notes"}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["trip_date"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["vehicle"].queryset = Vehicle.objects.filter(is_active=True).order_by("plate_number")
        self.fields["driver"].queryset = Driver.objects.filter(is_active=True).select_related("user").order_by(
            "first_name", "last_name", "user__username"
        )

        profile = getattr(user, "profile", None) if user else None
        role = (getattr(profile, "role", "") or "").lower()
        branch = getattr(profile, "branch", None)
        if role == "transport" and branch:
            self.fields["branch"].initial = branch
            self.fields["branch"].queryset = Branch.objects.filter(pk=branch.pk)
            self.fields["branch"].disabled = True
            self.fields["driver"].queryset = self.fields["driver"].queryset.filter(branch=branch)

        _add_bootstrap_classes(self)

    def clean_customer_name(self):
        name = (self.cleaned_data.get("customer_name") or "").strip()
        if not name:
            raise forms.ValidationError("Customer name is required.")
        return name

    def clean_customer_phone(self):
        phone = (self.cleaned_data.get("customer_phone") or "").strip()
        if not phone:
            raise forms.ValidationError("Customer phone is required for automatic SMS.")
        return phone

    def clean(self):
        cleaned = super().clean()
        driver = cleaned.get("driver")
        vehicle = cleaned.get("vehicle")
        branch = cleaned.get("branch")
        if driver and driver.branch_id and branch and driver.branch_id != branch.pk:
            self.add_error("driver", "The selected driver belongs to another branch.")
        if driver and driver.vehicle_id and vehicle and driver.vehicle_id != vehicle.pk:
            self.add_error("vehicle", "The selected vehicle is not the driver's assigned vehicle.")
        return cleaned


class DriverTripForm(forms.ModelForm):
    class Meta:
        model = Trip
        fields = [
            "trip_date",
            "customer_name",
            "customer_phone",
            "origin",
            "destination",
            "amount_charged",
            "notes",
        ]
        widgets = {
            "trip_date": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "customer_name": forms.TextInput(attrs={"placeholder": "Customer full name"}),
            "customer_phone": forms.TextInput(attrs={"placeholder": "Example: 0712345678"}),
            "origin": forms.TextInput(attrs={"placeholder": "From / pickup area"}),
            "destination": forms.TextInput(attrs={"placeholder": "Destination"}),
            "amount_charged": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Optional trip notes"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["trip_date"].input_formats = ["%Y-%m-%dT%H:%M"]
        if not self.is_bound and not self.instance.pk:
            self.fields["trip_date"].initial = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        _add_bootstrap_classes(self)

    def clean_customer_name(self):
        name = (self.cleaned_data.get("customer_name") or "").strip()
        if not name:
            raise forms.ValidationError("Customer name is required.")
        return name

    def clean_customer_phone(self):
        phone = (self.cleaned_data.get("customer_phone") or "").strip()
        if not phone:
            raise forms.ValidationError("Customer phone is required for automatic SMS.")
        return phone


# Backwards-compatible name used by older imports.
TripForm = AdminTripForm


class TransportSMSTemplateForm(forms.ModelForm):
    class Meta:
        model = TransportSMSTemplate
        fields = ["message", "sender_id", "is_active"]
        widgets = {
            "message": forms.Textarea(attrs={"rows": 9}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sender_id"].queryset = self.fields["sender_id"].queryset.filter(
            is_active=True
        ).order_by("-is_default", "name")
        _add_bootstrap_classes(self)


class TransportExpenseForm(forms.ModelForm):
    class Meta:
        model = TransportExpense
        fields = ["branch", "vehicle", "expense_type", "amount", "description", "expense_date"]
        widgets = {
            "expense_date": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["expense_date"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["vehicle"].required = False
        self.fields["vehicle"].queryset = Vehicle.objects.filter(is_active=True).order_by("plate_number")
        self.fields["vehicle"].empty_label = "No Vehicle / General Expense"

        profile = getattr(user, "profile", None) if user else None
        branch = getattr(profile, "branch", None)
        role = (getattr(profile, "role", "") or "").lower()
        if role == "transport" and branch:
            self.fields["branch"].initial = branch
            self.fields["branch"].queryset = Branch.objects.filter(pk=branch.pk)
            self.fields["branch"].disabled = True

        _add_bootstrap_classes(self)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("expense_type") in ["fuel", "repair", "service"] and not cleaned.get("vehicle"):
            self.add_error("vehicle", "Vehicle is required for fuel, repair, or service expenses.")
        return cleaned


class BookingForm(forms.ModelForm):
    class Meta:
        model = Booking
        fields = [
            "branch",
            "customer_name",
            "customer_phone",
            "pickup_area",
            "destination",
            "booking_datetime",
            "notes",
            "status",
        ]
        widgets = {
            "customer_name": forms.TextInput(attrs={"placeholder": "Customer full name"}),
            "customer_phone": forms.TextInput(attrs={"placeholder": "Phone number"}),
            "pickup_area": forms.TextInput(attrs={"placeholder": "Pickup area / street / place"}),
            "destination": forms.TextInput(attrs={"placeholder": "Destination area"}),
            "booking_datetime": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Extra details"}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["booking_datetime"].input_formats = ["%Y-%m-%dT%H:%M"]
        profile = getattr(user, "profile", None) if user else None
        branch = getattr(profile, "branch", None)
        role = (getattr(profile, "role", "") or "").lower()
        if role == "transport" and branch:
            self.fields["branch"].initial = branch
            self.fields["branch"].queryset = Branch.objects.filter(pk=branch.pk)
            self.fields["branch"].disabled = True
        _add_bootstrap_classes(self)
