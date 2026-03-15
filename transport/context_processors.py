from datetime import timedelta
from django.utils import timezone
from .models import Booking

def transport_booking_badge(request):
    if not request.user.is_authenticated:
        return {"transport_booking_badge_count": 0}

    profile = getattr(request.user, "profile", None)
    if not profile:
        return {"transport_booking_badge_count": 0}

    role = (getattr(profile, "role", "") or "").lower()
    branch = getattr(profile, "branch", None)

    if role not in ["admin", "cashier", "transport"]:
        return {"transport_booking_badge_count": 0}

    now = timezone.localtime(timezone.now())
    next_24h = now + timedelta(hours=24)

    qs = Booking.objects.filter(status="pending")

    if role == "transport":
        if branch:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.none()

    count = qs.filter(
        booking_datetime__lte=next_24h
    ).count()

    return {
        "transport_booking_badge_count": count
    }