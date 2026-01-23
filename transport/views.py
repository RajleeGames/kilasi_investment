# transport/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count
from django.db.models.functions import TruncDate
from django.utils import timezone
from datetime import timedelta

from .models import Vehicle, Driver, Trip, TransportExpense
from .forms import VehicleForm, DriverForm, TripForm, TransportExpenseForm

# Try to import role_required helper; if not present create simple fallback.
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
                if profile and profile.role in allowed_roles:
                    return view_func(request, *args, **kwargs)
                return HttpResponseForbidden("You don't have permission to view this page.")
            return _wrapped
        return decorator


# ---------- Dashboard / Index ----------
@login_required
@role_required(['admin', 'cashier', 'transport'])
def index(request):
    """
    Transport dashboard:
     - totals: trips_total, trips_count, expenses_total
     - net_total (income - expenses)
     - profit_value / loss_value (for pie charts)
     - recent_trips, recent_expenses
     - chart arrays (last 30 days): trips_chart_labels, trips_chart_values, expenses_chart_values
    """

    profile = getattr(request.user, "profile", None)
    role = getattr(profile, "role", None)

    # Scope queryset depending on role
    if role in ['admin', 'cashier']:
        trips_qs = Trip.objects.select_related('vehicle', 'driver', 'branch').all()
        expenses_qs = TransportExpense.objects.select_related('vehicle', 'branch', 'recorded_by').all()
    else:  # transport role: limit to user's branch (if any)
        branch = getattr(profile, 'branch', None)
        trips_qs = Trip.objects.select_related('vehicle', 'driver', 'branch').filter(branch=branch)
        expenses_qs = TransportExpense.objects.select_related('vehicle', 'branch', 'recorded_by').filter(branch=branch)

    # Totals and counts (use aggregate; fallback to 0)
    trips_total = trips_qs.aggregate(total=Sum('amount_charged')).get('total') or 0
    trips_count = trips_qs.count()

    expenses_total = expenses_qs.aggregate(total=Sum('amount')).get('total') or 0

    # Net (profit = income - expenses) - keep Decimal if possible
    try:
        net_total = (trips_total or 0) - (expenses_total or 0)
    except Exception:
        net_total = float(trips_total or 0) - float(expenses_total or 0)

    # Profit / Loss numbers for pie chart (always float)
    try:
        net_float = float(net_total or 0)
    except Exception:
        net_float = 0.0

    profit_value = net_float if net_float > 0 else 0.0
    loss_value = abs(net_float) if net_float < 0 else 0.0

    # Recent items for lists
    recent_trips = trips_qs.order_by('-trip_date')[:6]
    recent_expenses = expenses_qs.order_by('-expense_date')[:6]

    # Chart: last N days (labels + sums)
    days = 30
    today = timezone.localtime(timezone.now()).date()
    start_date = today - timedelta(days=days - 1)  # include today

    # Aggregate trips per day
    trips_by_day_qs = (
        trips_qs
        .filter(trip_date__date__gte=start_date)
        .annotate(day=TruncDate('trip_date'))
        .values('day')
        .annotate(total=Sum('amount_charged'))
        .order_by('day')
    )

    # Aggregate expenses per day
    expenses_by_day_qs = (
        expenses_qs
        .filter(expense_date__date__gte=start_date)
        .annotate(day=TruncDate('expense_date'))
        .values('day')
        .annotate(total=Sum('amount'))
        .order_by('day')
    )

    # Convert queryset results into dict for quick lookup
    trips_map = {entry['day'].isoformat(): float(entry['total'] or 0) for entry in trips_by_day_qs}
    expenses_map = {entry['day'].isoformat(): float(entry['total'] or 0) for entry in expenses_by_day_qs}

    # Build labels & values arrays for chart, in chronological order
    labels = []
    trips_values = []
    expenses_values = []
    for i in range(days):
        d = start_date + timedelta(days=i)
        labels.append(d.strftime('%d %b'))  # e.g., "22 Jan"
        key = d.isoformat()
        trips_values.append(trips_map.get(key, 0.0))
        expenses_values.append(expenses_map.get(key, 0.0))

    # NOTE: Keep arrays as Python lists, because template uses json_script safely
    context = {
        'trips_total': trips_total,
        'trips_count': trips_count,
        'expenses_total': expenses_total,
        'net_total': net_total,

        # profit/loss for pie chart
        'profit_value': profit_value,
        'loss_value': loss_value,

        'recent_trips': recent_trips,
        'recent_expenses': recent_expenses,

        # chart payloads
        'trips_chart_labels': labels,
        'trips_chart_values': trips_values,
        'expenses_chart_values': expenses_values,
    }

    return render(request, 'transport/index.html', context)


# ---------- Vehicle CRUD ----------
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


# ---------- Driver CRUD ----------
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


# ---------- Trips ----------
@login_required
@role_required(['admin', 'cashier', 'transport'])
def trip_list(request):
    profile = request.user.profile
    if profile.role in ['admin', 'cashier']:
        trips = Trip.objects.select_related('vehicle', 'driver', 'branch').all()
    else:
        trips = Trip.objects.select_related('vehicle', 'driver', 'branch').filter(branch=profile.branch)
    return render(request, 'transport/trip_list.html', {'trips': trips})


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

# transport/views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from users.utils import role_required
from .models import TransportExpense

def _role(request):
    profile = getattr(request.user, "profile", None)
    return (getattr(profile, "role", "") or "").lower()

def _branch(request):
    profile = getattr(request.user, "profile", None)
    return getattr(profile, "branch", None)

@login_required
@role_required(['admin', 'transport', 'cashier'])
def expense_list(request):
    role = _role(request)
    branch = _branch(request)

    qs = TransportExpense.objects.select_related(
        'vehicle', 'branch', 'recorded_by'
    ).order_by('-expense_date', '-id')

    # ✅ Your requirement:
    # - Cashier: view ALL expenses
    # - Admin: view ALL expenses
    # - Transport: view their branch only
    if role == "transport":
        if branch:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.none()  # transport must have branch

    # admin/cashier => no filter (see all)

    return render(request, 'transport/expense_list.html', {
        'expenses': qs,
        'role': role,
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
