# sales/views.py
from decimal import Decimal
import json
from datetime import date, datetime, timedelta
from django.db.models import F, ExpressionWrapper, DecimalField
from django.shortcuts import render
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q


from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    Sum, Q, OuterRef, Subquery, IntegerField, Value,
    F, ExpressionWrapper, DecimalField
)
from django.db.models.functions import TruncMonth, TruncYear, TruncDate, TruncWeek, Coalesce
from django.http import JsonResponse, HttpResponseForbidden, HttpResponse
from django.shortcuts import redirect, get_object_or_404
from django.template.loader import select_template
from django.utils.timezone import localdate
from django.utils.dateformat import format as datefmt

from users.utils import role_required
from users.models import Role

from inventory.models import Product, Category, StockEntry, Supplier, Branch, ProductStock
from .models import Invoice, InvoiceItem, Customer, Payment, Expense


# ───────────────────────────────────────────────────────────────
# TEMPLATE RENDER HELPER (supports both "sales/x.html" and "x.html")
# ───────────────────────────────────────────────────────────────
def _render(request, template_candidates, context):
    """
    Example:
      _render(request, ["sales/invoice_detail.html", "invoice_detail.html"], {...})
    """
    tpl = select_template(template_candidates)
    return HttpResponse(tpl.render(context, request))


# ───────────────────────────────────────────────────────────────
# ROLE / PROFILE / BRANCH HELPERS
# ───────────────────────────────────────────────────────────────
def _get_user_profile(request):
    return getattr(request.user, "profile", None)


def _role_lower(request):
    profile = _get_user_profile(request)
    return (getattr(profile, "role", "") or "").lower()


def _role_const(name, fallback):
    return str(getattr(Role, name, fallback)).lower()


def _is_admin(request):
    profile = _get_user_profile(request)
    return bool(request.user.is_superuser or (profile and str(getattr(profile, "role", "")).lower() == _role_const("ADMIN", "admin")))


def _is_cashier(request):
    return _role_lower(request) == _role_const("CASHIER", "cashier")


def _is_seller(request):
    return _role_lower(request) == _role_const("SELLER", "seller")


def _get_user_branch(request):
    """
    Expect: request.user.profile.branch (FK to inventory.Branch)
    If not present -> None
    """
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


# ───────────────────────────────────────────────────────────────
# SAFE UTILITIES
# ───────────────────────────────────────────────────────────────
def _safe_int(v, default=None):
    try:
        v = (v or "").strip() if isinstance(v, str) else v
        return int(v) if v not in (None, "") else default
    except Exception:
        return default


def _money(val) -> Decimal:
    try:
        return Decimal(val or 0)
    except Exception:
        return Decimal("0.00")


def _product_selling_price(prod) -> Decimal:
    # supports new field selling_price, fallback old price
    if hasattr(prod, "selling_price") and prod.selling_price is not None:
        return Decimal(prod.selling_price)
    return Decimal(getattr(prod, "price", 0) or 0)


def _product_buying_price(prod) -> Decimal:
    # supports new field buying_price, fallback 0
    if hasattr(prod, "buying_price") and prod.buying_price is not None:
        return Decimal(prod.buying_price)
    return Decimal("0.00")


# ───────────────────────────────────────────────────────────────
# BRANCH STOCK HELPERS
# ───────────────────────────────────────────────────────────────
def _branch_products_qs(branch, cat=None, q=None):
    """
    Returns products visible to seller for a branch with branch_qty annotation.
    If branch is None -> fallback to Product.stock.
    """
    if not branch:
        qs = Product.objects.select_related("category")
        if cat:
            qs = qs.filter(category_id=cat)
        if q:
            qs = qs.filter(name__icontains=q)
        qs = qs.filter(stock__gt=0)  # legacy mode
        return qs, None

    ps = ProductStock.objects.filter(
        product=OuterRef("pk"),
        branch=branch
    ).values("quantity")[:1]

    qs = (
        Product.objects
        .select_related("category")
        .annotate(branch_qty=Coalesce(Subquery(ps, output_field=IntegerField()), Value(0)))
        .filter(branch_qty__gt=0)
    )

    if cat:
        qs = qs.filter(category_id=cat)
    if q:
        qs = qs.filter(name__icontains=q)

    return qs, "branch_qty"


def _get_branch_qty(product_id, branch):
    if not branch:
        p = Product.objects.filter(pk=product_id).only("stock").first()
        return int(getattr(p, "stock", 0) or 0) if p else 0

    return int(
        ProductStock.objects.filter(product_id=product_id, branch=branch)
        .values_list("quantity", flat=True)
        .first() or 0
    )


def _adjust_branch_stock_locked(product, branch, delta):
    """
    Atomic safe change:
      delta negative for sale, positive for restock
    NOTE: In your system you prefer using StockEntry to update stock (recommended).
    """
    ps = ProductStock.objects.select_for_update().filter(product=product, branch=branch).first()
    if not ps:
        ps = ProductStock(product=product, branch=branch, quantity=0)

    new_qty = int(ps.quantity) + int(delta)
    if new_qty < 0:
        raise ValueError(f"Insufficient stock for '{product.name}' in {branch.name}. Available: {ps.quantity}")

    ps.quantity = new_qty
    ps.save()
    return new_qty


# ───────────────────────────────────────────────────────────────
# BRANCH DROPDOWN + SCOPE HELPERS
# ───────────────────────────────────────────────────────────────
def _branches_for_sales_dropdown():
    """
    Exclude Transport/HQ branches from sales dashboards.
    Adjust keywords to match your exact names.
    """
    return (
        Branch.objects.filter(is_active=True)
        .exclude(name__icontains="transport")
        .exclude(name__icontains="HAMA NA ")
        .exclude(name__icontains="hq")
        .order_by("name")
    )


def _can_filter_branch(request):
    # Admin + Cashier can use branch dropdown
    return _is_admin(request) or _is_cashier(request)


def _selected_branch(request):
    """
    Seller: always locked to their own branch
    Admin/Cashier: can pick ?branch=<id> (or None = all branches)
    """
    if _is_seller(request):
        return _get_user_branch(request)

    if not _can_filter_branch(request):
        return None

    branch_id = (request.GET.get("branch") or "").strip()
    if not branch_id:
        return None

    # Only allow branches from sales dropdown (not transport/hq)
    return _branches_for_sales_dropdown().filter(pk=branch_id).first()


def _invoice_scope_qs(request):
    """
    Base invoice scope:
    - seller => own branch only (if branch exists), else own invoices
    - admin/cashier => all
    """
    qs = Invoice.objects.select_related("customer", "seller")
    if hasattr(Invoice, "branch_id"):
        qs = qs.select_related("branch")

    if _is_seller(request):
        b = _get_user_branch(request)
        if b and hasattr(Invoice, "branch_id"):
            qs = qs.filter(branch=b)
        else:
            qs = qs.filter(seller=request.user)

    return qs


def _expense_scope_qs(request):
    qs = Expense.objects.select_related("created_by")
    if hasattr(Expense, "branch_id"):
        qs = qs.select_related("branch")

    if _is_seller(request):
        b = _get_user_branch(request)
        if b and hasattr(Expense, "branch_id"):
            qs = qs.filter(branch=b)

    return qs


def _payment_scope_qs(request):
    qs = Payment.objects.select_related("invoice", "created_by")
    if hasattr(Invoice, "branch_id"):
        qs = qs.select_related("invoice__branch")

    if _is_seller(request):
        b = _get_user_branch(request)
        if b and hasattr(Invoice, "branch_id"):
            qs = qs.filter(invoice__branch=b)

    return qs


def _must_own_invoice_if_seller(request, inv):
    """
    Seller must only access own invoices / branch invoices.
    """
    if _is_admin(request) or _is_cashier(request):
        return True

    if _is_seller(request):
        branch = _get_user_branch(request)
        if branch and hasattr(inv, "branch_id"):
            return inv.branch_id == branch.id
        return inv.seller_id == request.user.id

    return False


# ───────────────────────────────────────────────────────────────
# BALANCE HELPERS (works for method OR property/int field)
# ───────────────────────────────────────────────────────────────
def _invoice_balance(inv) -> int:
    """
    Supports BOTH styles:
      - inv.balance()  (method)
      - inv.balance    (property / int field)
    Always returns int >= 0
    """
    b = getattr(inv, "balance", 0)
    try:
        value = b() if callable(b) else b
        value = int(value or 0)
    except Exception:
        value = 0
    return max(0, value)


# ───────────────────────────────────────────────────────────────
# LANDING REDIRECT "/"
# ───────────────────────────────────────────────────────────────
@login_required
def dashboard_redirect(request):
    role = _role_lower(request)

    if request.user.is_superuser or role == _role_const("ADMIN", "admin"):
        return redirect("sales:admin_dashboard")

    if role == _role_const("CASHIER", "cashier"):
        return redirect("sales:admin_dashboard")

    if role == _role_const("TRANSPORT", "transport"):
        return redirect("transport:index")

    if role == _role_const("SELLER", "seller"):
        return redirect("sales:seller_dashboard")

    return redirect("login")


# ───────────────────────────────────────────────────────────────
# SELLER POS (BRANCH-AWARE)
# ───────────────────────────────────────────────────────────────
@login_required
@role_required(["seller", "admin"])
def seller_dashboard(request):
    branch = _get_user_branch(request)

    raw_cart = request.session.get("cart", {}) or {}
    cart = {}

    # normalize cart (store as {"pid": {"qty": int, "discount": int}})
    for pid, entry in raw_cart.items():
        if isinstance(entry, dict):
            qty = max(1, int(entry.get("qty", 1) or 1))
            disc = max(0, int(entry.get("discount", 0) or 0))
        else:
            qty, disc = max(1, int(entry or 1)), 0
        cart[str(pid)] = {"qty": qty, "discount": disc}

    request.session["cart"] = cart
    request.session.modified = True

    # build cart items (✅ include unit_price so template never uses product.price)
    cart_items = []
    for pid, data in cart.items():
        prod = get_object_or_404(Product, pk=int(pid))
        qty = max(1, int(data.get("qty", 1) or 1))
        disc = max(0, int(data.get("discount", 0) or 0))  # per-unit discount

        unit_price = _product_selling_price(prod)  # Decimal
        discount_d = Decimal(str(disc))
        effective_price = unit_price - discount_d
        if effective_price < Decimal("0.00"):
            effective_price = Decimal("0.00")

        total_price = (effective_price * Decimal(qty)).quantize(Decimal("0.01"))

        # branch-aware visible stock
        if _is_seller(request) and branch:
            visible_stock = int(_get_branch_qty(prod.pk, branch) or 0)
        else:
            visible_stock = int(getattr(prod, "stock", 0) or 0)

        cart_items.append({
            "product": prod,
            "quantity": qty,
            "discount": disc,
            "unit_price": unit_price,            # ✅ USED IN TEMPLATE
            "effective_price": effective_price,  # optional (use in receipt/invoice if needed)
            "total_price": total_price,
            "visible_stock": visible_stock,
        })

    # filters (optional)
    cat = (request.GET.get("category") or "").strip() or None
    q = (request.GET.get("q") or "").strip() or None

    # branch products query
    qs, stock_field = _branch_products_qs(
        branch if _is_seller(request) else None,
        cat=cat,
        q=q
    )

    products_data = []
    for p in qs:
        stock_val = getattr(p, stock_field) if stock_field else getattr(p, "stock", 0)
        stock_val = int(stock_val or 0)

        price = _product_selling_price(p)  # Decimal

        # ✅ include "price" because your barcode JS expects pd.price
        products_data.append({
            "id": p.id,
            "name": p.name,
            "price": float(price),  # JS use
            "display": f"{p.name} — Tsh {int(price):,} (Stock: {stock_val})",
            "category": p.category_id,
            "stock": stock_val,
            "reorder_level": int(getattr(p, "reorder_level", 0) or 0),
        })

    return _render(
        request,
        ["sales/seller_dashboard.html", "seller_dashboard.html"],
        {
            "categories": Category.objects.values_list("id", "name"),
            "products_json": json.dumps(products_data),
            "cart_items": cart_items,
            "selected_cat": cat or "",
            "search_q": q or "",
            "branch": branch,
        },
    )


@login_required
@role_required(["seller", "admin"])
def add_to_cart(request, product_id):
    branch = _get_user_branch(request)
    qty = max(1, int(request.POST.get("quantity", 1)))

    if _is_seller(request) and branch:
        available = _get_branch_qty(product_id, branch)
        if available <= 0:
            messages.error(request, "No stock in your branch for this product.")
            return redirect("sales:seller_dashboard")
        if qty > available:
            qty = available
            messages.warning(request, f"Stock in your branch is only {available}. Quantity reduced.")

    cart = request.session.setdefault("cart", {})
    entry = cart.get(str(product_id), {"qty": 0, "discount": 0})
    entry["qty"] += qty
    cart[str(product_id)] = entry
    request.session.modified = True
    return redirect("sales:seller_dashboard")


@login_required
@role_required(["seller", "admin"])
def remove_from_cart(request, product_id):
    cart = request.session.get("cart", {})
    cart.pop(str(product_id), None)
    request.session.modified = True
    return redirect("sales:seller_dashboard")


@login_required
@role_required(["seller", "admin"])
def update_cart_qty(request, product_id):
    if request.method == "POST":
        branch = _get_user_branch(request)
        cart = request.session.setdefault("cart", {})

        if str(product_id) in cart:
            qty = max(1, int(request.POST.get("quantity", 1)))
            disc = max(0, int(request.POST.get("discount", 0)))

            if _is_seller(request) and branch:
                available = _get_branch_qty(product_id, branch)
                if available <= 0:
                    messages.error(request, "No stock in your branch for this product.")
                    cart.pop(str(product_id), None)
                    request.session.modified = True
                    return redirect("sales:seller_dashboard")
                if qty > available:
                    qty = available
                    messages.warning(request, f"Stock in your branch is only {available}. Quantity reduced.")

            cart[str(product_id)] = {"qty": qty, "discount": disc}
            request.session.modified = True

    return redirect("sales:seller_dashboard")


@login_required
@role_required(["seller", "admin"])
def add_selected_product_to_cart(request):
    if request.method == "POST":
        branch = _get_user_branch(request)

        pid = request.POST.get("product_id")
        if not pid:
            return redirect("sales:seller_dashboard")

        qty = max(1, int(request.POST.get("quantity", 1)))

        if _is_seller(request) and branch:
            available = _get_branch_qty(pid, branch)
            if available <= 0:
                messages.error(request, "No stock in your branch for this product.")
                return redirect("sales:seller_dashboard")
            if qty > available:
                qty = available
                messages.warning(request, f"Stock in your branch is only {available}. Quantity reduced.")

        cart = request.session.setdefault("cart", {})
        entry = cart.get(str(pid), {"qty": 0, "discount": 0})
        entry["qty"] += qty
        cart[str(pid)] = entry
        request.session.modified = True

    return redirect("sales:seller_dashboard")


@login_required
@role_required(["seller", "admin"])
def ajax_products(request):
    branch = _get_user_branch(request) if _is_seller(request) else None
    cat = (request.GET.get("category") or "").strip() or None
    q = (request.GET.get("q") or "").strip() or None

    qs, stock_field = _branch_products_qs(branch, cat=cat, q=q)

    results = []
    for p in qs[:50]:
        stock_val = getattr(p, stock_field) if stock_field else getattr(p, "stock", 0)
        stock_val = int(stock_val or 0)
        results.append({
            "id": p.id,
            "label": f"{p.name} — Tsh{_product_selling_price(p):.2f} (Stock: {stock_val})"
        })

    return JsonResponse(results, safe=False)


@login_required
@role_required(["seller", "admin"])
def lookup_product(request):
    branch = _get_user_branch(request) if _is_seller(request) else None
    barcode = (request.GET.get("barcode") or "").strip()

    try:
        prod = Product.objects.get(barcode=barcode)
    except Product.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Product not found"})

    stock_val = _get_branch_qty(prod.pk, branch) if branch else int(getattr(prod, "stock", 0) or 0)

    return JsonResponse({
        "status": "success",
        "product": {
            "id": prod.pk,
            "name": prod.name,
            "price": str(_product_selling_price(prod)),
            "stock": stock_val,
        }
    })


@login_required
@role_required(["seller", "admin"])
def submit_sale(request):
    """
    Seller handles everything:
      - cash/bank sale => paid=True immediately
      - debt => paid=False and appears in debts list
      - stock updates ONLY via StockEntry
      - branch-aware: seller sells only from their branch
    """
    if request.method != "POST":
        return redirect("sales:seller_dashboard")

    cart = request.session.get("cart", {})
    if not cart:
        messages.warning(request, "Cart is empty.")
        return redirect("sales:seller_dashboard")

    branch = _get_user_branch(request) if _is_seller(request) else None

    payment = (request.POST.get("payment_method") or "debt").strip().lower()
    customer_name = (request.POST.get("customer_name") or "").strip()
    customer_phone = (request.POST.get("customer_phone") or "").strip()

    if payment not in ("cash", "debt", "ebt", "bank"):
        payment = "debt"

    if payment == "debt":
        customer, _ = Customer.objects.get_or_create(
            name=customer_name or "Unknown",
            phone=customer_phone or "N/A",
        )
        paid_flag = False
    else:
        customer = None
        paid_flag = True

    try:
        with transaction.atomic():
            # 1) Validate stock FIRST
            if branch:
                for pid, data in cart.items():
                    qty = max(1, int(data.get("qty", 1)))
                    prod = get_object_or_404(Product, pk=int(pid))

                    ps = (
                        ProductStock.objects
                        .select_for_update()
                        .filter(product=prod, branch=branch)
                        .first()
                    )
                    available = int(ps.quantity) if ps else 0
                    if qty > available:
                        raise ValueError(
                            f"Insufficient stock for '{prod.name}' in your branch. "
                            f"Available: {available}, Requested: {qty}"
                        )

            # 2) Create invoice
            inv_kwargs = {
                "customer": customer,
                "seller": request.user,
                "status": payment,
                "paid": paid_flag,
            }
            if branch and hasattr(Invoice, "branch_id"):
                inv_kwargs["branch"] = branch

            inv = Invoice.objects.create(**inv_kwargs)

            # 3) Create items + stock entries
            for pid, data in cart.items():
                prod = get_object_or_404(Product, pk=int(pid))
                qty = max(1, int(data.get("qty", 1)))
                disc = max(0, int(data.get("discount", 0)))

                selling = _product_selling_price(prod)
                buying = _product_buying_price(prod)

                # InvoiceItem snapshot (new fields preferred, fallback old unit_price)
                item_kwargs = {
                    "invoice": inv,
                    "product": prod,
                    "quantity": qty,
                    "discount": disc,
                }

                if hasattr(InvoiceItem, "selling_price"):
                    item_kwargs["selling_price"] = selling
                    if hasattr(InvoiceItem, "buying_cost"):
                        item_kwargs["buying_cost"] = buying
                else:
                    # legacy
                    item_kwargs["unit_price"] = selling

                InvoiceItem.objects.create(**item_kwargs)

                # Stock change ONLY via StockEntry
                StockEntry.objects.create(
                    product=prod,
                    branch=branch if branch else None,
                    change=-qty,
                    note=f"Sale #{inv.pk} ({payment})",
                )

            # 4) Totals
            if hasattr(inv, "calculate_totals") and callable(getattr(inv, "calculate_totals")):
                inv.calculate_totals()

    except (ValueError, ValidationError) as e:
        messages.error(request, str(e))
        return redirect("sales:seller_dashboard")

    request.session["cart"] = {}
    request.session.modified = True
    return redirect("sales:invoice_detail", pk=inv.pk)


# ───────────────────────────────────────────────────────────────

from decimal import Decimal
from django.shortcuts import get_object_or_404, render
from django.contrib.auth.decorators import login_required
from users.utils import role_required

from .models import Invoice, InvoiceItem  # adjust names if different


def _d(value, default="0.00"):
    """Safe Decimal"""
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


@login_required
@role_required(["seller", "admin"])
def invoice_detail(request, pk=None, invoice_id=None):
    """
    Works with URL kwargs pk or invoice_id.
    Fixes: invoice_detail() got unexpected keyword argument 'pk'
    Fixes: cannot assign unit_price property
    """
    inv_id = pk if pk is not None else invoice_id
    invoice = get_object_or_404(Invoice, pk=inv_id)

    # items queryset
    if hasattr(invoice, "items"):
        items_qs = invoice.items.select_related("product").all()
    else:
        items_qs = InvoiceItem.objects.filter(invoice=invoice).select_related("product")

    invoice_items = []
    for it in items_qs:
        qty = int(getattr(it, "quantity", 0) or 0)
        disc = _d(getattr(it, "discount", 0) or 0)  # per unit discount

        # unit price (DO NOT set it.unit_price!)
        up = getattr(it, "unit_price", None)
        if up is None:
            prod = getattr(it, "product", None)
            up = getattr(prod, "selling_price", None)
            if up is None:
                up = getattr(prod, "price", 0)  # may not exist in your Product
        unit_price = _d(up or 0)

        effective_price = unit_price - disc
        if effective_price < 0:
            effective_price = Decimal("0.00")

        line_total = effective_price * _d(qty)

        invoice_items.append({
            "product": it.product,
            "quantity": qty,
            "discount": disc,
            "unit_price": unit_price,
            "effective_price": effective_price,
            "line_total": line_total,
        })

    # balance safe (field/property/method)
    balance_val = None
    bal_attr = getattr(invoice, "balance", None)
    if callable(bal_attr):
        try:
            balance_val = bal_attr()
        except Exception:
            balance_val = None
    else:
        balance_val = bal_attr

    context = {
        "invoice": invoice,
        "invoice_items": invoice_items,
        "balance": _d(balance_val or 0),
    }
    return render(request, "sales/invoice_detail.html", context)

@login_required
@role_required(["seller", "admin", "cashier"])
def invoice_receipt(request, pk):
    inv = get_object_or_404(
        Invoice.objects.select_related("customer", "seller"),
        pk=pk
    )

    if not _must_own_invoice_if_seller(request, inv):
        return HttpResponseForbidden("You cannot access this invoice.")

    autoprint = (request.GET.get("autoprint") or "") == "1"

    company = {
        "name": "KILASI INVESTMENT",
        "tin": "TIN: 155-581-522",
        "phone": "Tel: +255 XXX XXX XXX",
        "location": "Moshi, Tanzania",
        "footer": "THANK YOU FOR BUYING WITH US",
    }

    # ✅ safe status label (works even without choices)
    status_map = {"cash": "CASH", "bank": "BANK", "ebt": "EBT", "debt": "DEBT"}

    status_label = status_map.get((inv.status or "").lower(), (inv.status or "").upper() or "—")

    # ✅ safe customer label
    customer_label = "Walk-in"
    if getattr(inv, "customer", None):
        customer_label = getattr(inv.customer, "name", None) or str(inv.customer)

    # ✅ safe debt numbers (works whether you use property or method)
    def _safe_balance(i):
        b = getattr(i, "balance", 0)
        try:
            v = b() if callable(b) else b
            return int(v or 0)
        except Exception:
            return 0

    payments_total = 0
    try:
        payments_total = int(inv.payments.aggregate(t=Sum("amount"))["t"] or 0)
    except Exception:
        payments_total = 0

    return _render(request, ["sales/thermal_receipt.html", "thermal_receipt.html"], {
        "invoice": inv,
        "autoprint": autoprint,
        "company": company,
        "status_label": status_label,
        "customer_label": customer_label,
        "payments_total": payments_total,
        "balance_value": _safe_balance(inv),
    })

# ───────────────────────────────────────────────────────────────
# SELLER DEBTS + PAYMENTS
# ───────────────────────────────────────────────────────────────
@login_required
@role_required(["seller", "admin"])
def outstanding_debts(request):
    q = (request.GET.get("q") or "").strip()

    qs = _invoice_scope_qs(request).filter(status="debt", paid=False)
    if q:
        qs = qs.filter(customer__name__icontains=q)

    debts = list(qs.order_by("-created_at"))

    total_outstanding = sum(_invoice_balance(inv) for inv in debts)
    top_debtor = max(debts, key=_invoice_balance) if debts else None
    least_debtor = min(debts, key=_invoice_balance) if debts else None

    return _render(request, ["sales/outstanding_debts.html", "outstanding_debts.html"], {
        "debts": debts,
        "query": q,
        "total_outstanding": total_outstanding,
        "top_debtor": top_debtor,
        "least_debtor": least_debtor,
        "_invoice_balance": _invoice_balance,
    })


@login_required
@role_required(["seller", "admin"])
def record_payment(request, invoice_id):
    inv = get_object_or_404(Invoice, pk=invoice_id, status="debt")

    if not _must_own_invoice_if_seller(request, inv):
        return HttpResponseForbidden("You cannot record payment for this invoice.")

    if request.method == "POST":
        raw = (request.POST.get("amount") or "0").strip()
        try:
            amount = int(Decimal(raw))
        except Exception:
            amount = 0

        if amount <= 0:
            messages.error(request, "Enter a valid amount.")
            return redirect("sales:record_payment", invoice_id=invoice_id)

        bal = _invoice_balance(inv)
        if amount > bal:
            amount = bal
            messages.warning(request, f"Amount reduced to balance: {bal} TSH")

        Payment.objects.create(
            invoice=inv,
            amount=amount,
            created_by=request.user
        )

        messages.success(request, "Payment recorded.")
        return redirect("sales:invoice_detail", pk=inv.pk)

    return _render(request, ["sales/record_payment.html", "record_payment.html"], {
        "invoice": inv,
        "balance": _invoice_balance(inv),
        "history": inv.payments.order_by("-timestamp"),
    })


def _product_cost_expr():
    """
    Picks the first existing buying-cost field from Product.
    Supported names (you can add yours here):
      buying_price, cost_price, purchase_price, buy_price, cost, unit_cost
    """
    candidates = ["buying_price", "cost_price", "purchase_price", "buy_price", "cost", "unit_cost"]
    product_field_names = {f.name for f in Product._meta.get_fields()}

    found = [c for c in candidates if c in product_field_names]
    if not found:
        # no cost field found
        return None, None

    # build Coalesce(product__field1, product__field2, ..., 0)
    out = DecimalField(max_digits=18, decimal_places=2)
    expr = F(f"product__{found[0]}")
    for name in found[1:]:
        expr = Coalesce(expr, F(f"product__{name}"))
    expr = Coalesce(expr, Value(0, output_field=out))
    return expr, found[0]


@login_required
@role_required(["admin", "cashier"])
def admin_dashboard(request):
    today = localdate()

    # ✅ branches dropdown (exclude transport/HQ)
    branches_qs = _branches_for_sales_dropdown()

    branch_id = (request.GET.get("branch") or "").strip()
    selected_branch = branches_qs.filter(pk=branch_id).first() if branch_id else None

    inv_qs = Invoice.objects.all()
    pay_qs = Payment.objects.select_related("invoice")
    exp_qs = Expense.objects.all()

    # apply branch filter
    if selected_branch:
        if hasattr(Invoice, "branch_id"):
            inv_qs = inv_qs.filter(branch=selected_branch)
            pay_qs = pay_qs.filter(invoice__branch=selected_branch)
        if hasattr(Expense, "branch_id"):
            exp_qs = exp_qs.filter(branch=selected_branch)

    # filters month/year (optional)
    month = _safe_int(request.GET.get("month"))
    year = _safe_int(request.GET.get("year"))

    # -----------------------------------------
    # CASHFLOW (today)
    # -----------------------------------------
    total_cash_today = (
        inv_qs.filter(created_at__date=today, status="cash", paid=True)
        .aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
    )
    total_bank_today = (
        inv_qs.filter(created_at__date=today, status="bank", paid=True)
        .aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
    )
    total_ebt_today = (
        inv_qs.filter(created_at__date=today, status="ebt", paid=True)
        .aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
    )

    total_today_cashflow = total_cash_today + total_bank_today + total_ebt_today

    # -----------------------------------------
    # CASHFLOW (overall / selected month-year)
    # -----------------------------------------
    if month and year:
        cash = (
            inv_qs.filter(status="cash", paid=True, created_at__month=month, created_at__year=year)
            .aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
        )
        bank = (
            inv_qs.filter(status="bank", paid=True, created_at__month=month, created_at__year=year)
            .aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
        )
        ebt = (
            inv_qs.filter(status="ebt", paid=True, created_at__month=month, created_at__year=year)
            .aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
        )

        # payments received for debt (by payment timestamp)
        debt_payments = (
            pay_qs.filter(timestamp__month=month, timestamp__year=year)
            .aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
        )

        expenses_total = (
            exp_qs.filter(date__month=month, date__year=year)
            .aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
        )
    else:
        cash = inv_qs.filter(status="cash", paid=True).aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
        bank = inv_qs.filter(status="bank", paid=True).aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
        ebt  = inv_qs.filter(status="ebt",  paid=True).aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")

        debt_payments = pay_qs.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
        expenses_total = exp_qs.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")

    # ✅ include EBT here
    overall_sales_cashflow = cash + bank + ebt + debt_payments

    expenses_today = exp_qs.filter(date=today).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    net_today_cashflow = total_today_cashflow - expenses_today
    net_overall_cashflow = overall_sales_cashflow - expenses_total

    # -----------------------------------------
    # ✅ PROFIT (Revenue - COGS - Expenses)
    # -----------------------------------------
    revenue_field = "subtotal" if hasattr(Invoice, "subtotal") else "total_amount"

    inv_today_all = inv_qs.filter(created_at__date=today)
    if month and year:
        inv_period_all = inv_qs.filter(created_at__month=month, created_at__year=year)
        exp_period = exp_qs.filter(date__month=month, date__year=year)
    else:
        inv_period_all = inv_qs
        exp_period = exp_qs

    revenue_today = inv_today_all.aggregate(t=Sum(revenue_field))["t"] or Decimal("0.00")
    revenue_period = inv_period_all.aggregate(t=Sum(revenue_field))["t"] or Decimal("0.00")

    cost_expr, cost_field_used = _product_cost_expr()
    profit_enabled = cost_expr is not None

    cogs_today = Decimal("0.00")
    cogs_period = Decimal("0.00")

    if profit_enabled:
        out = DecimalField(max_digits=18, decimal_places=2)
        cogs_line = ExpressionWrapper(F("quantity") * cost_expr, output_field=out)

        items_today = InvoiceItem.objects.filter(invoice__in=inv_today_all)
        items_period = InvoiceItem.objects.filter(invoice__in=inv_period_all)

        cogs_today = items_today.aggregate(t=Sum(cogs_line))["t"] or Decimal("0.00")
        cogs_period = items_period.aggregate(t=Sum(cogs_line))["t"] or Decimal("0.00")

    gross_profit_today = revenue_today - cogs_today
    gross_profit_period = revenue_period - cogs_period

    expenses_today_for_profit = exp_qs.filter(date=today).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    expenses_period_for_profit = exp_period.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")

    net_profit_today = gross_profit_today - expenses_today_for_profit
    net_profit_period = gross_profit_period - expenses_period_for_profit

    # -----------------------------------------
    # counts
    # -----------------------------------------
    num_debts = inv_qs.filter(status="debt", paid=False).count()
    num_cash_sales = inv_qs.filter(status="cash", paid=True).count()
    num_bank_sales = inv_qs.filter(status="bank", paid=True).count()
    num_ebt_sales  = inv_qs.filter(status="ebt",  paid=True).count()
    total_transactions = inv_qs.count()

    # recent products
    all_products = Product.objects.order_by("-id")
    paginator = Paginator(all_products, 6)
    recent_products = paginator.get_page(request.GET.get("recent_page", 1))

    months = list(range(1, 13))
    current_year = date.today().year
    years = list(range(current_year - 5, current_year + 1))

    return render(request, "sales/admin_dashboard.html", {
        "is_admin": _is_admin(request),
        "is_cashier": _is_cashier(request),

        "branches": branches_qs,
        "selected_branch": str(selected_branch.id) if selected_branch else "",
        "selected_branch_obj": selected_branch,

        # cashflow
        "total_cash_today": total_cash_today,
        "total_bank_today": total_bank_today,
        "total_ebt_today": total_ebt_today,
        "total_today": total_today_cashflow,

        "selected_month": month or "",
        "selected_year": year or "",

        "total_debt_payments": debt_payments,
        "overall_sales": overall_sales_cashflow,

        "expenses_today": expenses_today,
        "expenses_total": expenses_total,

        "net_today_cashflow": net_today_cashflow,
        "net_overall_cashflow": net_overall_cashflow,

        # profit
        "profit_enabled": profit_enabled,
        "profit_cost_field": cost_field_used or "",
        "revenue_today": revenue_today,
        "cogs_today": cogs_today,
        "gross_profit_today": gross_profit_today,
        "net_profit_today": net_profit_today,
        "revenue_period": revenue_period,
        "cogs_period": cogs_period,
        "gross_profit_period": gross_profit_period,
        "net_profit_period": net_profit_period,

        # counts
        "num_debts": num_debts,
        "num_cash_sales": num_cash_sales,
        "num_bank_sales": num_bank_sales,
        "num_ebt_sales": num_ebt_sales,
        "total_transactions": total_transactions,

        "recent_products": recent_products,
        "months": months,
        "years": years,
        "today_str": today.isoformat(),
    })

# ───────────────────────────────────────────────────────────────
# REPORTS (admin/cashier filter branch, seller locked to branch)
# ───────────────────────────────────────────────────────────────
@login_required
@role_required(["admin", "cashier", "seller"])
def sales_history(request):
    selected = _selected_branch(request)

    qs = _invoice_scope_qs(request)

    if selected and hasattr(Invoice, "branch_id") and _can_filter_branch(request):
        qs = qs.filter(branch=selected)

    paginator = Paginator(qs.order_by("-created_at"), 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    return _render(request, ["sales/sales_history.html", "sales_history.html"], {
        "invoices": page_obj.object_list,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "paginator": paginator,

        "branches": _branches_for_sales_dropdown() if _can_filter_branch(request) else [],
        "selected_branch": str(selected.id) if selected else "",
        "selected_branch_obj": selected,

        "is_admin": _is_admin(request),
        "is_cashier": _is_cashier(request),
        "is_seller": _is_seller(request),
    })


from decimal import Decimal
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum
from django.utils.timezone import localdate

from .models import Invoice, Expense, Payment  # make sure these imports exist


def _dec(x):
    """
    Safe Decimal conversion. Keeps cents (does NOT cast to int).
    """
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal("0.00")


# ───────────────────────────────────────────────────────────────
# EXPENSES (ADD + HISTORY)  ✅ REQUIRED for urls.py
# ───────────────────────────────────────────────────────────────
@login_required
@role_required(["admin", "cashier", "seller"])
def add_expense(request):
    """
    URL: /expenses/add/
    Fixes: AttributeError: sales.views has no attribute add_expense
    """
    selected = _selected_branch(request)

    # Branch list for dropdown: only admin/cashier can pick, seller hidden
    branches = _branches_for_sales_dropdown() if _can_filter_branch(request) else []

    if request.method == "POST":
        # Inputs
        date_val = (request.POST.get("date") or "").strip()
        category = (request.POST.get("category") or "").strip()
        description = (request.POST.get("description") or "").strip()
        amount_raw = (request.POST.get("amount") or "0").strip()

        try:
            amount = Decimal(amount_raw)
        except Exception:
            amount = Decimal("0")

        if amount <= 0:
            messages.error(request, "Enter a valid amount.")
            return redirect("sales:kilasi_add_expense")

        # Determine branch
        branch_obj = None

        # seller locked to own branch
        if _is_seller(request):
            branch_obj = _get_user_branch(request)

        # admin/cashier can choose in form, else uses selected filter branch
        else:
            branch_id = (request.POST.get("branch") or "").strip()
            if branch_id:
                branch_obj = _branches_for_sales_dropdown().filter(pk=branch_id).first()
            elif selected:
                branch_obj = selected

        # Create expense
        exp_kwargs = {
            "date": date_val or localdate(),
            "category": category or "General",
            "description": description,
            "amount": amount,
            "created_by": request.user,
        }
        if hasattr(Expense, "branch_id"):
            exp_kwargs["branch"] = branch_obj

        Expense.objects.create(**exp_kwargs)

        messages.success(request, "Expense added successfully.")
        return redirect("sales:expense_history")

    return _render(request, ["sales/kilasi_add_expense.html", "kilasi_add_expense.html"], {
        "branches": branches,
        "selected_branch": str(selected.id) if selected else "",
        "selected_branch_obj": selected,
        "today_str": localdate().isoformat(),
        "is_admin": _is_admin(request),
        "is_cashier": _is_cashier(request),
        "is_seller": _is_seller(request),
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def expense_history(request):
    """
    URL: /expenses/history/
    """
    selected = _selected_branch(request)
    branches = _branches_for_sales_dropdown() if _can_filter_branch(request) else []

    qs = _expense_scope_qs(request).order_by("-date", "-id")

    # admin/cashier branch filter
    if selected and _can_filter_branch(request) and hasattr(Expense, "branch_id"):
        qs = qs.filter(branch=selected)

    page_obj = Paginator(qs, 30).get_page(request.GET.get("page", 1))

    total_amount = qs.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")

    return _render(request, ["sales/expense_history.html", "expense_history.html"], {
        "expenses": page_obj.object_list,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "paginator": page_obj.paginator,

        "total_amount": total_amount,

        "branches": branches,
        "selected_branch": str(selected.id) if selected else "",
        "selected_branch_obj": selected,

        "is_admin": _is_admin(request),
        "is_cashier": _is_cashier(request),
        "is_seller": _is_seller(request),
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def sales_report(request):
    today = localdate()
    selected_date = (request.GET.get("date") or today.isoformat()).strip()

    try:
        report_date = datetime.strptime(selected_date, "%Y-%m-%d").date()
    except ValueError:
        report_date = today
        selected_date = today.isoformat()

    prev_date = (report_date - timedelta(days=1)).isoformat()
    next_date = (report_date + timedelta(days=1)).isoformat()

    selected = _selected_branch(request)

    inv_qs = _invoice_scope_qs(request)
    exp_qs = _expense_scope_qs(request)

    if selected and hasattr(Invoice, "branch_id") and _can_filter_branch(request):
        inv_qs = inv_qs.filter(branch=selected)
    if selected and hasattr(Expense, "branch_id") and _can_filter_branch(request):
        exp_qs = exp_qs.filter(branch=selected)

    # invoices and expenses of that date
    sales_qs = inv_qs.filter(created_at__date=report_date).order_by("-created_at")
    expenses_qs = exp_qs.filter(date=report_date).order_by("-date", "-id")

    # ✅ Income received that day:
    # cash + bank + ebt invoices that are paid
    income_sales = _dec(
        sales_qs.filter(status__in=["cash", "bank", "ebt"], paid=True)
        .aggregate(t=Sum("total_amount"))["t"] or 0
    )

    # debt payments received that day
    income_debt_qs = Payment.objects.filter(timestamp__date=report_date)
    if selected and hasattr(Invoice, "branch_id") and _can_filter_branch(request):
        income_debt_qs = income_debt_qs.filter(invoice__branch=selected)

    income_debt = _dec(income_debt_qs.aggregate(t=Sum("amount"))["t"] or 0)

    revenue_received = income_sales + income_debt

    # ✅ Total expenses that day
    total_expenses = _dec(expenses_qs.aggregate(total=Sum("amount"))["total"] or 0)

    # ✅ NET = Income - Expenses
    net_income = revenue_received - total_expenses

    # Pagination
    sales_page = Paginator(sales_qs, 20).get_page(request.GET.get("sales_page", 1))
    exp_page = Paginator(expenses_qs, 20).get_page(request.GET.get("exp_page", 1))

    # Keep old keys for templates
    total_sales = revenue_received
    net_sales = net_income

    return _render(
        request,
        ["sales/kilasi_sales_report.html", "kilasi_sales_report.html"],
        {
            "selected_date": selected_date,
            "prev_date": prev_date,
            "next_date": next_date,

            "total_sales": total_sales,           # Income received
            "total_expenses": total_expenses,     # Expenses
            "net_sales": net_sales,               # Net = income - expenses

            "revenue_received": revenue_received,
            "income_sales": income_sales,
            "income_debt": income_debt,

            "sales_page": sales_page,
            "exp_page": exp_page,

            "branches": _branches_for_sales_dropdown() if _can_filter_branch(request) else [],
            "selected_branch": str(selected.id) if selected else "",
            "selected_branch_obj": selected,
            "today_str": today.isoformat(),
        }
    )


@login_required
@role_required(["admin", "cashier", "seller"])
def enhanced_sales_report(request):
    today = localdate()
    selected = _selected_branch(request)

    date_filter = (request.GET.get("date") or "").strip()
    month_filter = _safe_int(request.GET.get("month"))
    year_filter = _safe_int(request.GET.get("year"))

    doughnut_month = _safe_int(request.GET.get("doughnut_month")) or today.month
    doughnut_year = _safe_int(request.GET.get("doughnut_year")) or today.year

    inv_qs = _invoice_scope_qs(request)
    exp_qs = _expense_scope_qs(request)
    pay_qs = _payment_scope_qs(request)

    if selected and hasattr(Invoice, "branch_id") and _can_filter_branch(request):
        inv_qs = inv_qs.filter(branch=selected)
        pay_qs = pay_qs.filter(invoice__branch=selected)

    if selected and hasattr(Expense, "branch_id") and _can_filter_branch(request):
        exp_qs = exp_qs.filter(branch=selected)

    report_date = None
    if date_filter:
        try:
            report_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
        except ValueError:
            report_date = None

    if report_date:
        start = end = report_date
    else:
        end = today
        start = today - timedelta(days=6)

    # Daily chart
    daily_raw = (
        inv_qs.filter(created_at__date__gte=start, created_at__date__lte=end)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Sum("total_amount"))
        .order_by("day")
    )
    daily_map = {r["day"]: float(r["total"] or 0) for r in daily_raw}
    days_count = (end - start).days + 1
    daily = [{"day": (start + timedelta(days=i)).isoformat(),
              "total": daily_map.get(start + timedelta(days=i), 0)} for i in range(days_count)]

    # Weekly chart
    weekly_raw = (
        inv_qs.annotate(week=TruncWeek("created_at"))
        .values("week")
        .annotate(total=Sum("total_amount"))
        .order_by("week")
    )
    weeks = [r["week"] for r in weekly_raw if r["week"]]
    last12w = weeks[-12:] if len(weeks) >= 12 else weeks
    weekly_map = {r["week"]: float(r["total"] or 0) for r in weekly_raw}
    weekly = [{"week": w.strftime("%Y-%m-%d"), "total": weekly_map.get(w, 0)} for w in last12w]

    # Monthly chart (last 12 months)
    monthly = []
    cursor = date(today.year, today.month, 1)
    months_list = []
    for _ in range(12):
        months_list.append(cursor)
        prev_month = cursor.month - 1
        prev_year = cursor.year
        if prev_month == 0:
            prev_month = 12
            prev_year -= 1
        cursor = date(prev_year, prev_month, 1)
    months_list = list(reversed(months_list))

    monthly_raw = (
        inv_qs.annotate(m=TruncMonth("created_at"))
        .values("m")
        .annotate(total=Sum("total_amount"))
        .order_by("m")
    )
    monthly_map = {r["m"]: float(r["total"] or 0) for r in monthly_raw if r["m"]}

    for m in months_list:
        if month_filter and m.month != month_filter:
            continue
        if year_filter and m.year != year_filter:
            continue
        monthly.append({"month": m.month, "year": m.year, "total": monthly_map.get(m, 0)})

    # Yearly chart (last 5 years)
    years_list = [today.year - 4, today.year - 3, today.year - 2, today.year - 1, today.year]
    yearly_raw = (
        inv_qs.annotate(y=TruncYear("created_at"))
        .values("y")
        .annotate(total=Sum("total_amount"))
        .order_by("y")
    )
    yearly_map = {r["y"].year: float(r["total"] or 0) for r in yearly_raw if r["y"]}
    yearly = [{"year": y, "total": yearly_map.get(y, 0)} for y in years_list if not year_filter or y == year_filter]

    # Doughnut: revenue received + expenses + COGS + profit/loss
    # ✅ include EBT here
    income_sales = inv_qs.filter(
        created_at__year=doughnut_year,
        created_at__month=doughnut_month,
        status__in=["cash", "bank", "ebt"],
        paid=True
    ).aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")

    income_debt = pay_qs.filter(
        timestamp__year=doughnut_year,
        timestamp__month=doughnut_month
    ).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")

    doughnut_sales_total = Decimal(income_sales) + Decimal(income_debt)

    doughnut_total_expenses = exp_qs.filter(
        date__year=doughnut_year,
        date__month=doughnut_month
    ).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")

    doughnut_inv_qs = inv_qs.filter(created_at__year=doughnut_year, created_at__month=doughnut_month)
    doughnut_items = InvoiceItem.objects.filter(invoice__in=doughnut_inv_qs)

    if hasattr(InvoiceItem, "line_cost_cache"):
        doughnut_cogs_total = doughnut_items.aggregate(t=Sum("line_cost_cache"))["t"] or Decimal("0.00")
    elif hasattr(InvoiceItem, "buying_cost"):
        doughnut_cogs_total = doughnut_items.aggregate(
            t=Sum(ExpressionWrapper(
                F("buying_cost") * F("quantity"),
                output_field=DecimalField(max_digits=18, decimal_places=2)
            ))
        )["t"] or Decimal("0.00")
    else:
        doughnut_cogs_total = Decimal("0.00")

    doughnut_net_profit = doughnut_sales_total - doughnut_total_expenses - doughnut_cogs_total
    profit_is_negative = doughnut_net_profit < 0
    doughnut_profit_for_chart = doughnut_net_profit if doughnut_net_profit > 0 else Decimal("0.00")

    month_choices = [
        (1, "January"), (2, "February"), (3, "March"), (4, "April"),
        (5, "May"), (6, "June"), (7, "July"), (8, "August"),
        (9, "September"), (10, "October"), (11, "November"), (12, "December"),
    ]
    year_choices = list(range(today.year - 7, today.year + 1))

    return _render(request, ["sales/enhanced_sales_report.html", "enhanced_sales_report.html"], {
        "branches": _branches_for_sales_dropdown() if _can_filter_branch(request) else [],
        "selected_branch": str(selected.id) if selected else "",
        "selected_branch_obj": selected,

        "daily_sales_json": json.dumps(daily),
        "weekly_sales_json": json.dumps(weekly),
        "monthly_sales_json": json.dumps(monthly),
        "yearly_sales_json": json.dumps(yearly),

        "date_filter": date_filter,
        "month_filter": month_filter or "",
        "year_filter": year_filter or "",

        "month_choices": month_choices,
        "year_choices": year_choices,

        "doughnut_month": doughnut_month,
        "doughnut_year": doughnut_year,
        "doughnut_sales_total": float(doughnut_sales_total),
        "doughnut_total_expenses": float(doughnut_total_expenses),
        "doughnut_cogs_total": float(doughnut_cogs_total),
        "doughnut_net_profit": float(doughnut_net_profit),
        "doughnut_profit_for_chart": float(doughnut_profit_for_chart),
        "profit_is_negative": profit_is_negative,
    })


# ───────────────────────────────────────────────────────────────
# EXPENSES
# ───────────────────────────────────────────────────────────────
@login_required
@role_required(["admin", "cashier", "seller"])
def expense_overview(request):
    today = localdate()

    branch_id = (request.GET.get("branch") or "").strip()
    selected_branch = None

    if _is_admin(request):
        if branch_id:
            selected_branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
    else:
        selected_branch = _get_user_branch(request)

    inv_qs = Invoice.objects.all()
    exp_qs = Expense.objects.all()

    if selected_branch and hasattr(Invoice, "branch_id"):
        inv_qs = inv_qs.filter(branch=selected_branch)
    if selected_branch and hasattr(Expense, "branch_id"):
        exp_qs = exp_qs.filter(branch=selected_branch)

    # ✅ include EBT here
    sales_today = (
        inv_qs.filter(created_at__date=today, status__in=["cash", "bank", "ebt"], paid=True)
        .aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
    )
    expenses_today = exp_qs.filter(date=today).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    net_profit = sales_today - expenses_today

    today_expenses_list = exp_qs.filter(date=today).order_by("-id")[:20]
    branches = Branch.objects.filter(is_active=True).order_by("name") if _is_admin(request) else None

    return _render(request, ["sales/expense_overview.html", "expense_overview.html"], {
        "sales_today": sales_today,
        "expenses_today": expenses_today,
        "net_profit": net_profit,
        "today_expenses_list": today_expenses_list,

        "branches": branches,
        "selected_branch": str(selected_branch.id) if selected_branch else "",
        "selected_branch_obj": selected_branch,
        "is_admin": _is_admin(request),
    })

# ───────────────────────────────────────────────────────────────
# PRODUCTS (seller sees ONLY branch stock)
# ───────────────────────────────────────────────────────────────
@login_required
@role_required(["admin", "cashier", "seller"])
def product_list(request):
    q = (request.GET.get("q") or "").strip()
    branch = _get_user_branch(request) if _is_seller(request) else None

    qs = Product.objects.select_related("category")

    use_branch_stock = bool(branch)
    if use_branch_stock:
        ps = ProductStock.objects.filter(product=OuterRef("pk"), branch=branch).values("quantity")[:1]
        qs = qs.annotate(branch_qty=Coalesce(Subquery(ps, output_field=IntegerField()), Value(0))).filter(branch_qty__gt=0)

    if q:
        qs = qs.filter(name__icontains=q)

    paginator = Paginator(qs.order_by("-id"), 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return _render(request, ["sales/product_list.html", "product_list.html"], {
        "products": page_obj.object_list,
        "is_paginated": page_obj.has_other_pages(),
        "page_obj": page_obj,
        "paginator": paginator,
        "query": q,
        "use_branch_stock": use_branch_stock,
        "branch": branch,
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def product_details_json(request, pk):
    p = get_object_or_404(Product.objects.select_related("category"), pk=pk)
    branch = _get_user_branch(request) if _is_seller(request) else None

    entries_qs = StockEntry.objects.filter(product=p)
    if branch:
        entries_qs = entries_qs.filter(branch=branch)

    entries = entries_qs.order_by("timestamp")
    history = [{
        "date": datefmt(e.timestamp, "Y-m-d H:i"),
        "change": e.change,
        "note": e.note,
        "branch": e.branch.name if e.branch else "",
    } for e in entries]

    current_stock = _get_branch_qty(p.pk, branch) if branch else int(getattr(p, "stock", 0) or 0)

    return JsonResponse({
        "name": p.name,
        "category": p.category.name if p.category else "",
        "selling_price": f"{_product_selling_price(p):.2f}",
        "buying_price": f"{_product_buying_price(p):.2f}",
        "current_stock": current_stock,
        "history": history,
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def product_detail(request, pk):
    product = get_object_or_404(Product.objects.select_related("category"), pk=pk)

    is_admin = _is_admin(request)
    branch = _get_user_branch(request) if _is_seller(request) else None

    current_stock = _get_branch_qty(product.pk, branch) if branch else int(getattr(product, "stock", 0) or 0)

    if request.method == "POST":
        if not is_admin:
            return HttpResponseForbidden("Only admin can restock.")

        add_qty = _safe_int(request.POST.get("add_qty"), 0) or 0
        note = (request.POST.get("note") or "").strip()
        branch_id = (request.POST.get("branch_id") or "").strip()

        restock_branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None
        if not restock_branch:
            restock_branch = Branch.objects.filter(is_active=True).first() or Branch.objects.first()

        if add_qty <= 0:
            messages.error(request, "Enter a valid quantity greater than 0.")
            return redirect("sales:product_detail", pk=pk)

        if not restock_branch:
            messages.error(request, "No branch found. Please create a branch first.")
            return redirect("sales:product_detail", pk=pk)

        try:
            with transaction.atomic():
                StockEntry.objects.create(
                    product=product,
                    change=add_qty,
                    branch=restock_branch,
                    note=note or "Restocked via UI",
                )
            messages.success(request, f"Added {add_qty} to '{product.name}' in {restock_branch.name}.")
        except (ValidationError, ValueError) as e:
            messages.error(request, str(e))

        return redirect("sales:product_detail", pk=pk)

    entries_qs = StockEntry.objects.filter(product=product)
    if branch:
        entries_qs = entries_qs.filter(branch=branch)
    entries = entries_qs.order_by("-timestamp")

    branch_stocks = []
    if is_admin:
        branch_stocks = ProductStock.objects.filter(product=product).select_related("branch").order_by("branch__name")

    current_stock = _get_branch_qty(product.pk, branch) if branch else int(getattr(product, "stock", 0) or 0)

    return _render(request, ["sales/product_detail.html", "product_detail.html"], {
        "product": product,
        "entries": entries,
        "is_admin": is_admin,
        "branch": branch,
        "use_branch_stock": bool(branch),
        "current_stock": current_stock,
        "branches": Branch.objects.filter(is_active=True).order_by("name") if is_admin else [],
        "branch_stocks": branch_stocks,
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def top_selling_products(request):
    q = (request.GET.get("q") or "").strip()

    role = _role_lower(request)
    branch = _get_user_branch(request) if role == _role_const("SELLER", "seller") else None

    qs = InvoiceItem.objects.select_related("product", "invoice")

    if branch and hasattr(Invoice, "branch_id"):
        qs = qs.filter(invoice__branch=branch)
    if branch and not hasattr(Invoice, "branch_id"):
        qs = qs.filter(invoice__seller=request.user)

    if q:
        qs = qs.filter(product__name__icontains=q)

    aggregated = qs.values("product_id").annotate(total_sold=Sum("quantity")).order_by("-total_sold")

    product_ids = [r["product_id"] for r in aggregated]
    product_map = {p.id: p for p in Product.objects.filter(id__in=product_ids)}

    items = [{"product": product_map.get(r["product_id"]), "total_sold": r["total_sold"] or 0} for r in aggregated]

    paginator = Paginator(items, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return _render(request, ["sales/top_selling_products.html", "top_selling_products.html"], {
        "top_products": page_obj.object_list,
        "is_paginated": page_obj.has_other_pages(),
        "page_obj": page_obj,
        "paginator": paginator,
        "query": q,
        "branch": branch,
        "role": role,
    })


# ───────────────────────────────────────────────────────────────
# ADMIN ONLY PAGES
# ───────────────────────────────────────────────────────────────
@login_required
@role_required(["admin"])
def customer_list(request):
    q = (request.GET.get("q") or "").strip()

    qs = Customer.objects.all().order_by("-id")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q))

    page_obj = Paginator(qs, 25).get_page(request.GET.get("page"))

    return _render(request, ["sales/customer_list.html", "customer_list.html"], {
        "customers": page_obj.object_list,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "query": q,
    })


@login_required
@role_required(["admin"])
def supplier_list(request):
    q = (request.GET.get("q") or "").strip()

    qs = Supplier.objects.all().order_by("-id")
    if q:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(phone__icontains=q) |
            Q(contact__icontains=q) |
            Q(address__icontains=q)
        )

    page_obj = Paginator(qs, 25).get_page(request.GET.get("page"))

    return _render(request, ["sales/supplier_list.html", "supplier_list.html"], {
        "suppliers": page_obj.object_list,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "query": q,
    })


@login_required
@role_required(["admin"])
def purchase_invoices(request):
    qs = Invoice.objects.filter(status="debt", paid=False)
    return _render(request, ["sales/invoice_list.html", "invoice_list.html"], {
        "invoices": qs,
        "title": "Purchase Invoices Due",
    })


@login_required
@role_required(["admin"])
def sales_invoices(request):
    qs = Invoice.objects.filter(status="cash")
    return _render(request, ["sales/invoice_list.html", "invoice_list.html"], {
        "invoices": qs,
        "title": "Sales Invoices",
    })


@login_required
@role_required(["admin"])
def all_payments(request):
    qs = Payment.objects.select_related("invoice").order_by("-timestamp")
    return _render(request, ["sales/payment_list.html", "payment_list.html"], {"payments": qs})


# ───────────────────────────────────────────────────────────────
# STOCK MANAGEMENT (ADMIN)
# ───────────────────────────────────────────────────────────────
@login_required
@role_required(["admin"])
def stock_management(request):
    """
    Admin Stock Management:
    - Choose branch (sales branches only)
    - Search product
    - Add (restock) or Reduce stock
    - Stock updates ONLY via StockEntry
    - Shows recent movements
    """
    branches = _branches_for_sales_dropdown()

    branch_id = (request.GET.get("branch") or request.POST.get("branch") or "").strip()
    selected_branch = branches.filter(pk=branch_id).first() if branch_id else branches.first()

    movements_qs = StockEntry.objects.select_related("product", "branch").order_by("-timestamp")
    if selected_branch:
        movements_qs = movements_qs.filter(branch=selected_branch)

    if request.method == "POST":
        product_id = (request.POST.get("product_id") or "").strip()
        qty = _safe_int(request.POST.get("quantity"), 0) or 0
        action = (request.POST.get("action") or "add").strip().lower()
        note = (request.POST.get("note") or "").strip()

        if not selected_branch:
            messages.error(request, "Please select a branch.")
        elif not product_id:
            messages.error(request, "Please choose a product.")
        elif qty <= 0:
            messages.error(request, "Enter a valid quantity (greater than 0).")
        else:
            product = get_object_or_404(Product, pk=product_id)
            if action == "reduce":
                delta = -abs(int(qty))
                action_label = "Reduced"
            else:
                delta = abs(int(qty))
                action_label = "Added"

            try:
                with transaction.atomic():
                    StockEntry.objects.create(
                        product=product,
                        branch=selected_branch,
                        change=delta,
                        note=note or f"{action_label} via Stock Management",
                    )

                    new_qty = (
                        ProductStock.objects.filter(product=product, branch=selected_branch)
                        .values_list("quantity", flat=True)
                        .first()
                    ) or 0

                messages.success(
                    request,
                    f"{action_label} {abs(delta)} to '{product.name}' ({selected_branch.name}). New stock: {new_qty}"
                )
            except Exception as e:
                messages.error(request, str(e))

    return _render(request, ["sales/stock_management.html", "stock_management.html"], {
        "branches": branches,
        "selected_branch": str(selected_branch.id) if selected_branch else "",
        "selected_branch_obj": selected_branch,
        "movements": movements_qs[:60],
        "today_str": localdate().isoformat(),
    })


@login_required
@role_required(["admin"])
def stock_search_products(request):
    """
    AJAX product search for Stock Management:
    /sales/stock/search/?q=...&branch=ID
    Returns: [{id,name,price,stock}]
    """
    q = (request.GET.get("q") or "").strip()
    branch_id = (request.GET.get("branch") or "").strip()

    branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None

    qs = Product.objects.select_related("category").order_by("name")

    if branch:
        ps = ProductStock.objects.filter(product=OuterRef("pk"), branch=branch).values("quantity")[:1]
        qs = qs.annotate(branch_qty=Coalesce(Subquery(ps, output_field=IntegerField()), Value(0)))
    else:
        qs = qs.annotate(branch_qty=Value(0, output_field=IntegerField()))

    if q:
        qs = qs.filter(name__icontains=q)

    results = []
    for p in qs[:30]:
        results.append({
            "id": p.id,
            "name": p.name,
            "price": float(_product_selling_price(p) or 0),
            "stock": int(getattr(p, "branch_qty", 0) or 0),
        })

    return JsonResponse(results, safe=False)
# sales/views.py
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.db.models import Sum
from django.core.paginator import Paginator
from django.utils import timezone

from .models import Customer, Invoice, Payment
from users.utils import role_required


@login_required
@role_required(["admin", "cashier", "seller"])
def customer_debt_statement(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)

    # Screen UI invoices (latest first)
    invoices_qs = (
        Invoice.objects
        .filter(customer=customer, status="debt")
        .prefetch_related("items__product", "payments")
        .order_by("-created_at")
    )

    # totals (keep Decimal; Payment is integer TSH so cast to Decimal safely)
    total_debt = invoices_qs.aggregate(t=Sum("total_amount"))["t"] or Decimal("0")
    total_paid_int = Payment.objects.filter(
        invoice__customer=customer,
        invoice__status="debt",
    ).aggregate(t=Sum("amount"))["t"] or 0

    total_paid = Decimal(total_paid_int)
    balance = Decimal(total_debt) - total_paid
    if balance < 0:
        balance = Decimal("0")

    # paginate screen invoices
    invoices_page = Paginator(invoices_qs, 10).get_page(request.GET.get("page", 1))

    # ------------------------------------------------------------
    # THERMAL STATEMENT (chronological): invoice=DEBIT, payment=CREDIT
    # ------------------------------------------------------------
    invoices_for_statement = (
        Invoice.objects
        .filter(customer=customer, status="debt")
        .prefetch_related("items__product", "payments")
        .order_by("created_at")
    )

    events = []
    for inv in invoices_for_statement:
        items_count = inv.items.all().count()

        # DEBIT: customer took goods (invoice created)
        inv_total = Decimal(inv.total_amount or 0)
        events.append({
            "dt": inv.created_at,
            "ref": f"INV#{inv.id}",
            "desc": f"Goods taken ({items_count} items)",
            "debit": inv_total,
            "credit": Decimal("0"),
        })

        # CREDIT: payments made
        for p in inv.payments.all():  # Payment.ordering is -timestamp, but we sort later
            who = f" by {p.created_by.username}" if p.created_by else ""
            events.append({
                "dt": p.timestamp,
                "ref": f"PAY#{p.id}",
                "desc": f"Payment{who}",
                "debit": Decimal("0"),
                "credit": Decimal(p.amount or 0),
            })

    events.sort(key=lambda x: x["dt"] or timezone.now())

    # running balance
    running = Decimal("0")
    rows = []
    for e in events:
        running = running + e["debit"] - e["credit"]
        e["balance"] = running
        rows.append(e)

    return render(request, "sales/customer_debt_statement.html", {
        "customer": customer,
        "invoices": invoices_page,
        "total_debt": total_debt,
        "total_paid": total_paid,
        "balance": balance,
        "rows": rows,  # <-- for thermal print
    })



# sales/views.py (stock transfer part)

import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from inventory.models import Branch, Product, ProductStock
from users.utils import role_required
from .models import StockTransfer
from .forms import StockTransferForm, StockTransferItemFormSet


def sales_branches_qs():
    return (
        Branch.objects.filter(is_active=True)
        .exclude(name__icontains="transport")
        .exclude(name__icontains="hama na")
        .exclude(name__icontains="hq")
        .order_by("name")
    )


def user_branch(request):
    prof = getattr(request.user, "profile", None)
    return getattr(prof, "branch", None)


def user_role(request):
    prof = getattr(request.user, "profile", None)
    return (getattr(prof, "role", "") or "").lower()


@login_required
@role_required(["admin", "seller", "cashier"])
def stock_transfer_list(request):
    role = user_role(request)
    branch = user_branch(request)

    qs = StockTransfer.objects.select_related("from_branch", "to_branch", "created_by", "posted_by")

    # seller sees only their transfers
    if role == "seller" and branch:
        qs = qs.filter(Q(from_branch=branch) | Q(to_branch=branch))

    # branch filter dropdown (✅ sales branches only)
    branches = sales_branches_qs()

    f_branch = (request.GET.get("branch") or "").strip()
    if f_branch:
        qs = qs.filter(Q(from_branch_id=f_branch) | Q(to_branch_id=f_branch))

    status = (request.GET.get("status") or "").strip()
    if status:
        qs = qs.filter(status=status)

    transfers = qs.order_by("-id")[:500]

    return render(request, "sales/stock_transfer_list.html", {
        "transfers": transfers,
        "branches": branches,
        "selected_branch": f_branch,
        "selected_status": status,
    })


@login_required
@role_required(["admin", "seller"])
def stock_transfer_create(request):
    role = user_role(request)
    branch = user_branch(request)

    if request.method == "POST":
        form = StockTransferForm(request.POST, user=request.user)
        formset = StockTransferItemFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            transfer = form.save(commit=False)
            transfer.created_by = request.user

            # ✅ seller: force from_branch
            if role == "seller" and branch:
                transfer.from_branch = branch

            transfer.save()
            formset.instance = transfer
            formset.save()

            messages.success(request, f"Transfer #{transfer.id} created.")
            return redirect("sales:stock_transfer_detail", transfer.id)

        messages.error(request, "Please fix the errors below.")
    else:
        form = StockTransferForm(user=request.user)
        formset = StockTransferItemFormSet()

    products_json = json.dumps(list(Product.objects.values("id", "name")))

    return render(request, "sales/stock_transfer_form.html", {
        "form": form,
        "formset": formset,
        "products_json": products_json,
    })


@login_required
@role_required(["admin", "seller", "cashier"])
def stock_transfer_detail(request, pk):
    transfer = get_object_or_404(
        StockTransfer.objects.select_related("from_branch", "to_branch", "created_by", "posted_by"),
        pk=pk
    )

    role = user_role(request)
    branch = user_branch(request)

    # ✅ seller permission
    if role == "seller" and branch:
        if transfer.from_branch_id != branch.id and transfer.to_branch_id != branch.id:
            return HttpResponseForbidden("You don't have permission.")

    if request.method == "POST" and transfer.status == StockTransfer.STATUS_DRAFT and request.POST.get("save_items") == "1":
        form = StockTransferForm(request.POST, instance=transfer, user=request.user)
        formset = StockTransferItemFormSet(request.POST, instance=transfer)

        if form.is_valid() and formset.is_valid():
            t = form.save(commit=False)

            # ✅ seller keep locked
            if role == "seller" and branch:
                t.from_branch = branch

            t.save()
            formset.save()

            messages.success(request, "Transfer updated.")
            return redirect("sales:stock_transfer_detail", transfer.id)
        else:
            messages.error(request, "Fix errors below.")
    else:
        form = StockTransferForm(instance=transfer, user=request.user)
        formset = StockTransferItemFormSet(instance=transfer)

    return render(request, "sales/stock_transfer_detail.html", {
        "transfer": transfer,
        "form": form,
        "formset": formset,
        "items": transfer.items.select_related("product"),
    })


@login_required
@role_required(["admin", "seller", ])
def stock_transfer_post(request, pk):
    transfer = get_object_or_404(StockTransfer, pk=pk)

    role = user_role(request)
    branch = user_branch(request)

    if request.method != "POST":
        return redirect("sales:stock_transfer_detail", transfer.id)

    # ✅ seller can post only if from_branch == their branch
    if role == "seller" and branch and transfer.from_branch_id != branch.id:
        messages.error(request, "You can only post transfers from your branch.")
        return redirect("sales:stock_transfer_detail", transfer.id)

    try:
        transfer.post(request.user)
        messages.success(request, f"Transfer #{transfer.id} posted successfully.")
    except Exception as e:
        messages.error(request, f"Failed to post transfer: {e}")

    return redirect("sales:stock_transfer_detail", transfer.id)


@login_required
@role_required(["admin", "seller"])
def api_branch_stock(request):
    try:
        branch_id = int(request.GET.get("branch") or 0)
        product_id = int(request.GET.get("product") or 0)
    except ValueError:
        return JsonResponse({"ok": False, "qty": 0, "message": "Invalid params"}, status=400)

    if not branch_id or not product_id:
        return JsonResponse({"ok": True, "qty": 0})

    ps = ProductStock.objects.filter(branch_id=branch_id, product_id=product_id).first()
    qty = int(getattr(ps, "quantity", 0) or 0)
    return JsonResponse({"ok": True, "qty": qty})



# sales/views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from users.utils import role_required
from .models import Debtor
from .forms import DebtorForm

from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum
from django.shortcuts import render
from users.utils import role_required

from .models import Debtor, DebtorPayment  # adjust if your payment model name differs


@login_required
@role_required(["admin"])
def debtor_list(request):
    q = (request.GET.get("q") or "").strip()

    qs = Debtor.objects.all().order_by("-id")
    if q:
        qs = qs.filter(name__icontains=q)

    # Build rows with computed totals to avoid template errors
    rows = []
    total_owed_active = Decimal("0.00")
    active_count = 0
    closed_count = 0

    for d in qs:
        paid = DebtorPayment.objects.filter(debtor=d).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
        owed = Decimal(str(d.amount_owed or 0))
        balance = owed - Decimal(str(paid or 0))
        if balance < 0:
            balance = Decimal("0.00")

        # ✅ auto close if cleared
        is_active = bool(balance > 0)
        if d.is_active != is_active:
            d.is_active = is_active
            d.save(update_fields=["is_active"])

        if is_active:
            total_owed_active += balance
            active_count += 1
        else:
            closed_count += 1

        rows.append({
            "obj": d,
            "paid": paid,
            "balance": balance,
        })

    # paginate rows
    paginator = Paginator(rows, 20)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(request, "sales/debtor_list.html", {
        "debtors": page_obj.object_list,   # list of dicts: {obj, paid, balance}
        "page_obj": page_obj,
        "query": q,

        # ✅ names your template needs
        "total_owed": total_owed_active,
        "active_count": active_count,
        "closed_count": closed_count,
    })


@login_required
@role_required(["admin"])
def debtor_create(request):
    if request.method == "POST":
        form = DebtorForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()
            messages.success(request, "Debtor added successfully.")
            return redirect("sales:debtor_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = DebtorForm()

    return render(request, "sales/debtor_form.html", {
        "form": form,
        "is_edit": False,
    })


@login_required
@role_required(["admin"])
def debtor_update(request, pk):
    debtor = get_object_or_404(Debtor, pk=pk)

    if request.method == "POST":
        form = DebtorForm(request.POST, instance=debtor)
        if form.is_valid():
            form.save()
            messages.success(request, "Debtor updated.")
            return redirect("sales:debtor_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = DebtorForm(instance=debtor)

    return render(request, "sales/debtor_form.html", {
        "form": form,
        "is_edit": True,
        "debtor": debtor,
    })


@login_required
@role_required(["admin"])
def debtor_delete(request, pk):
    debtor = get_object_or_404(Debtor, pk=pk)

    if request.method == "POST":
        debtor.delete()
        messages.success(request, "Debtor deleted.")
        return redirect("sales:debtor_list")

    return render(request, "sales/debtor_delete.html", {
        "debtor": debtor
    })


# sales/views.py
from .models import Debtor, DebtorPayment
from .forms import DebtorForm, DebtorPaymentForm

@login_required
@role_required(["admin"])
def debtor_detail(request, pk):
    debtor = get_object_or_404(Debtor, pk=pk)
    payments = debtor.payments.select_related("created_by").all()

    # payment form
    pay_form = DebtorPaymentForm()

    return render(request, "sales/debtor_detail.html", {
        "debtor": debtor,
        "payments": payments,
        "total_paid": debtor.total_paid(),
        "balance": debtor.balance(),
        "pay_form": pay_form,
    })


@login_required
@role_required(["admin"])
def debtor_add_payment(request, pk):
    debtor = get_object_or_404(Debtor, pk=pk)

    if request.method != "POST":
        return redirect("sales:debtor_detail", pk=debtor.id)

    form = DebtorPaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Fix payment errors.")
        return redirect("sales:debtor_detail", pk=debtor.id)

    amount = Decimal(form.cleaned_data["amount"] or 0)
    if amount <= 0:
        messages.error(request, "Payment must be greater than 0.")
        return redirect("sales:debtor_detail", pk=debtor.id)

    # Don’t allow overpay beyond balance (optional rule)
    current_balance = debtor.balance()
    if amount > current_balance:
        amount = current_balance
        messages.warning(request, f"Payment reduced to balance: {current_balance}")

    try:
        with transaction.atomic():
            p = form.save(commit=False)
            p.debtor = debtor
            p.amount = amount
            p.created_by = request.user
            p.save()

            # ✅ Auto close if finished
            if debtor.balance() <= 0:
                debtor.is_active = False
                debtor.save(update_fields=["is_active", "updated_at"])

    except Exception as e:
        messages.error(request, f"Failed: {e}")
        return redirect("sales:debtor_detail", pk=debtor.id)

    messages.success(request, "Payment recorded.")
    return redirect("sales:debtor_detail", pk=debtor.id)


@login_required
@role_required(["admin"])
def debtor_delete_payment(request, pk, payment_id):
    debtor = get_object_or_404(Debtor, pk=pk)
    payment = get_object_or_404(DebtorPayment, pk=payment_id, debtor=debtor)

    if request.method == "POST":
        payment.delete()

        # ✅ Re-open if balance becomes > 0 again
        if debtor.balance() > 0:
            debtor.is_active = True
            debtor.save(update_fields=["is_active", "updated_at"])

        messages.success(request, "Payment deleted.")
    return redirect("sales:debtor_detail", pk=debtor.id)
