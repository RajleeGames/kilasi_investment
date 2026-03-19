from datetime import datetime, timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import Vehicle, Driver, Trip, TransportExpense, Booking
from .forms import VehicleForm, DriverForm, TripForm, TransportExpenseForm, BookingForm


try:
    from users.utils import role_required
except Exception:
    from functools import wraps
    from django.http import HttpResponseForbidden

    def role_required(allowed_roles):
        def decorator(view_func):
            @login_required
            @wraps(view_func)
            def _wrapped(request, *args, **kwargs):
                profile = getattr(request.user, 'profile', None)
                role = (getattr(profile, "role", "") or "").lower()
                if profile and role in allowed_roles:
                    return view_func(request, *args, **kwargs)
                return HttpResponseForbidden("You don't have permission to view this page.")
            return _wrapped
        return decorator


def _role(request):
    profile = getattr(request.user, "profile", None)
    return (getattr(profile, "role", "") or "").lower()


def _branch(request):
    profile = getattr(request.user, "profile", None)
    return getattr(profile, "branch", None)


def _parse_date(date_str: str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return None


def _parse_int(value):
    try:
        return int(value)
    except Exception:
        return None


# -------------------------
# Dashboard
# -------------------------
@login_required
@role_required(['admin', 'cashier', 'transport'])
def index(request):
    role = _role(request)
    branch = _branch(request)

    if role in ['admin', 'cashier']:
        trips_qs = Trip.objects.select_related('vehicle', 'driver', 'branch').all()
        expenses_qs = TransportExpense.objects.select_related('vehicle', 'branch', 'recorded_by').all()
        bookings_qs = Booking.objects.select_related('branch', 'created_by').all()
        vehicles = Vehicle.objects.filter(is_active=True).order_by('plate_number')
    else:
        if branch:
            trips_qs = Trip.objects.select_related('vehicle', 'driver', 'branch').filter(branch=branch)
            expenses_qs = TransportExpense.objects.select_related('vehicle', 'branch', 'recorded_by').filter(branch=branch)
            bookings_qs = Booking.objects.select_related('branch', 'created_by').filter(branch=branch)
            vehicles = Vehicle.objects.filter(is_active=True).order_by('plate_number')
        else:
            trips_qs = Trip.objects.none()
            expenses_qs = TransportExpense.objects.none()
            bookings_qs = Booking.objects.none()
            vehicles = Vehicle.objects.none()

    # -------------------------
    # Default date = today
    # -------------------------
    today_local = timezone.localtime(timezone.now()).date()
    selected_date_str = request.GET.get("date", "").strip()
    filter_date = _parse_date(selected_date_str) or today_local

    # Vehicle filter
    selected_vehicle_id = _parse_int(request.GET.get("vehicle"))
    selected_vehicle = None

    if selected_vehicle_id:
        selected_vehicle = vehicles.filter(pk=selected_vehicle_id).first()
        if selected_vehicle:
            trips_qs = trips_qs.filter(vehicle_id=selected_vehicle.pk)
            expenses_qs = expenses_qs.filter(vehicle_id=selected_vehicle.pk)

    # Today by default (or chosen date)
    trips_qs = trips_qs.filter(trip_date__date=filter_date)
    expenses_qs = expenses_qs.filter(expense_date__date=filter_date)

    trips_total = trips_qs.aggregate(total=Sum('amount_charged')).get('total') or 0
    trips_count = trips_qs.count()
    expenses_total = expenses_qs.aggregate(total=Sum('amount')).get('total') or 0

    try:
        net_total = (trips_total or 0) - (expenses_total or 0)
    except Exception:
        net_total = float(trips_total or 0) - float(expenses_total or 0)

    try:
        net_float = float(net_total or 0)
    except Exception:
        net_float = 0.0

    profit_value = net_float if net_float > 0 else 0.0
    loss_value = abs(net_float) if net_float < 0 else 0.0

    recent_trips = trips_qs.order_by('-trip_date')[:6]
    recent_expenses = expenses_qs.order_by('-expense_date')[:6]

    # For chart:
    # if vehicle selected and date selected -> that day only
    # otherwise today only (dashboard default)
    days = 1
    start_date = filter_date

    trips_by_day_qs = (
        Trip.objects.select_related('vehicle', 'driver', 'branch')
        .filter(trip_date__date__gte=start_date, trip_date__date__lte=filter_date)
    )
    expenses_by_day_qs = (
        TransportExpense.objects.select_related('vehicle', 'branch', 'recorded_by')
        .filter(expense_date__date__gte=start_date, expense_date__date__lte=filter_date)
    )

    if role == "transport":
        if branch:
            trips_by_day_qs = trips_by_day_qs.filter(branch=branch)
            expenses_by_day_qs = expenses_by_day_qs.filter(branch=branch)
        else:
            trips_by_day_qs = Trip.objects.none()
            expenses_by_day_qs = TransportExpense.objects.none()

    if selected_vehicle:
        trips_by_day_qs = trips_by_day_qs.filter(vehicle=selected_vehicle)
        expenses_by_day_qs = expenses_by_day_qs.filter(vehicle=selected_vehicle)

    trips_by_day_qs = (
        trips_by_day_qs
        .annotate(day=TruncDate('trip_date'))
        .values('day')
        .annotate(total=Sum('amount_charged'))
        .order_by('day')
    )

    expenses_by_day_qs = (
        expenses_by_day_qs
        .annotate(day=TruncDate('expense_date'))
        .values('day')
        .annotate(total=Sum('amount'))
        .order_by('day')
    )

    trips_map = {
        entry['day'].isoformat(): float(entry['total'] or 0)
        for entry in trips_by_day_qs
    }
    expenses_map = {
        entry['day'].isoformat(): float(entry['total'] or 0)
        for entry in expenses_by_day_qs
    }

    labels = []
    trips_values = []
    expenses_values = []

    for i in range(days):
        d = start_date + timedelta(days=i)
        labels.append(d.strftime('%d %b'))
        key = d.isoformat()
        trips_values.append(trips_map.get(key, 0.0))
        expenses_values.append(expenses_map.get(key, 0.0))

    # -------------------------
    # Bookings
    # -------------------------
    now = timezone.localtime(timezone.now())
    today = now.date()

    pending_bookings = bookings_qs.filter(status='pending').order_by('booking_datetime')
    today_bookings = pending_bookings.filter(booking_datetime__date=today).order_by('booking_datetime')
    upcoming_bookings = pending_bookings.filter(booking_datetime__gt=now).order_by('booking_datetime')
    overdue_bookings = pending_bookings.filter(booking_datetime__lt=now).order_by('booking_datetime')

    booking_count_today = today_bookings.count()
    booking_count_upcoming = upcoming_bookings.count()
    booking_count_overdue = overdue_bookings.count()
    booking_count_pending = pending_bookings.count()

    session_key = f"transport_dashboard_notice_{today.isoformat()}"
    if not request.session.get(session_key):
        if booking_count_overdue > 0:
            first_overdue = overdue_bookings.first()
            messages.warning(
                request,
                f"You have {booking_count_overdue} overdue booking(s). "
                f"Nearest overdue: {first_overdue.customer_name} ({first_overdue.customer_phone}) "
                f"scheduled for {timezone.localtime(first_overdue.booking_datetime).strftime('%d %b %Y %H:%M')} "
                f"from {first_overdue.pickup_area}."
            )
            request.session[session_key] = True
        elif booking_count_upcoming > 0:
            nearest = upcoming_bookings.first()
            messages.info(
                request,
                f"You have {booking_count_upcoming} upcoming booking(s). "
                f"Nearest: {nearest.customer_name} ({nearest.customer_phone}) "
                f"on {timezone.localtime(nearest.booking_datetime).strftime('%d %b %Y %H:%M')} "
                f"from {nearest.pickup_area}."
            )
            request.session[session_key] = True

    context = {
        'trips_total': trips_total,
        'trips_count': trips_count,
        'expenses_total': expenses_total,
        'net_total': net_total,
        'profit_value': profit_value,
        'loss_value': loss_value,
        'recent_trips': recent_trips,
        'recent_expenses': recent_expenses,
        'trips_chart_labels': labels,
        'trips_chart_values': trips_values,
        'expenses_chart_values': expenses_values,

        'selected_date': filter_date.isoformat(),
        'vehicles': vehicles,
        'selected_vehicle_id': selected_vehicle.pk if selected_vehicle else "",

        'today_bookings': today_bookings[:6],
        'upcoming_bookings': upcoming_bookings[:6],
        'overdue_bookings': overdue_bookings[:6],
        'booking_count_today': booking_count_today,
        'booking_count_upcoming': booking_count_upcoming,
        'booking_count_overdue': booking_count_overdue,
        'booking_count_pending': booking_count_pending,
    }

    return render(request, 'transport/index.html', context)


# -------------------------
# Vehicles
# -------------------------
@login_required
@role_required(['admin', 'cashier', 'transport'])
def vehicle_list(request):
    vehicles = Vehicle.objects.all()
    return render(request, 'transport/vehicle_list.html', {'vehicles': vehicles})


@login_required
@role_required(['admin'])
def vehicle_create(request):
    if request.method == 'POST':
        form = VehicleForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Vehicle created.")
            return redirect('transport:vehicle_list')
    else:
        form = VehicleForm()
    return render(request, 'transport/vehicle_form.html', {'form': form})


# -------------------------
# Drivers
# -------------------------
@login_required
@role_required(['admin', 'transport', 'cashier'])
def driver_list(request):
    drivers = Driver.objects.select_related('user').all()
    return render(request, 'transport/driver_list.html', {'drivers': drivers})


@login_required
@role_required(['admin'])
def driver_create(request):
    if request.method == 'POST':
        form = DriverForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Driver created successfully.")
            return redirect('transport:driver_list')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = DriverForm()

    return render(request, 'transport/driver_form.html', {'form': form})


# -------------------------
# Trips
# -------------------------
from django.core.paginator import Paginator

@login_required
@role_required(['admin', 'cashier', 'transport'])
def trip_list(request):
    profile = request.user.profile
    vehicles = Vehicle.objects.filter(is_active=True).order_by('plate_number')

    if profile.role in ['admin', 'cashier']:
        trips = Trip.objects.select_related('vehicle', 'driver', 'branch').all()
    else:
        trips = Trip.objects.select_related('vehicle', 'driver', 'branch').filter(branch=profile.branch)

    selected_date_str = request.GET.get("date", "").strip()
    filter_date = _parse_date(selected_date_str)

    selected_vehicle_id = _parse_int(request.GET.get("vehicle"))
    if selected_vehicle_id:
        trips = trips.filter(vehicle_id=selected_vehicle_id)

    if filter_date:
        trips = trips.filter(trip_date__date=filter_date)

    trips = trips.order_by('-trip_date', '-id')

    # -------------------------
    # Pagination
    # -------------------------
    paginator = Paginator(trips, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, 'transport/trip_list.html', {
        'trips': page_obj.object_list,
        'page_obj': page_obj,
        'selected_date': filter_date.isoformat() if filter_date else "",
        'vehicles': vehicles,
        'selected_vehicle_id': selected_vehicle_id or "",
        'total_trips_count': paginator.count,
    })

@login_required
@role_required(['admin', 'transport'])
def trip_create(request):
    if request.method == 'POST':
        form = TripForm(request.POST, user=request.user)
        if form.is_valid():
            trip = form.save(commit=False)
            trip.created_by = request.user
            if request.user.profile.role == 'transport' and request.user.profile.branch:
                trip.branch = request.user.profile.branch
            trip.save()
            messages.success(request, "Trip recorded.")
            return redirect('transport:trip_list')
    else:
        form = TripForm(user=request.user)
    return render(request, 'transport/trip_form.html', {'form': form})


# -------------------------
# Expenses
# -------------------------
@login_required
@role_required(['admin', 'transport', 'cashier'])
def expense_list(request):
    role = _role(request)
    branch = _branch(request)
    vehicles = Vehicle.objects.filter(is_active=True).order_by('plate_number')

    qs = TransportExpense.objects.select_related(
        'vehicle', 'branch', 'recorded_by'
    ).order_by('-expense_date', '-id')

    if role == "transport":
        if branch:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.none()

    selected_date_str = request.GET.get("date", "").strip()
    filter_date = _parse_date(selected_date_str)
    selected_vehicle_id = _parse_int(request.GET.get("vehicle"))
    expense_scope = request.GET.get("scope", "").strip()  # all / vehicle / general

    if filter_date:
        qs = qs.filter(expense_date__date=filter_date)

    if selected_vehicle_id:
        qs = qs.filter(vehicle_id=selected_vehicle_id)

    if expense_scope == "vehicle":
        qs = qs.filter(vehicle__isnull=False)
    elif expense_scope == "general":
        qs = qs.filter(vehicle__isnull=True)

    return render(request, 'transport/expense_list.html', {
        'expenses': qs,
        'role': role,
        'selected_date': filter_date.isoformat() if filter_date else "",
        'vehicles': vehicles,
        'selected_vehicle_id': selected_vehicle_id or "",
        'selected_scope': expense_scope,
        'is_cashier': role == "cashier",
        'is_admin': role == "admin",
        'is_transport': role == "transport",
    })

@login_required
@role_required(['admin', 'transport'])
def expense_create(request):
    if request.method == 'POST':
        form = TransportExpenseForm(request.POST, user=request.user)
        if form.is_valid():
            exp = form.save(commit=False)
            exp.recorded_by = request.user
            if request.user.profile.role == 'transport' and request.user.profile.branch:
                exp.branch = request.user.profile.branch
            exp.save()
            messages.success(request, "Expense recorded.")
            return redirect('transport:expense_list')
    else:
        form = TransportExpenseForm(user=request.user)
    return render(request, 'transport/expense_form.html', {'form': form})


# -------------------------
# Bookings
# -------------------------
@login_required
@role_required(['admin', 'cashier', 'transport'])
def booking_list(request):
    role = _role(request)
    branch = _branch(request)

    qs = Booking.objects.select_related('branch', 'created_by').all()

    if role == "transport":
        if branch:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.none()

    selected_date_str = request.GET.get("date", "").strip()
    filter_date = _parse_date(selected_date_str)
    status = request.GET.get("status", "").strip()

    if filter_date:
        qs = qs.filter(booking_datetime__date=filter_date)

    if status:
        qs = qs.filter(status=status)

    qs = qs.order_by('booking_datetime', '-id')

    return render(request, 'transport/booking_list.html', {
        'bookings': qs,
        'selected_date': filter_date.isoformat() if filter_date else "",
        'selected_status': status,
    })


@login_required
@role_required(['admin', 'cashier', 'transport'])
def booking_create(request):
    if request.method == 'POST':
        form = BookingForm(request.POST, user=request.user)
        if form.is_valid():
            booking = form.save(commit=False)
            booking.created_by = request.user

            if request.user.profile.role == 'transport' and request.user.profile.branch:
                booking.branch = request.user.profile.branch

            if not booking.status:
                booking.status = "pending"

            booking.save()
            messages.success(request, "Booking added successfully.")
            return redirect('transport:booking_list')
        else:
            messages.error(request, "Please correct the booking form errors.")
    else:
        form = BookingForm(user=request.user)

    return render(request, 'transport/booking_form.html', {'form': form})


@login_required
@role_required(['admin', 'cashier', 'transport'])
def booking_edit(request, pk):
    role = _role(request)
    branch = _branch(request)

    booking = get_object_or_404(Booking, pk=pk)

    if role == "transport":
        if not branch or booking.branch != branch:
            messages.error(request, "You do not have permission to edit this booking.")
            return redirect('transport:booking_list')

    if request.method == 'POST':
        form = BookingForm(request.POST, instance=booking, user=request.user)
        if form.is_valid():
            edited_booking = form.save(commit=False)

            if request.user.profile.role == 'transport' and request.user.profile.branch:
                edited_booking.branch = request.user.profile.branch

            edited_booking.save()
            messages.success(request, "Booking updated successfully.")
            return redirect('transport:booking_list')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = BookingForm(instance=booking, user=request.user)

    return render(request, 'transport/booking_form.html', {
        'form': form,
        'is_edit': True,
        'booking': booking,
    })


@login_required
@role_required(['admin', 'cashier', 'transport'])
def booking_delete(request, pk):
    role = _role(request)
    branch = _branch(request)

    booking = get_object_or_404(Booking, pk=pk)

    if role == "transport":
        if not branch or booking.branch != branch:
            messages.error(request, "You do not have permission to delete this booking.")
            return redirect('transport:booking_list')

    if request.method == 'POST':
        booking.delete()
        messages.success(request, "Booking deleted successfully.")
        return redirect('transport:booking_list')

    return render(request, 'transport/booking_confirm_delete.html', {
        'booking': booking
    })