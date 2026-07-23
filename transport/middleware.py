from django.shortcuts import redirect
from django.urls import Resolver404, resolve


class DriverPortalMiddleware:
    """
    Keeps authenticated drivers inside the transport portal.

    Put this immediately after AuthenticationMiddleware in settings.MIDDLEWARE.
    View-level permission checks still remain the main security layer.
    """

    allowed_non_transport_url_names = {
        "logout",
        "password_change",
        "password_change_done",
        "profile_update",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return self.get_response(request)

        try:
            driver = user.driver_profile
        except Exception:
            driver = None

        if not driver:
            return self.get_response(request)

        if request.path.startswith(("/static/", "/media/", "/favicon")):
            return self.get_response(request)

        try:
            match = resolve(request.path_info)
        except Resolver404:
            return self.get_response(request)

        if match.namespace == "transport" or match.url_name in self.allowed_non_transport_url_names:
            return self.get_response(request)

        return redirect("transport:driver_dashboard")
