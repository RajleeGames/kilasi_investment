# users/utils.py
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden

def role_required(allowed_roles):
    """
    Decorator for view functions that restricts access based on Profile.role.
    Usage:
        @role_required(['admin', 'cashier'])
        def my_view(request): ...
    """
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            profile = getattr(request.user, 'profile', None)
            if profile and profile.role in allowed_roles:
                return view_func(request, *args, **kwargs)
            return HttpResponseForbidden("You don't have permission to view this page.")
        return _wrapped
    return decorator


# -----------------------------
# CBV mixin: restrict queryset
# -----------------------------
from django.contrib.auth.mixins import LoginRequiredMixin

class BranchQuerysetMixin(LoginRequiredMixin):
    """
    Use this mixin on ListView/DetailView where querysets are branch-aware.
    Sellers will automatically see only their branch records.
    Admins/Cashiers see everything.
    Example:
        class SaleListView(BranchQuerysetMixin, ListView):
            model = Sale
    """
    def get_queryset(self):
        qs = super().get_queryset()
        profile = getattr(self.request.user, 'profile', None)
        if profile and profile.role == 'seller' and profile.branch:
            # assumes model has `branch` FK
            return qs.filter(branch=profile.branch)
        return qs
