from decimal import Decimal
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.timezone import localdate

from sales.models import Customer
from users.utils import role_required
from inventory.models import Branch
from .forms import TopupCreateForm, UsedTopupSellForm, UsedTopupStockUpdateForm
from .models import (
    TopupTransaction,
    UsedTopupStock,
    UsedTopupSale,
    create_topup_transaction,
)


def _user_branch(request):
    profile = getattr(request.user, "profile", None)
    return getattr(profile, "branch", None)


def _is_admin(request):
    profile = getattr(request.user, "profile", None)
    role = (getattr(profile, "role", "") or "").lower()
    return bool(request.user.is_superuser or role == "admin")


def _is_cashier(request):
    profile = getattr(request.user, "profile", None)
    return (getattr(profile, "role", "") or "").lower() == "cashier"


def _is_seller(request):
    profile = getattr(request.user, "profile", None)
    return (getattr(profile, "role", "") or "").lower() == "seller"


def _topup_scope(request):
    qs = TopupTransaction.objects.select_related("branch", "customer", "new_product", "created_by")
    if _is_seller(request):
        b = _user_branch(request)
        if b:
            qs = qs.filter(branch=b)
    return qs


def _used_scope(request):
    qs = UsedTopupStock.objects.select_related("branch", "transaction")
    if _is_seller(request):
        b = _user_branch(request)
        if b:
            qs = qs.filter(branch=b)
    return qs


def _save_customer_if_needed(customer_obj, customer_name, customer_phone):
    if customer_obj:
        return customer_obj

    name = (customer_name or "").strip()
    phone = (customer_phone or "").strip()

    if not name and not phone:
        return None

    if phone:
        existing = Customer.objects.filter(phone=phone).first()
        if existing:
            if not existing.name and name:
                existing.name = name
                existing.save(update_fields=["name"])
            return existing

    if name:
        existing = Customer.objects.filter(name__iexact=name).first()
        if existing:
            if phone and not existing.phone:
                existing.phone = phone
                existing.save(update_fields=["phone"])
            return existing

    return Customer.objects.create(name=name or "Customer", phone=phone or "")


@login_required
@role_required(["admin", "cashier", "seller"])
def topup_dashboard(request):
    qs = _topup_scope(request)
    used_qs = _used_scope(request)
    sold_qs = UsedTopupSale.objects.select_related("used_stock", "branch")

    if _is_seller(request):
        b = _user_branch(request)
        if b:
            sold_qs = sold_qs.filter(branch=b)

    branch_id = (request.GET.get("branch") or "").strip()
    if branch_id and not _is_seller(request):
        qs = qs.filter(branch_id=branch_id)
        used_qs = used_qs.filter(branch_id=branch_id)
        sold_qs = sold_qs.filter(branch_id=branch_id)

    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
        used_qs = used_qs.filter(created_at__date__gte=date_from)
        sold_qs = sold_qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
        used_qs = used_qs.filter(created_at__date__lte=date_to)
        sold_qs = sold_qs.filter(created_at__date__lte=date_to)

    total_topups = qs.filter(status="completed").count()
    addon_received = qs.filter(status="completed").aggregate(t=Sum("addon_amount"))["t"] or Decimal("0.00")
    used_value_received = qs.filter(status="completed").aggregate(t=Sum("used_item_buying_price"))["t"] or Decimal("0.00")

    available_used_items = used_qs.filter(status="available").count()
    sold_used_items = used_qs.filter(status="sold").count()

    used_sales_total = sold_qs.aggregate(t=Sum("sold_price"))["t"] or Decimal("0.00")
    used_sales_profit = Decimal("0.00")
    for sale in sold_qs:
        try:
            used_sales_profit += sale.profit
        except Exception:
            pass

    branches = Branch.objects.filter(is_active=True).order_by("name")

    return render(request, "topup/dashboard.html", {
        "topup_title": "Top-up Dashboard",
        "topups": qs.order_by("-created_at")[:10],
        "total_topups": total_topups,
        "addon_received": addon_received,
        "used_value_received": used_value_received,
        "available_used_items": available_used_items,
        "sold_used_items": sold_used_items,
        "used_sales_total": used_sales_total,
        "used_sales_profit": used_sales_profit,
        "branches": branches,
        "selected_branch": branch_id,
        "date_from": date_from,
        "date_to": date_to,
        "is_seller": _is_seller(request),
        "user_branch": _user_branch(request),
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def topup_create(request):
    initial = {}
    if _is_seller(request):
        branch = _user_branch(request)
        if branch:
            initial["branch"] = branch

    if request.method == "POST":
        form = TopupCreateForm(request.POST)
        if _is_seller(request):
            branch = _user_branch(request)
            if branch:
                form.fields["branch"].queryset = Branch.objects.filter(pk=branch.pk)
        if form.is_valid():
            customer = _save_customer_if_needed(
                form.cleaned_data["customer"],
                form.cleaned_data["customer_name"],
                form.cleaned_data["customer_phone"],
            )

            branch = form.cleaned_data["branch"]
            if _is_seller(request):
                user_branch = _user_branch(request)
                if user_branch and branch != user_branch:
                    messages.error(request, "You can only create top-up transactions in your own branch.")
                    return render(request, "topup/create.html", {"form": form})

            try:
                tx = create_topup_transaction(
                    branch=branch,
                    customer=customer,
                    new_product=form.cleaned_data["new_product"],
                    new_product_qty=form.cleaned_data["new_product_qty"],
                    used_item_name=form.cleaned_data["used_item_name"],
                    used_item_category=form.cleaned_data["used_item_category"],
                    used_item_condition=form.cleaned_data["used_item_condition"],
                    used_item_buying_price=form.cleaned_data["used_item_buying_price"],
                    addon_amount=form.cleaned_data["addon_amount"],
                    created_by=request.user,
                    note=form.cleaned_data["note"],
                )
                messages.success(request, f"Top-up transaction #{tx.pk} created successfully.")
                return redirect("topup:transaction_detail", pk=tx.pk)
            except Exception as e:
                messages.error(request, str(e))
    else:
        form = TopupCreateForm(initial=initial)
        if _is_seller(request):
            branch = _user_branch(request)
            if branch:
                form.fields["branch"].queryset = Branch.objects.filter(pk=branch.pk)

    return render(request, "topup/create.html", {
        "form": form,
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def topup_history(request):
    qs = _topup_scope(request)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(used_item_name__icontains=q) |
            Q(new_product__name__icontains=q) |
            Q(customer__name__icontains=q)
        )

    return render(request, "topup/history.html", {
        "transactions": qs.order_by("-created_at"),
        "query": q,
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def topup_transaction_detail(request, pk):
    tx = get_object_or_404(_topup_scope(request), pk=pk)
    return render(request, "topup/transaction_detail.html", {
        "tx": tx,
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def used_stock_list(request):
    qs = _used_scope(request)

    status = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()

    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(
            Q(item_name__icontains=q) |
            Q(item_category__icontains=q) |
            Q(condition__icontains=q)
        )

    return render(request, "topup/used_stock_list.html", {
        "items": qs.order_by("-created_at"),
        "selected_status": status,
        "query": q,
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def used_stock_detail(request, pk):
    item = get_object_or_404(_used_scope(request), pk=pk)

    if request.method == "POST" and item.status == "available":
        form = UsedTopupStockUpdateForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, "Used item updated.")
            return redirect("topup:used_stock_detail", pk=item.pk)
    else:
        form = UsedTopupStockUpdateForm(instance=item)

    return render(request, "topup/used_stock_detail.html", {
        "item": item,
        "form": form,
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def used_stock_sell(request, pk):
    item = get_object_or_404(_used_scope(request).filter(status="available"), pk=pk)

    if request.method == "POST":
        form = UsedTopupSellForm(request.POST)
        if form.is_valid():
            customer = _save_customer_if_needed(
                form.cleaned_data["customer"],
                form.cleaned_data["customer_name"],
                form.cleaned_data["customer_phone"],
            )

            sold_price = form.cleaned_data["sold_price"]
            note = form.cleaned_data["note"]

            item.mark_sold(sold_price=sold_price, note=note)

            UsedTopupSale.objects.create(
                used_stock=item,
                customer=customer,
                branch=item.branch,
                sold_price=sold_price,
                note=note,
                sold_by=request.user,
            )

            messages.success(request, "Used top-up item sold successfully.")
            return redirect("topup:used_stock_detail", pk=item.pk)
    else:
        form = UsedTopupSellForm()

    return render(request, "topup/sell_used.html", {
        "item": item,
        "form": form,
    })