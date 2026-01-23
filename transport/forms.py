# transport/forms.py
from django import forms
from django.contrib.auth.models import User
from .models import Vehicle, Driver, Trip, TransportExpense
from inventory.models import Branch


# -------------------------
# Vehicle Form
# -------------------------
class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = [
            'plate_number',
            'model',
            'capacity',
            'is_active',
        ]


from django import forms
from django.contrib.auth.models import User
from .models import Driver

# transport/forms.py
from django import forms
from django.contrib.auth.models import User
from .models import Driver

# transport/forms.py
from django import forms
from .models import Vehicle, Driver, Trip, TransportExpense
from inventory.models import Branch

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
        fields = ["branch", "trip_date", "vehicle", "driver", "origin", "destination", "amount_charged", "notes"]
        widgets = {
            "trip_date": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "origin": forms.TextInput(attrs={"placeholder": "Origin (e.g. Dar)"}),
            "destination": forms.TextInput(attrs={"placeholder": "Destination (e.g. Moshi)"}),
            "amount_charged": forms.NumberInput(attrs={"placeholder": "Amount charged"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Optional notes..."}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # Bootstrap classes (NO Select2 here = avoids dropdown bugs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select")
            else:
                field.widget.attrs.setdefault("class", "form-control")

        # Branch locking for users with a branch
        if user and hasattr(user, "profile") and user.profile.branch:
            self.fields["branch"].initial = user.profile.branch
            self.fields["branch"].queryset = Branch.objects.filter(pk=user.profile.branch.pk)
            self.fields["branch"].disabled = True



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

        # Sync first_name & last_name
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
            'expense_date': forms.DateTimeInput(
                attrs={'type': 'datetime-local'}
            )
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})

        self.fields['vehicle'].queryset = Vehicle.objects.filter(is_active=True)

        if user and hasattr(user, 'profile') and user.profile.branch:
            self.fields['branch'].initial = user.profile.branch
            self.fields['branch'].queryset = Branch.objects.filter(
                pk=user.profile.branch.pk
            )
            self.fields['branch'].disabled = True
