from datetime import datetime, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from inventory.models import Branch

from .forms import (
    AdminTripForm,
    BookingForm,
    DriverForm,
    DriverTripForm,
    TransportExpenseForm,
    TransportSMSTemplateForm,
    VehicleForm,
)
from .models import Booking, Driver, TransportExpense, TransportSMSTemplate, Trip, Vehicle
from .services import SUPPORTED_PLACEHOLDERS, ensure_default_transport_templates, send_trip_sms,send_boss_trip_sms


def _profile_role(user):
    profile = getattr(user, "profile", None)
    return (getattr(profile, "role", "") or "").lower()


def _driver_for_user(user):
    try:
        driver = user.driver_profile
    except Exception:
        return None
    return driver


def _effective_role(user):
    if _driver_for_user(user):
        return "driver"
    return _profile_role(user)


def _profile_branch(user):
    profile = getattr(user, "profile", None)
    return getattr(profile, "branch", None)


def _driver_branch(driver):
    return driver.branch or _profile_branch(driver.user)


def transport_roles_required(*allowed_roles):
    allowed = {str(role).lower() for role in allowed_roles}

    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            role = _effective_role(request.user)
            if role in allowed:
                return view_func(request, *args, **kwargs)
            return HttpResponseForbidden("You do not have permission to view this page.")

        return wrapped

    return decorator


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _trip_queryset_for_user(user):
    role = _effective_role(user)
    qs = Trip.objects.select_related("vehicle", "driver__user", "driver__branch", "branch", "created_by")

    if role in {"admin", "cashier"}:
        return qs
    if role == "transport":
        branch = _profile_branch(user)
        return qs.filter(branch=branch) if branch else qs.none()
    if role == "driver":
        driver = _driver_for_user(user)
        return qs.filter(driver=driver) if driver else qs.none()
    return qs.none()


def _get_accessible_trip(user, pk):
    return get_object_or_404(_trip_queryset_for_user(user), pk=pk)


def _get_status_manageable_trip(user, pk, lock=False):
    role = _effective_role(user)
    if role not in {"admin", "transport", "driver"}:
        raise PermissionDenied

    qs = Trip.objects.select_related("vehicle", "driver__user", "driver__branch", "branch")
    if lock:
        qs = qs.select_for_update()
    trip = get_object_or_404(qs, pk=pk)

    if role == "driver" and trip.driver_id != getattr(_driver_for_user(user), "pk", None):
        raise PermissionDenied
    if role == "transport":
        branch = _profile_branch(user)
        if not branch or trip.branch_id != branch.pk:
            raise PermissionDenied
    return trip


# -----------------------------------------------------------------------------
# Transport manager dashboard
# -----------------------------------------------------------------------------
@transport_roles_required("admin", "cashier", "transport", "driver")
def index(request):
    if _effective_role(request.user) == "driver":
        return redirect("transport:driver_dashboard")

    role = _effective_role(request.user)
    branch = _profile_branch(request.user)

    if role in {"admin", "cashier"}:
        trips_base = Trip.objects.select_related("vehicle", "driver__user", "branch").all()
        expenses_base = TransportExpense.objects.select_related("vehicle", "branch", "recorded_by").all()
        bookings_base = Booking.objects.select_related("branch", "created_by").all()
    elif branch:
        trips_base = Trip.objects.select_related("vehicle", "driver__user", "branch").filter(branch=branch)
        expenses_base = TransportExpense.objects.select_related("vehicle", "branch", "recorded_by").filter(branch=branch)
        bookings_base = Booking.objects.select_related("branch", "created_by").filter(branch=branch)
    else:
        trips_base = Trip.objects.none()
        expenses_base = TransportExpense.objects.none()
        bookings_base = Booking.objects.none()

    vehicles = Vehicle.objects.filter(is_active=True).order_by("plate_number")
    today_local = timezone.localdate()
    filter_date = _parse_date((request.GET.get("date") or "").strip()) or today_local
    selected_vehicle_id = _parse_int(request.GET.get("vehicle"))

    trips_qs = trips_base.filter(trip_date__date=filter_date)
    expenses_qs = expenses_base.filter(expense_date__date=filter_date)
    if selected_vehicle_id:
        trips_qs = trips_qs.filter(vehicle_id=selected_vehicle_id)
        expenses_qs = expenses_qs.filter(vehicle_id=selected_vehicle_id)

    trips_total = trips_qs.aggregate(total=Sum("amount_charged"))["total"] or 0
    expenses_total = expenses_qs.aggregate(total=Sum("amount"))["total"] or 0
    net_total = trips_total - expenses_total

    day_trips = (
        trips_base.filter(trip_date__date=filter_date)
        .annotate(day=TruncDate("trip_date"))
        .values("day")
        .annotate(total=Sum("amount_charged"))
    )
    day_expenses = (
        expenses_base.filter(expense_date__date=filter_date)
        .annotate(day=TruncDate("expense_date"))
        .values("day")
        .annotate(total=Sum("amount"))
    )
    trips_map = {x["day"].isoformat(): float(x["total"] or 0) for x in day_trips}
    expenses_map = {x["day"].isoformat(): float(x["total"] or 0) for x in day_expenses}
    key = filter_date.isoformat()

    now = timezone.localtime()
    pending_bookings = bookings_base.filter(status="pending").order_by("booking_datetime")
    today_bookings = pending_bookings.filter(booking_datetime__date=now.date())
    upcoming_bookings = pending_bookings.filter(booking_datetime__gt=now)
    overdue_bookings = pending_bookings.filter(booking_datetime__lt=now)

    context = {
        "trips_total": trips_total,
        "trips_count": trips_qs.count(),
        "expenses_total": expenses_total,
        "net_total": net_total,
        "profit_value": net_total if net_total > 0 else 0,
        "loss_value": abs(net_total) if net_total < 0 else 0,
        "recent_trips": trips_qs.order_by("-trip_date")[:6],
        "recent_expenses": expenses_qs.order_by("-expense_date")[:6],
        "trips_chart_labels": [filter_date.strftime("%d %b")],
        "trips_chart_values": [trips_map.get(key, 0)],
        "expenses_chart_values": [expenses_map.get(key, 0)],
        "selected_date": filter_date.isoformat(),
        "vehicles": vehicles,
        "selected_vehicle_id": selected_vehicle_id or "",
        "today_bookings": today_bookings[:6],
        "upcoming_bookings": upcoming_bookings[:6],
        "overdue_bookings": overdue_bookings[:6],
        "booking_count_today": today_bookings.count(),
        "booking_count_upcoming": upcoming_bookings.count(),
        "booking_count_overdue": overdue_bookings.count(),
        "booking_count_pending": pending_bookings.count(),
    }
    return render(request, "transport/index.html", context)


# -----------------------------------------------------------------------------
# Driver portal
# -----------------------------------------------------------------------------
@transport_roles_required("driver")
def driver_dashboard(request):
    driver = _driver_for_user(request.user)
    trips = Trip.objects.select_related("vehicle", "branch").filter(driver=driver)
    today = timezone.localdate()

    context = {
        "driver": driver,
        "pending_count": trips.filter(status=Trip.STATUS_PENDING).count(),
        "in_transit_count": trips.filter(status=Trip.STATUS_IN_TRANSIT).count(),
        "completed_today_count": trips.filter(
            status=Trip.STATUS_COMPLETED,
            completed_at__date=today,
        ).count(),
        "today_count": trips.filter(trip_date__date=today).count(),
        "recent_trips": trips.order_by("-trip_date", "-id")[:8],
    }
    return render(request, "transport/driver_dashboard.html", context)


# -----------------------------------------------------------------------------
# Vehicles
# -----------------------------------------------------------------------------
@transport_roles_required("admin", "cashier", "transport")
def vehicle_list(request):
    q = (request.GET.get("q") or "").strip()
    vehicles = Vehicle.objects.all()
    if q:
        vehicles = vehicles.filter(Q(plate_number__icontains=q) | Q(model__icontains=q))
    return render(request, "transport/vehicle_list.html", {"vehicles": vehicles, "q": q})


@transport_roles_required("admin")
def vehicle_create(request):
    form = VehicleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Vehicle created successfully.")
        return redirect("transport:vehicle_list")
    return render(request, "transport/vehicle_form.html", {"form": form})


# -----------------------------------------------------------------------------
# Drivers and login accounts
# -----------------------------------------------------------------------------
@transport_roles_required("admin", "transport", "cashier")
def driver_list(request):
    q = (request.GET.get("q") or "").strip()
    branch_id = _parse_int(request.GET.get("branch"))
    qs = Driver.objects.select_related("user", "branch", "vehicle")

    if _effective_role(request.user) == "transport":
        branch = _profile_branch(request.user)
        qs = qs.filter(branch=branch) if branch else qs.none()
    elif branch_id:
        qs = qs.filter(branch_id=branch_id)

    if q:
        qs = qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(user__username__icontains=q)
            | Q(phone__icontains=q)
            | Q(license_number__icontains=q)
            | Q(vehicle__plate_number__icontains=q)
        )

    page_obj = Paginator(qs.order_by("first_name", "last_name", "user__username"), 25).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "transport/driver_list.html",
        {
            "drivers": page_obj.object_list,
            "page_obj": page_obj,
            "q": q,
            "branches": Branch.objects.all().order_by("name") if _effective_role(request.user) == "admin" else [],
            "selected_branch_id": branch_id or "",
            "is_admin": _effective_role(request.user) == "admin",
        },
    )


@transport_roles_required("admin")
def driver_create(request):
    form = DriverForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        driver = form.save()
        messages.success(
            request,
            f"Driver {driver.full_name} created. Username: {driver.user.username}.",
        )
        return redirect("transport:driver_list")
    return render(request, "transport/driver_form.html", {"form": form, "is_edit": False})


@transport_roles_required("admin")
def driver_edit(request, pk):
    driver = get_object_or_404(Driver.objects.select_related("user"), pk=pk)
    form = DriverForm(request.POST or None, request.FILES or None, instance=driver)
    if request.method == "POST" and form.is_valid():
        driver = form.save()
        messages.success(request, f"Driver {driver.full_name} updated successfully.")
        return redirect("transport:driver_list")
    return render(
        request,
        "transport/driver_form.html",
        {"form": form, "is_edit": True, "driver": driver},
    )


# -----------------------------------------------------------------------------
# Trips
# -----------------------------------------------------------------------------
@transport_roles_required("admin", "cashier", "transport", "driver")
def trip_list(request):
    role = _effective_role(request.user)
    trips = _trip_queryset_for_user(request.user)

    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    single_date = _parse_date((request.GET.get("date") or "").strip())
    start_date = _parse_date((request.GET.get("start_date") or "").strip()) or single_date
    end_date = _parse_date((request.GET.get("end_date") or "").strip()) or single_date
    vehicle_id = _parse_int(request.GET.get("vehicle"))
    driver_id = _parse_int(request.GET.get("driver"))

    if q:
        trips = trips.filter(
            Q(customer_name__icontains=q)
            | Q(customer_phone__icontains=q)
            | Q(origin__icontains=q)
            | Q(destination__icontains=q)
            | Q(vehicle__plate_number__icontains=q)
            | Q(driver__first_name__icontains=q)
            | Q(driver__last_name__icontains=q)
            | Q(driver__user__username__icontains=q)
        )
    if status in dict(Trip.STATUS_CHOICES):
        trips = trips.filter(status=status)
    if start_date:
        trips = trips.filter(trip_date__date__gte=start_date)
    if end_date:
        trips = trips.filter(trip_date__date__lte=end_date)
    if vehicle_id:
        trips = trips.filter(vehicle_id=vehicle_id)
    if driver_id and role in {"admin", "cashier", "transport"}:
        trips = trips.filter(driver_id=driver_id)

    trips = trips.order_by("-trip_date", "-id")
    page_obj = Paginator(trips, 50).get_page(request.GET.get("page"))

    drivers = Driver.objects.filter(is_active=True).select_related("user").order_by("first_name", "last_name")
    if role == "transport":
        branch = _profile_branch(request.user)
        drivers = drivers.filter(branch=branch) if branch else drivers.none()
    elif role == "driver":
        drivers = drivers.filter(pk=getattr(_driver_for_user(request.user), "pk", None))

    context = {
        "trips": page_obj.object_list,
        "page_obj": page_obj,
        "total_trips_count": page_obj.paginator.count,
        "role": role,
        "is_driver": role == "driver",
        "can_change_status": role in {"admin", "transport", "driver"},
        "can_retry_sms": role in {"admin", "transport"},
        "q": q,
        "selected_status": status,
        "start_date": start_date.isoformat() if start_date else "",
        "end_date": end_date.isoformat() if end_date else "",
        "vehicles": Vehicle.objects.filter(is_active=True).order_by("plate_number"),
        "selected_vehicle_id": vehicle_id or "",
        "drivers": drivers,
        "selected_driver_id": driver_id or "",
        "status_choices": Trip.STATUS_CHOICES,
    }
    return render(request, "transport/trip_list.html", context)


@transport_roles_required("admin", "transport", "driver")
def trip_create(request):
    role = _effective_role(request.user)
    driver = _driver_for_user(request.user) if role == "driver" else None
    form_class = DriverTripForm if role == "driver" else AdminTripForm
    kwargs = {"data": request.POST or None}
    if form_class is AdminTripForm:
        kwargs["user"] = request.user
    form = form_class(**kwargs)

    if request.method == "POST" and form.is_valid():
        if role == "driver":
            branch = _driver_branch(driver)
            if not driver.is_active:
                form.add_error(None, "Your driver account is inactive. Contact the administrator.")
            elif not branch:
                form.add_error(None, "No branch is assigned to your driver account.")
            elif not driver.vehicle:
                form.add_error(None, "No vehicle is assigned to your driver account.")

        if not form.errors:
            with transaction.atomic():
                trip = form.save(commit=False)
                trip.created_by = request.user
                trip.status = Trip.STATUS_PENDING
                if role == "driver":
                    trip.driver = driver
                    trip.vehicle = driver.vehicle
                    trip.branch = _driver_branch(driver)
                elif role == "transport" and _profile_branch(request.user):
                    trip.branch = _profile_branch(request.user)
                trip.save()
                transaction.on_commit(
                    lambda trip_id=trip.pk, user_id=request.user.pk: send_trip_sms(
                        trip_id,
                        TransportSMSTemplate.EVENT_PENDING,
                        user_id,
                    )
                )
                transaction.on_commit(
    lambda trip_id=trip.pk, user_id=request.user.pk: send_boss_trip_sms(
        trip_id,
        user_id,
    )
)

            messages.success(request, "Trip saved as pending. The customer SMS was processed automatically.")
            return redirect("transport:trip_detail", pk=trip.pk)

    return render(
        request,
        "transport/trip_form.html",
        {"form": form, "role": role, "driver": driver},
    )


@transport_roles_required("admin", "cashier", "transport", "driver")
def trip_detail(request, pk):
    trip = _get_accessible_trip(request.user, pk)
    role = _effective_role(request.user)
    return render(
        request,
        "transport/trip_detail.html",
        {
            "trip": trip,
            "role": role,
            "can_change_status": role in {"admin", "transport", "driver"},
            "can_retry_sms": role in {"admin", "transport"},
        },
    )


@require_POST
@transport_roles_required("admin", "transport", "driver")
def trip_status_update(request, pk):
    action = (request.POST.get("action") or "").strip().lower()

    with transaction.atomic():
        trip = _get_status_manageable_trip(request.user, pk, lock=True)
        now = timezone.now()

        if action == "start":
            if trip.status != Trip.STATUS_PENDING:
                messages.error(request, "Only a pending trip can be started.")
                return redirect("transport:trip_detail", pk=trip.pk)
            trip.status = Trip.STATUS_IN_TRANSIT
            trip.started_at = now
            trip.cancelled_at = None
            trip.save(update_fields=["status", "started_at", "cancelled_at", "updated_at"])
            messages.success(request, "Journey marked as in transit.")

        elif action == "complete":
            if trip.status != Trip.STATUS_IN_TRANSIT:
                messages.error(request, "Start the journey before completing it.")
                return redirect("transport:trip_detail", pk=trip.pk)
            trip.status = Trip.STATUS_COMPLETED
            trip.completed_at = now
            trip.save(update_fields=["status", "completed_at", "updated_at"])
            transaction.on_commit(
                lambda trip_id=trip.pk, user_id=request.user.pk: send_trip_sms(
                    trip_id,
                    TransportSMSTemplate.EVENT_COMPLETED,
                    user_id,
                )
            )
            messages.success(request, "Trip completed. The arrival SMS was processed automatically.")

        elif action == "cancel":
            if trip.status in {Trip.STATUS_COMPLETED, Trip.STATUS_CANCELLED}:
                messages.error(request, "This trip is already closed.")
                return redirect("transport:trip_detail", pk=trip.pk)
            trip.status = Trip.STATUS_CANCELLED
            trip.cancelled_at = now
            trip.save(update_fields=["status", "cancelled_at", "updated_at"])
            messages.success(request, "Trip cancelled.")

        else:
            messages.error(request, "Unknown trip action.")

    return redirect("transport:trip_detail", pk=pk)


@require_POST
@transport_roles_required("admin", "transport")
def trip_retry_sms(request, pk, event):
    trip = _get_status_manageable_trip(request.user, pk)
    if event not in {
        TransportSMSTemplate.EVENT_PENDING,
        TransportSMSTemplate.EVENT_COMPLETED,
    }:
        messages.error(request, "Unknown SMS type.")
        return redirect("transport:trip_detail", pk=trip.pk)

    if event == TransportSMSTemplate.EVENT_COMPLETED and trip.status != Trip.STATUS_COMPLETED:
        messages.error(request, "Completion SMS can only be sent after the trip is completed.")
        return redirect("transport:trip_detail", pk=trip.pk)

    result = send_trip_sms(trip.pk, event, request.user.pk)
    if result.get("ok"):
        messages.success(request, result.get("reason") or "SMS sent successfully.")
    else:
        messages.error(request, f"SMS failed: {result.get('error') or result.get('reason') or 'Unknown error'}")
    return redirect("transport:trip_detail", pk=trip.pk)


# -----------------------------------------------------------------------------
# Automatic transport SMS settings
# -----------------------------------------------------------------------------
@transport_roles_required("admin")
def sms_template_list(request):
    ensure_default_transport_templates()
    templates = TransportSMSTemplate.objects.select_related("sender_id", "updated_by").all()
    return render(
        request,
        "transport/sms_template_list.html",
        {"templates": templates, "placeholders": SUPPORTED_PLACEHOLDERS},
    )


@transport_roles_required("admin")
def sms_template_edit(request, pk):
    ensure_default_transport_templates()
    template = get_object_or_404(TransportSMSTemplate, pk=pk)
    form = TransportSMSTemplateForm(request.POST or None, instance=template)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.updated_by = request.user
        obj.save()
        messages.success(request, f"{obj.get_event_display()} message updated successfully.")
        return redirect("transport:sms_template_list")
    return render(
        request,
        "transport/sms_template_form.html",
        {"form": form, "template": template, "placeholders": SUPPORTED_PLACEHOLDERS},
    )


# -----------------------------------------------------------------------------
# Expenses
# -----------------------------------------------------------------------------
@transport_roles_required("admin", "transport", "cashier")
def expense_list(request):
    role = _effective_role(request.user)
    branch = _profile_branch(request.user)
    qs = TransportExpense.objects.select_related("vehicle", "branch", "recorded_by")

    if role == "transport":
        qs = qs.filter(branch=branch) if branch else qs.none()

    filter_date = _parse_date((request.GET.get("date") or "").strip())
    vehicle_id = _parse_int(request.GET.get("vehicle"))
    scope = (request.GET.get("scope") or "").strip()
    if filter_date:
        qs = qs.filter(expense_date__date=filter_date)
    if vehicle_id:
        qs = qs.filter(vehicle_id=vehicle_id)
    if scope == "vehicle":
        qs = qs.filter(vehicle__isnull=False)
    elif scope == "general":
        qs = qs.filter(vehicle__isnull=True)

    page_obj = Paginator(qs.order_by("-expense_date", "-id"), 50).get_page(request.GET.get("page"))
    return render(
        request,
        "transport/expense_list.html",
        {
            "expenses": page_obj.object_list,
            "page_obj": page_obj,
            "role": role,
            "selected_date": filter_date.isoformat() if filter_date else "",
            "vehicles": Vehicle.objects.filter(is_active=True).order_by("plate_number"),
            "selected_vehicle_id": vehicle_id or "",
            "selected_scope": scope,
            "is_cashier": role == "cashier",
            "is_admin": role == "admin",
            "is_transport": role == "transport",
        },
    )


@transport_roles_required("admin", "transport")
def expense_create(request):
    form = TransportExpenseForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        expense = form.save(commit=False)
        expense.recorded_by = request.user
        if _effective_role(request.user) == "transport" and _profile_branch(request.user):
            expense.branch = _profile_branch(request.user)
        expense.save()
        messages.success(request, "Expense recorded successfully.")
        return redirect("transport:expense_list")
    return render(request, "transport/expense_form.html", {"form": form})


# -----------------------------------------------------------------------------
# Bookings
# -----------------------------------------------------------------------------
@transport_roles_required("admin", "cashier", "transport")
def booking_list(request):
    role = _effective_role(request.user)
    branch = _profile_branch(request.user)
    qs = Booking.objects.select_related("branch", "created_by")
    if role == "transport":
        qs = qs.filter(branch=branch) if branch else qs.none()

    filter_date = _parse_date((request.GET.get("date") or "").strip())
    status = (request.GET.get("status") or "").strip()
    if filter_date:
        qs = qs.filter(booking_datetime__date=filter_date)
    if status in dict(Booking.STATUS_CHOICES):
        qs = qs.filter(status=status)

    page_obj = Paginator(qs.order_by("booking_datetime", "-id"), 50).get_page(request.GET.get("page"))
    return render(
        request,
        "transport/booking_list.html",
        {
            "bookings": page_obj.object_list,
            "page_obj": page_obj,
            "selected_date": filter_date.isoformat() if filter_date else "",
            "selected_status": status,
        },
    )


@transport_roles_required("admin", "cashier", "transport")
def booking_create(request):
    form = BookingForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        booking = form.save(commit=False)
        booking.created_by = request.user
        if _effective_role(request.user) == "transport" and _profile_branch(request.user):
            booking.branch = _profile_branch(request.user)
        booking.status = booking.status or "pending"
        booking.save()
        messages.success(request, "Booking added successfully.")
        return redirect("transport:booking_list")
    return render(request, "transport/booking_form.html", {"form": form})


@transport_roles_required("admin", "cashier", "transport")
def booking_edit(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    if _effective_role(request.user) == "transport":
        branch = _profile_branch(request.user)
        if not branch or booking.branch_id != branch.pk:
            raise PermissionDenied

    form = BookingForm(request.POST or None, instance=booking, user=request.user)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if _effective_role(request.user) == "transport" and _profile_branch(request.user):
            obj.branch = _profile_branch(request.user)
        obj.save()
        messages.success(request, "Booking updated successfully.")
        return redirect("transport:booking_list")
    return render(request, "transport/booking_form.html", {"form": form, "is_edit": True, "booking": booking})


@transport_roles_required("admin", "cashier", "transport")
def booking_delete(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    if _effective_role(request.user) == "transport":
        branch = _profile_branch(request.user)
        if not branch or booking.branch_id != branch.pk:
            raise PermissionDenied

    if request.method == "POST":
        booking.delete()
        messages.success(request, "Booking deleted successfully.")
        return redirect("transport:booking_list")
    return render(request, "transport/booking_confirm_delete.html", {"booking": booking})
