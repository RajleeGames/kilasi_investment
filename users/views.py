# users/views.py
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from .forms import ProfileUpdateForm
from .models import Profile


from django.contrib.auth.views import LoginView
from django.urls import reverse
from django.contrib import messages
from .models import Profile, Role

class RoleBasedLoginView(LoginView):
    template_name = "login.html"

    def form_invalid(self, form):
        messages.error(self.request, "Login failed. Check username/password.")
        return self.render_to_response(self.get_context_data(form=form))

    def get_success_url(self):
        user = self.request.user

        # Ensure profile exists
        profile, created = Profile.objects.get_or_create(user=user)

        # ✅ Auto-detect Transport users (Driver table)
        try:
            from transport.models import Driver
            if Driver.objects.filter(user=user).exists():
                if profile.role != Role.TRANSPORT:
                    profile.role = Role.TRANSPORT
                    profile.save(update_fields=["role"])
        except Exception:
            pass

        return reverse("dashboard")

# users/views.py
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from inventory.models import Branch
from .models import Profile
from users.utils import role_required
from users.models import Role


@login_required
def profile_update(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    is_admin = request.user.is_superuser or (profile.role == Role.ADMIN)

    if request.method == "POST":
        profile.full_name = (request.POST.get("full_name") or "").strip()
        profile.phone_number = (request.POST.get("phone_number") or "").strip()

        # picture upload
        if request.FILES.get("picture"):
            profile.picture = request.FILES["picture"]

        # ✅ Branch assignment logic
        # Admin can change branch from dropdown
        # Non-admin cannot change branch (locked)
        if is_admin:
            branch_id = (request.POST.get("branch") or "").strip()
            if branch_id:
                b = Branch.objects.filter(pk=branch_id, is_active=True).first()
                profile.branch = b
            else:
                profile.branch = None

        profile.save()
        messages.success(request, "Profile updated successfully.")
        return redirect("users:profile_update")  # ✅ FIXED

    branches = Branch.objects.filter(is_active=True).order_by("name") if is_admin else []

    return render(request, "users/profile_update.html", {
        "profile": profile,
        "branches": branches,
        "is_admin": is_admin,
    })
