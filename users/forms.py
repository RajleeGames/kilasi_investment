from django import forms
from django.contrib.auth.models import User
from .models import Profile

class ProfileUpdateForm(forms.ModelForm):
    full_name = forms.CharField(max_length=100, required=True)
    
    class Meta:
        model = Profile
        fields = ['phone_number', 'picture']

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields['full_name'].initial = user.get_full_name()

    def save(self, commit=True):
        profile = super().save(commit=False)
        full_name = self.cleaned_data.get('full_name')
        if full_name and hasattr(self, 'user'):
            first, *last = full_name.strip().split(' ', 1)
            self.user.first_name = first
            self.user.last_name = last[0] if last else ''
            if commit:
                self.user.save()
        if commit:
            profile.save()
        return profile
