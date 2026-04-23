# kilasi_investment/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from users.views import RoleBasedLoginView
from django.contrib.auth.views import LogoutView
from sales.views import dashboard_redirect


# ─── Allow GET on logout ────────────────────────────────────────
class LogoutViaGet(LogoutView):
    http_method_names = ["get", "post"]


urlpatterns = [
    # ✅ Landing always redirects by role
    path("", dashboard_redirect, name="dashboard"),

    # Django admin (Django built-in)
    path("admin/", admin.site.urls),

    # Auth
    path("login/", RoleBasedLoginView.as_view(), name="login"),
    path("logout/", LogoutViaGet.as_view(next_page="login"), name="logout"),

    # ✅ Sales app under /seller/ with namespace "sales"
    # IMPORTANT: this requires `app_name = "sales"` in sales/urls.py
    path("seller/", include(("sales.urls", "sales"), namespace="sales")),
     
    #contacts 
    path("contacts/", include("contacts.urls")),

    # Users app
    path("users/", include(("users.urls", "users"), namespace="users")),

    # ✅ Transport app (namespaced)
    path("transport/", include(("transport.urls", "transport"), namespace="transport")),
    path("sms/", include("sms.urls")),
    
    path("topup/", include("topup.urls")),
]

# Branding
admin.site.site_header = "KILASI INVESTMENTS ADMIN"
admin.site.site_title = "KILASI INVESTMENTS ADMIN PORTAL"
admin.site.index_title = "Welcome to KILASI INVESTMENTS MANAGEMENT"

# Static + Media (DEV only)
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "static")
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
