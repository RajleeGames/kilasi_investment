from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.timezone import localdate

from users.utils import role_required
from inventory.models import Branch

from .forms import CustomerOrderForm, CustomerOrderPaymentForm, CustomerOrderStatusForm
from .models import CustomerOrder, CustomerOrderPayment


def _get_user_profile(request):
    return getattr(request.user, "profile", None)


def _role_lower(request):
    profile = _get_user_profile(request)
    return (getattr(profile, "role", "") or "").lower()


def _is_admin(request):
    return request.user.is_superuser or _role_lower(request) == "admin"


def _is_cashier(request):
    return _role_lower(request) == "cashier"


def _is_seller(request):
    return _role_lower(request) == "seller"


def _get_user_branch(request):
    profile = _get_user_profile(request)
    if not profile:
        return None

    branch = getattr(profile, "branch", None)
    if branch:
        return branch

    branch_id = getattr(profile, "branch_id", None)
    if branch_id:
        return Branch.objects.filter(pk=branch_id).first()

    return None


def _branches_for_dropdown():
    return (
        Branch.objects.filter(is_active=True)
        .exclude(name__icontains="transport")
        .exclude(name__icontains="hq")
        .order_by("name")
    )


def _selected_branch(request):
    if _is_seller(request):
        return _get_user_branch(request)

    branch_id = (request.GET.get("branch") or "").strip()
    if not branch_id:
        return None

    return _branches_for_dropdown().filter(pk=branch_id).first()


def _order_scope(request):
    qs = CustomerOrder.objects.select_related("product", "branch", "created_by")

    if _is_seller(request):
        branch = _get_user_branch(request)
        if branch:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.filter(created_by=request.user)

    return qs


def _can_access_order(request, order):
    if _is_admin(request) or _is_cashier(request):
        return True

    if _is_seller(request):
        branch = _get_user_branch(request)
        if branch and order.branch_id:
            return order.branch_id == branch.id
        return order.created_by_id == request.user.id

    return False


@login_required
@role_required(["admin", "cashier", "seller"])
def order_dashboard(request):
    qs = _order_scope(request)

    selected_branch = _selected_branch(request)
    if selected_branch and (_is_admin(request) or _is_cashier(request)):
        qs = qs.filter(branch=selected_branch)

    today = localdate()

    total_orders = qs.count()
    pending_orders = qs.filter(order_status="pending").count()
    in_progress_orders = qs.filter(order_status="in_progress").count()
    completed_orders = qs.filter(order_status="completed").count()

    unpaid_orders = qs.filter(payment_status="unpaid").count()
    partial_orders = qs.filter(payment_status="partial").count()
    paid_orders = qs.filter(payment_status="paid").count()

    total_order_value = qs.aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")

    paid_total = Decimal("0.00")
    balance_total = Decimal("0.00")

    for order in qs:
        paid_total += order.paid_amount
        balance_total += order.balance

    recent_orders = qs.order_by("-created_at")[:10]

    return render(request, "orders/order_dashboard.html", {
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "in_progress_orders": in_progress_orders,
        "completed_orders": completed_orders,
        "unpaid_orders": unpaid_orders,
        "partial_orders": partial_orders,
        "paid_orders": paid_orders,
        "total_order_value": total_order_value,
        "paid_total": paid_total,
        "balance_total": balance_total,
        "recent_orders": recent_orders,
        "branches": _branches_for_dropdown() if (_is_admin(request) or _is_cashier(request)) else [],
        "selected_branch": str(selected_branch.id) if selected_branch else "",
        "today": today,
        "is_admin": _is_admin(request),
        "is_cashier": _is_cashier(request),
        "is_seller": _is_seller(request),
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def order_list(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    payment_status = (request.GET.get("payment_status") or "").strip()

    qs = _order_scope(request)

    selected_branch = _selected_branch(request)
    if selected_branch and (_is_admin(request) or _is_cashier(request)):
        qs = qs.filter(branch=selected_branch)

    if q:
        qs = qs.filter(
            Q(customer_name__icontains=q)
            | Q(customer_phone__icontains=q)
            | Q(custom_product_name__icontains=q)
            | Q(description__icontains=q)
            | Q(product__name__icontains=q)
        )

    if status:
        qs = qs.filter(order_status=status)

    if payment_status:
        qs = qs.filter(payment_status=payment_status)

    page_obj = Paginator(qs, 25).get_page(request.GET.get("page"))

    return render(request, "orders/order_list.html", {
        "orders": page_obj.object_list,
        "page_obj": page_obj,
        "q": q,
        "status": status,
        "payment_status": payment_status,
        "branches": _branches_for_dropdown() if (_is_admin(request) or _is_cashier(request)) else [],
        "selected_branch": str(selected_branch.id) if selected_branch else "",
        "order_status_choices": CustomerOrder.ORDER_STATUS_CHOICES,
        "payment_status_choices": CustomerOrder.PAYMENT_STATUS_CHOICES,
        "is_admin": _is_admin(request),
        "is_cashier": _is_cashier(request),
        "is_seller": _is_seller(request),
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def order_create(request):
    if request.method == "POST":
        form = CustomerOrderForm(request.POST)

        if form.is_valid():
            try:
                with transaction.atomic():
                    order = form.save(commit=False)
                    order.created_by = request.user

                    if _is_seller(request):
                        order.branch = _get_user_branch(request)
                    else:
                        branch_id = (request.POST.get("branch") or "").strip()
                        if branch_id:
                            order.branch = _branches_for_dropdown().filter(pk=branch_id).first()

                    order.save()

                    advance_amount = form.cleaned_data.get("advance_amount") or Decimal("0.00")
                    payment_method = form.cleaned_data.get("payment_method") or "cash"

                    if advance_amount > 0:
                        CustomerOrderPayment.objects.create(
                            order=order,
                            amount=advance_amount,
                            method=payment_method,
                            note="Advance payment",
                            received_by=request.user,
                        )
                    else:
                        order.refresh_payment_status()

                messages.success(request, "Customer order created successfully.")
                return redirect("orders:order_detail", pk=order.pk)

            except Exception as e:
                messages.error(request, f"Failed to create order: {e}")
    else:
        form = CustomerOrderForm()

    return render(request, "orders/order_form.html", {
        "form": form,
        "title": "Create Customer Order",
        "branches": _branches_for_dropdown() if (_is_admin(request) or _is_cashier(request)) else [],
        "is_admin": _is_admin(request),
        "is_cashier": _is_cashier(request),
        "is_seller": _is_seller(request),
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def order_detail(request, pk):
    order = get_object_or_404(
        CustomerOrder.objects.select_related("product", "branch", "created_by"),
        pk=pk
    )

    if not _can_access_order(request, order):
        return HttpResponseForbidden("You cannot access this order.")

    payments = order.payments.select_related("received_by").all()

    payment_form = CustomerOrderPaymentForm()
    status_form = CustomerOrderStatusForm(instance=order)

    return render(request, "orders/order_detail.html", {
        "order": order,
        "payments": payments,
        "payment_form": payment_form,
        "status_form": status_form,
        "is_admin": _is_admin(request),
        "is_cashier": _is_cashier(request),
        "is_seller": _is_seller(request),
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def order_add_payment(request, pk):
    order = get_object_or_404(CustomerOrder, pk=pk)

    if not _can_access_order(request, order):
        return HttpResponseForbidden("You cannot add payment to this order.")

    if request.method != "POST":
        return redirect("orders:order_detail", pk=order.pk)

    form = CustomerOrderPaymentForm(request.POST)

    if form.is_valid():
        payment = form.save(commit=False)
        payment.order = order
        payment.received_by = request.user

        if payment.amount <= 0:
            messages.error(request, "Enter a valid payment amount.")
            return redirect("orders:order_detail", pk=order.pk)

        if payment.amount > order.balance:
            payment.amount = order.balance
            messages.warning(request, f"Payment reduced to balance: {order.balance:,.0f}")

        if payment.amount <= 0:
            messages.error(request, "This order is already fully paid.")
            return redirect("orders:order_detail", pk=order.pk)

        payment.save()
        messages.success(request, "Payment recorded successfully.")

    else:
        messages.error(request, "Please check payment form.")

    return redirect("orders:order_detail", pk=order.pk)


@login_required
@role_required(["admin", "cashier", "seller"])
def order_update_status(request, pk):
    order = get_object_or_404(CustomerOrder, pk=pk)

    if not _can_access_order(request, order):
        return HttpResponseForbidden("You cannot update this order.")

    if request.method != "POST":
        return redirect("orders:order_detail", pk=order.pk)

    form = CustomerOrderStatusForm(request.POST, instance=order)

    if form.is_valid():
        obj = form.save(commit=False)

        if obj.order_status == "completed" and not obj.completed_at:
            from django.utils import timezone
            obj.completed_at = timezone.now()

        obj.save()
        messages.success(request, "Order status updated.")
    else:
        messages.error(request, "Please check status form.")

    return redirect("orders:order_detail", pk=order.pk)