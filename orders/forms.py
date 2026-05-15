from django import forms

from inventory.models import Product
from .models import CustomerOrder, CustomerOrderPayment


class CustomerOrderForm(forms.ModelForm):
    advance_amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
        min_value=0,
        label="Advance Paid"
    )

    payment_method = forms.ChoiceField(
        choices=CustomerOrderPayment.METHOD_CHOICES,
        required=False,
        label="Payment Method"
    )

    class Meta:
        model = CustomerOrder
        fields = [
            "customer_name",
            "customer_phone",
            "product",
            "custom_product_name",
            "description",
            "quantity",
            "total_amount",
            "advance_amount",
            "payment_method",
            "expected_date",
            "notes",
        ]

        widgets = {
            "customer_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Customer name"}),
            "customer_phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Phone number"}),
            "product": forms.Select(attrs={"class": "form-select"}),
            "custom_product_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Example: Custom bed"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "Describe style, size, color, material..."}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "total_amount": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "advance_amount": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "payment_method": forms.Select(attrs={"class": "form-select"}),
            "expected_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Furniture products first if your Product has business_type
        qs = Product.objects.all().order_by("name")
        if hasattr(Product, "business_type"):
            qs = qs.filter(business_type="furniture")

        self.fields["product"].queryset = qs
        self.fields["product"].required = False

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        custom_product_name = cleaned.get("custom_product_name")
        total_amount = cleaned.get("total_amount") or 0
        advance_amount = cleaned.get("advance_amount") or 0

        if not product and not custom_product_name:
            raise forms.ValidationError("Choose a product or enter a custom product name.")

        if total_amount <= 0:
            raise forms.ValidationError("Total amount must be greater than 0.")

        if advance_amount and advance_amount > total_amount:
            raise forms.ValidationError("Advance paid cannot be greater than total amount.")

        return cleaned


class CustomerOrderStatusForm(forms.ModelForm):
    class Meta:
        model = CustomerOrder
        fields = ["order_status", "expected_date", "notes"]

        widgets = {
            "order_status": forms.Select(attrs={"class": "form-select"}),
            "expected_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class CustomerOrderPaymentForm(forms.ModelForm):
    class Meta:
        model = CustomerOrderPayment
        fields = ["amount", "method", "note"]

        widgets = {
            "amount": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "method": forms.Select(attrs={"class": "form-select"}),
            "note": forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional note"}),
        }