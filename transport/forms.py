from django import forms
from django.contrib.auth.models import User

from .models import Vehicle, Driver, Trip, TransportExpense, Booking
from inventory.models import Branch


# -------------------------
# Vehicle Form
# -------------------------
class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = ['plate_number', 'model', 'capacity', 'is_active']


# -------------------------
# Trip Form
# -------------------------
class TripForm(forms.ModelForm):
    vehicle = forms.ModelChoiceField(
        queryset=Vehicle.objects.filter(is_active=True).order_by("plate_number"),
        empty_label="Select Vehicle",
        label="Vehicle",
        widget=forms.Select()
    )
    driver = forms.ModelChoiceField(
        queryset=Driver.objects.filter(is_active=True).select_related("user").order_by("user__username"),
        empty_label="Select Driver",
        label="Driver",
        widget=forms.Select()
    )

    class Meta:
        model = Trip
        fields = [
            "branch",
            "trip_date",
            "vehicle",
            "driver",
            "origin",
            "destination",
            "customer_phone",   # ✅ NEW
            "amount_charged",
            "notes",
        ]
        widgets = {
            "trip_date": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "origin": forms.TextInput(attrs={"placeholder": "Origin"}),
            "destination": forms.TextInput(attrs={"placeholder": "Destination"}),
            "customer_phone": forms.TextInput(attrs={"placeholder": "Customer phone number"}),
            "amount_charged": forms.NumberInput(attrs={"placeholder": "Amount charged"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Optional notes..."}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select")
            else:
                field.widget.attrs.setdefault("class", "form-control")

        if user and hasattr(user, "profile") and user.profile.branch:
            self.fields["branch"].initial = user.profile.branch
            self.fields["branch"].queryset = Branch.objects.filter(pk=user.profile.branch.pk)
            self.fields["branch"].disabled = True


# -------------------------
# Driver Form
# -------------------------
class DriverForm(forms.ModelForm):
    class Meta:
        model = Driver
        fields = [
            'first_name',
            'last_name',
            'phone',
            'license_number',
            'vehicle',
            'photo',
            'is_active',
        ]

    def save(self, commit=True):
        username = f"driver_{User.objects.count() + 1}"
        user = User.objects.create_user(
            username=username,
            password="driver123",
            first_name=self.cleaned_data.get('first_name', ''),
            last_name=self.cleaned_data.get('last_name', ''),
        )

        driver = super().save(commit=False)
        driver.user = user
        driver.is_active = True

        user.first_name = driver.first_name
        user.last_name = driver.last_name

        if commit:
            user.save()
            driver.save()

        return driver


# -------------------------
# Transport Expense Form
# -------------------------
class TransportExpenseForm(forms.ModelForm):
    class Meta:
        model = TransportExpense
        fields = [
            'branch',
            'vehicle',
            'expense_type',
            'amount',
            'description',
            'expense_date',
        ]
        widgets = {
            'expense_date': forms.DateTimeInput(attrs={'type': 'datetime-local'})
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            existing = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = (existing + ' form-control').strip()

        self.fields['vehicle'].required = False
        self.fields['vehicle'].queryset = Vehicle.objects.filter(is_active=True).order_by('plate_number')
        self.fields['vehicle'].empty_label = "No Vehicle / General Expense"

        if user and hasattr(user, 'profile') and user.profile.branch:
            self.fields['branch'].initial = user.profile.branch
            self.fields['branch'].queryset = Branch.objects.filter(pk=user.profile.branch.pk)
            self.fields['branch'].disabled = True

    def clean(self):
        cleaned = super().clean()
        expense_type = cleaned.get('expense_type')
        vehicle = cleaned.get('vehicle')

        vehicle_required_types = ['fuel', 'repair', 'service']

        if expense_type in vehicle_required_types and not vehicle:
            self.add_error('vehicle', 'Vehicle is required for fuel, repair, or service expenses.')

        return cleaned

# -------------------------
# Booking Form
# -------------------------
class BookingForm(forms.ModelForm):
    class Meta:
        model = Booking
        fields = [
            'branch',
            'customer_name',
            'customer_phone',
            'pickup_area',
            'destination',
            'booking_datetime',
            'notes',
            'status',
        ]
        widgets = {
            'customer_name': forms.TextInput(attrs={'placeholder': 'Customer full name'}),
            'customer_phone': forms.TextInput(attrs={'placeholder': 'Phone number'}),
            'pickup_area': forms.TextInput(attrs={'placeholder': 'Pickup area / street / place'}),
            'destination': forms.TextInput(attrs={'placeholder': 'Destination area'}),
            'booking_datetime': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Extra details...'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select")
            else:
                field.widget.attrs.setdefault("class", "form-control")

        if user and hasattr(user, 'profile') and user.profile.branch:
            self.fields['branch'].initial = user.profile.branch
            self.fields['branch'].queryset = Branch.objects.filter(pk=user.profile.branch.pk)
            self.fields['branch'].disabled = True