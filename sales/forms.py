# sales/forms.py
from django import forms
from django.forms import inlineformset_factory

from inventory.models import Branch, Product
from .models import StockTransfer, StockTransferItem


def sales_branches_qs():
    # SAME RULE AS YOUR DASHBOARD FILTER
    return (
        Branch.objects.filter(is_active=True)
        .exclude(name__icontains="transport")
        .exclude(name__icontains="hama na")
        .exclude(name__icontains="hq")
        .order_by("name")
    )


class StockTransferForm(forms.ModelForm):
    class Meta:
        model = StockTransfer
        fields = ["from_branch", "to_branch", "note"]
        widgets = {
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Optional note..."}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)

        # ✅ apply bootstrap classes
        self.fields["from_branch"].widget.attrs.update({"class": "form-select"})
        self.fields["to_branch"].widget.attrs.update({"class": "form-select"})

        # ✅ ONLY SALES BRANCHES HERE (this is the main fix)
        allowed = sales_branches_qs()
        self.fields["from_branch"].queryset = allowed
        self.fields["to_branch"].queryset = allowed

        # ✅ seller: lock from_branch to their branch and disable field
        if user and hasattr(user, "profile") and (user.profile.role or "").lower() == "seller":
            b = getattr(user.profile, "branch", None)
            if b:
                self.fields["from_branch"].initial = b
                self.fields["from_branch"].queryset = allowed.filter(pk=b.pk)  # only their branch
                self.fields["from_branch"].disabled = True  # cannot change


class StockTransferItemForm(forms.ModelForm):
    class Meta:
        model = StockTransferItem
        fields = ["product", "quantity"]
        widgets = {
            "product": forms.Select(attrs={"class": "form-select product-select"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
        }


StockTransferItemFormSet = inlineformset_factory(
    StockTransfer,
    StockTransferItem,
    form=StockTransferItemForm,
    extra=1,
    can_delete=True
)


# sales/forms.py
from django import forms
from .models import Debtor

class DebtorForm(forms.ModelForm):
    class Meta:
        model = Debtor
        fields = ["name", "phone", "location", "amount_owed", "note", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class":"form-control", "placeholder":"Full name"}),
            "phone": forms.TextInput(attrs={"class":"form-control", "placeholder":"Phone number"}),
            "location": forms.TextInput(attrs={"class":"form-control", "placeholder":"Location (optional)"}),
            "amount_owed": forms.NumberInput(attrs={"class":"form-control", "min":"0", "step":"0.01"}),
            "note": forms.Textarea(attrs={"class":"form-control", "rows":2, "placeholder":"Note (optional)"}),
            "is_active": forms.CheckboxInput(attrs={"class":"form-check-input"}),
        }


# sales/forms.py
from django import forms
from .models import DebtorPayment

class DebtorPaymentForm(forms.ModelForm):
    class Meta:
        model = DebtorPayment
        fields = ["amount", "method", "note"]
        widgets = {
            "amount": forms.NumberInput(attrs={"class":"form-control", "min":"0", "step":"0.01", "placeholder":"Amount paid"}),
            "method": forms.Select(attrs={"class":"form-select"}, choices=[
                ("", "Select method (optional)"),
                ("cash", "Cash"),
                ("bank", "Bank"),
                ("ebt", "EBT"),
            ]),
            "note": forms.TextInput(attrs={"class":"form-control", "placeholder":"Note (optional)"}),
        }
