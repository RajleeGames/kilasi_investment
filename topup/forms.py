from decimal import Decimal
from django import forms

from inventory.models import Branch, Product
from sales.models import Customer
from .models import TopupTransaction, UsedTopupStock, UsedTopupSale


class TopupCreateForm(forms.Form):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all().order_by("name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    customer_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Customer name (optional)"}),
    )
    customer_phone = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Customer phone (optional)"}),
    )

    branch = forms.ModelChoiceField(
        queryset=Branch.objects.filter(is_active=True).order_by("name"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    new_product = forms.ModelChoiceField(
        queryset=Product.objects.select_related("category").all().order_by("name"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    new_product_qty = forms.IntegerField(
        min_value=1,
        initial=1,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )

    used_item_name = forms.CharField(
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Used item name"}),
    )
    used_item_category = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Used item category"}),
    )
    used_item_condition = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Condition"}),
    )
    used_item_buying_price = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    addon_amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Optional note"}),
    )


class UsedTopupStockUpdateForm(forms.ModelForm):
    class Meta:
        model = UsedTopupStock
        fields = ["asking_price", "note"]
        widgets = {
            "asking_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class UsedTopupSellForm(forms.Form):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all().order_by("name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    customer_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Customer name (optional)"}),
    )
    customer_phone = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Customer phone (optional)"}),
    )
    sold_price = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Optional note"}),
    )