# sales/views.py
from decimal import Decimal
import json
from datetime import date, datetime, timedelta
from django.shortcuts import render

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum, OuterRef, Subquery, IntegerField, Value
from django.db.models.functions import TruncMonth, TruncYear, TruncDate, TruncWeek, Coalesce
from django.http import JsonResponse, Http404, HttpResponseForbidden, HttpResponse
from django.shortcuts import redirect, get_object_or_404
from django.template.loader import select_template
from django.utils import timezone
from django.utils.timezone import localdate
from django.utils.dateformat import format as datefmt
# sales/views.py
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from users.utils import role_required

from .models import Customer
from inventory.models import Supplier
from inventory.models import (
    Product, Category, StockEntry, Supplier,
    Branch, ProductStock
)
from .models import Invoice, InvoiceItem, Customer, Payment, Expense
from users.utils import role_required
from users.models import Role


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


def _is_admin(request):
    profile = _get_user_profile(request)
    return request.user.is_superuser or (profile and profile.role == Role.ADMIN)


def _is_cashier(request):
    role = _role_lower(request)
    return role in (Role.CASHIER, "cashier")


def _is_seller(request):
    role = _role_lower(request)
    return role in (Role.SELLER, "seller")


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
        # show only with stock > 0 in legacy mode
        qs = qs.filter(stock__gt=0)
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
        return int(p.stock) if p else 0

    return int(
        ProductStock.objects.filter(product_id=product_id, branch=branch)
        .values_list("quantity", flat=True)
        .first() or 0
    )


def _adjust_branch_stock_locked(product, branch, delta):
    """
    Atomic safe change:
      delta negative for sale, positive for restock
    """
    ps = ProductStock.objects.select_for_update().filter(product=product, branch=branch).first()
    if not ps:
        # create row if restocking or first time
        ps = ProductStock(product=product, branch=branch, quantity=0)

    new_qty = int(ps.quantity) + int(delta)
    if new_qty < 0:
        raise ValueError(f"Insufficient stock for '{product.name}' in {branch.name}. Available: {ps.quantity}")

    ps.quantity = new_qty
    ps.save()
    return new_qty


def _invoice_scope_qs(request):
    """
    Who can see which invoices:
      - Admin/Cashier: all invoices (cashier view-only)
      - Seller: only own branch invoices if invoice has branch, else only own invoices.
    """
    qs = Invoice.objects.select_related("customer", "seller").order_by("-created_at")

    if _is_admin(request) or _is_cashier(request):
        return qs

    if _is_seller(request):
        branch = _get_user_branch(request)
        if branch and hasattr(Invoice, "branch_id"):
            return qs.filter(branch=branch)
        return qs.filter(seller=request.user)

    # default: none
    return qs.none()


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
# LANDING REDIRECT "/"
# ───────────────────────────────────────────────────────────────
@login_required
def dashboard_redirect(request):
    role = _role_lower(request)

    if request.user.is_superuser or role in (Role.ADMIN, "admin"):
        return redirect("sales:admin_dashboard")

    # Cashier -> admin dashboard but read-only
    if role in (Role.CASHIER, "cashier"):
        return redirect("sales:admin_dashboard")

    # Transport if you have it
    if role in (Role.TRANSPORT, "transport"):
        return redirect("transport:index")

    # Seller -> POS
    if role in (Role.SELLER, "seller"):
        return redirect("sales:seller_dashboard")

    return redirect("login")


# ───────────────────────────────────────────────────────────────
# SELLER POS (BRANCH-AWARE)
# ───────────────────────────────────────────────────────────────
@login_required
@role_required(["seller", "admin"])
def seller_dashboard(request):
    branch = _get_user_branch(request)

    raw_cart = request.session.get("cart", {})
    cart = {}

    # normalize cart
    for pid, entry in raw_cart.items():
        if isinstance(entry, dict):
            qty = max(1, int(entry.get("qty", 1)))
            disc = max(0, int(entry.get("discount", 0)))
        else:
            qty, disc = max(1, int(entry)), 0
        cart[str(pid)] = {"qty": qty, "discount": disc}

    request.session["cart"] = cart
    request.session.modified = True

    # build cart items
    cart_items = []
    for pid, data in cart.items():
        prod = get_object_or_404(Product, pk=int(pid))
        qty = data["qty"]
        disc = data["discount"]
        total_price = max(0, (prod.price - disc) * qty)

        visible_stock = _get_branch_qty(prod.pk, branch) if (_is_seller(request) and branch) else int(prod.stock)

        cart_items.append({
            "product": prod,
            "quantity": qty,
            "discount": disc,
            "total_price": total_price,
            "visible_stock": visible_stock,
        })

    cat = (request.GET.get("category") or "").strip() or None
    q = (request.GET.get("q") or "").strip() or None

    # products JSON branch-aware
    qs, stock_field = _branch_products_qs(branch if _is_seller(request) else None, cat=cat, q=q)
    products_data = []
    for p in qs:
        stock_val = getattr(p, stock_field) if stock_field else p.stock
        stock_val = int(stock_val or 0)
        products_data.append({
            "id": p.id,
            "display": f"{p.name} — Tsh{p.price:.2f} (Stock: {stock_val})",
            "category": p.category_id,
            "stock": stock_val,
            "reorder_level": getattr(p, "reorder_level", 0),
        })

    return _render(request, ["sales/seller_dashboard.html", "seller_dashboard.html"], {
        "categories": Category.objects.values_list("id", "name"),
        "products_json": json.dumps(products_data),
        "cart_items": cart_items,
        "selected_cat": cat or "",
        "search_q": q or "",
        "branch": branch,
    })


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
        stock_val = getattr(p, stock_field) if stock_field else p.stock
        stock_val = int(stock_val or 0)
        results.append({
            "id": p.id,
            "label": f"{p.name} — Tsh{p.price:.2f} (Stock: {stock_val})"
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

    stock_val = _get_branch_qty(prod.pk, branch) if branch else int(prod.stock)

    return JsonResponse({
        "status": "success",
        "product": {
            "id": prod.pk,
            "name": prod.name,
            "price": str(prod.price),
            "stock": stock_val,
        }
    })


from django.core.exceptions import ValidationError
from django.db import transaction
from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404

@login_required
@role_required(["seller", "admin"])
def submit_sale(request):
    """
    Seller handles everything:
      - cash/bank sale => paid=True immediately
      - debt => paid=False and appears in debts list
      - stock updates ONLY via StockEntry (StockEntry.save updates ProductStock)
      - branch-aware: seller sells only from their branch
    """
    if request.method != "POST":
        return redirect("sales:seller_dashboard")

    cart = request.session.get("cart", {})
    if not cart:
        messages.warning(request, "Cart is empty.")
        return redirect("sales:seller_dashboard")

    # Seller branch (admin may have None -> system will use default branch in StockEntry)
    branch = _get_user_branch(request) if _is_seller(request) else None

    payment = (request.POST.get("payment_method") or "debt").strip().lower()
    customer_name = (request.POST.get("customer_name") or "").strip()
    customer_phone = (request.POST.get("customer_phone") or "").strip()

    # Accept these payment values safely
    if payment not in ("cash", "debt", "bank"):
        payment = "debt"

    # Customer only required for debt
    if payment == "debt":
        customer, _ = Customer.objects.get_or_create(
            name=customer_name or "Unknown",
            phone=customer_phone or "N/A",
        )
        paid_flag = False
    else:
        customer = None
        paid_flag = True  # cash/bank -> paid immediately

    try:
        with transaction.atomic():
            # 1) Validate stock FIRST (prevents partial invoice)
            #    Use select_for_update to lock stock rows during sale.
            if branch:
                for pid, data in cart.items():
                    qty = int(data.get("qty", 1))
                    qty = max(1, qty)

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
            # Optional: if Invoice has branch field
            if branch and hasattr(Invoice, "branch_id"):
                inv_kwargs["branch"] = branch

            inv = Invoice.objects.create(**inv_kwargs)

            # 3) Create items + stock entries (NO manual stock reduce!)
            for pid, data in cart.items():
                prod = get_object_or_404(Product, pk=int(pid))

                qty = max(1, int(data.get("qty", 1)))
                disc = max(0, int(data.get("discount", 0)))

                # item first (fine)
                InvoiceItem.objects.create(
                    invoice=inv,
                    product=prod,
                    quantity=qty,
                    unit_price=prod.price,
                    discount=disc,
                )

                # ✅ Stock change only via StockEntry (this updates ProductStock automatically)
                StockEntry.objects.create(
                    product=prod,
                    branch=branch if branch else None,   # if None, StockEntry will auto-pick default branch
                    change=-qty,
                    note=f"Sale #{inv.pk} ({payment})",
                )

            # 4) Totals
            inv.calculate_totals()

    except (ValueError, ValidationError) as e:
        messages.error(request, str(e))
        return redirect("sales:seller_dashboard")

    # clear cart
    request.session["cart"] = {}
    request.session.modified = True

    return redirect("sales:invoice_detail", pk=inv.pk)

# ───────────────────────────────────────────────────────────────
# INVOICE DETAIL (read-only for cashier, seller can view own)
# ───────────────────────────────────────────────────────────────
@login_required
@role_required(["seller", "admin", "cashier"])
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("customer", "seller"), pk=pk)

    if not _must_own_invoice_if_seller(request, inv):
        return HttpResponseForbidden("You cannot access this invoice.")

    return _render(request, ["sales/invoice_detail.html", "invoice_detail.html"], {
        "invoice": inv
    })


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


def _invoice_total(inv) -> int:
    """
    Optional helper if you need integer totals for comparisons/printing.
    """
    t = getattr(inv, "total_amount", 0)
    try:
        return int(t or 0)
    except Exception:
        return 0


# ───────────────────────────────────────────────────────────────
# SELLER DEBTS + PAYMENTS (SELLER DOES THIS, NOT CASHIER)
# ───────────────────────────────────────────────────────────────
@login_required
@role_required(["seller", "admin"])
def outstanding_debts(request):
    q = (request.GET.get("q") or "").strip()

    qs = _invoice_scope_qs(request).filter(status="debt", paid=False)
    if q:
        qs = qs.filter(customer__name__icontains=q)

    debts = list(qs)

    # ✅ FIX: do NOT call inv.balance() directly
    total_outstanding = sum(_invoice_balance(inv) for inv in debts)

    top_debtor = max(debts, key=_invoice_balance) if debts else None
    least_debtor = min(debts, key=_invoice_balance) if debts else None

    return _render(request, ["sales/outstanding_debts.html", "outstanding_debts.html"], {
        "debts": debts,
        "query": q,
        "total_outstanding": total_outstanding,
        "top_debtor": top_debtor,
        "least_debtor": least_debtor,

        # optional: helper if you want to show balance per row easily
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

        # optional: prevent overpayment
        bal = _invoice_balance(inv)
        if amount > bal:
            amount = bal  # clamp
            messages.warning(request, f"Amount reduced to balance: {bal} TSH")

        Payment.objects.create(
            invoice=inv,
            amount=amount,
            created_by=request.user  # ✅ you have this field in Payment
        )

        messages.success(request, "Payment recorded.")
        return redirect("sales:invoice_detail", pk=inv.pk)

    return _render(request, ["sales/record_payment.html", "record_payment.html"], {
        "invoice": inv,
        "balance": _invoice_balance(inv),  # ✅ FIX HERE
        "history": inv.payments.order_by("-timestamp"),
    })



def _safe_int(v):
    try:
        v = (v or "").strip()
        return int(v) if v else None
    except ValueError:
        return None







def _branches_for_sales_dropdown():
    """
    Exclude branches you don't want in Sales dashboard dropdown
    (Transport HQ has its own dashboard).
    Adjust these excludes to match your real names.
    """
    return (
        Branch.objects.filter(is_active=True)
        .exclude(name__icontains="transport")
        .exclude(name__icontains="hq")
        .order_by("name")
    )


@login_required
@role_required(["admin", "cashier"])
def admin_dashboard(request):
    today = localdate()

    # ✅ Admin + Cashier can filter (but only SALES branches, not Transport HQ)
    branches_qs = _branches_for_sales_dropdown()

    branch_id = (request.GET.get("branch") or "").strip()
    selected_branch = None
    if branch_id:
        # ✅ IMPORTANT: only allow selecting from branches_qs
        selected_branch = branches_qs.filter(pk=branch_id).first()

    # Base querysets
    inv_qs = Invoice.objects.all()
    pay_qs = Payment.objects.select_related("invoice")
    exp_qs = Expense.objects.all()

    # Apply branch filter
    if selected_branch:
        if hasattr(Invoice, "branch_id"):
            inv_qs = inv_qs.filter(branch=selected_branch)
            pay_qs = pay_qs.filter(invoice__branch=selected_branch)
        if hasattr(Expense, "branch_id"):
            exp_qs = exp_qs.filter(branch=selected_branch)

    # Today sales (paid only for cash/bank)
    total_cash_today = (
        inv_qs.filter(created_at__date=today, status="cash", paid=True)
        .aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
    )
    total_bank_today = (
        inv_qs.filter(created_at__date=today, status="bank", paid=True)
        .aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
    )
    total_today = total_cash_today + total_bank_today

    # Month/year filters
    month = _safe_int(request.GET.get("month"))
    year = _safe_int(request.GET.get("year"))

    if month and year:
        cash = (
            inv_qs.filter(status="cash", paid=True, created_at__month=month, created_at__year=year)
            .aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
        )
        bank = (
            inv_qs.filter(status="bank", paid=True, created_at__month=month, created_at__year=year)
            .aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
        )
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
        debt_payments = pay_qs.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
        expenses_total = exp_qs.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")

    overall_sales = cash + bank + debt_payments

    # counts
    num_debts = inv_qs.filter(status="debt", paid=False).count()
    num_cash_sales = inv_qs.filter(status="cash", paid=True).count()
    num_bank_sales = inv_qs.filter(status="bank", paid=True).count()
    total_transactions = inv_qs.count()

    # Today expenses + net
    expenses_today = exp_qs.filter(date=today).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    net_today = total_today - expenses_today
    net_overall = overall_sales - expenses_total

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

        # ✅ dropdown will NOT show Transport/HQ
        "branches": branches_qs,
        "selected_branch": str(selected_branch.id) if selected_branch else "",
        "selected_branch_obj": selected_branch,

        "total_cash_today": total_cash_today,
        "total_bank_today": total_bank_today,
        "total_today": total_today,

        "selected_month": month or "",
        "selected_year": year or "",

        "total_debt_payments": debt_payments,
        "overall_sales": overall_sales,

        "expenses_today": expenses_today,
        "expenses_total": expenses_total,

        "net_today": net_today,
        "net_overall": net_overall,

        "num_debts": num_debts,
        "num_cash_sales": num_cash_sales,
        "num_bank_sales": num_bank_sales,
        "total_transactions": total_transactions,

        "recent_products": recent_products,
        "months": months,
        "years": years,
        "today_str": today.isoformat(),
    })



# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def _render(request, template_candidates, context):
    tpl = select_template(template_candidates)
    return render(request, tpl.template.name, context)


def _safe_int(val):
    try:
        v = int(val)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None



def _branches_for_sales_dropdown():
    """
    You said: remove transport from this dashboard filter.
    Long-term best: add a field in Branch like type='sales/transport/both'.
    For now: we exclude any branch whose name contains 'transport'.
    """
    return (
        Branch.objects.filter(is_active=True)
        .exclude(name__icontains="transport")
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

    return Branch.objects.filter(pk=branch_id, is_active=True).first()


def _invoice_scope_qs(request):
    """
    Base invoice scope:
    - seller => own branch only (if branch exists)
    - admin/cashier => all
    """
    qs = Invoice.objects.select_related("customer", "seller", "branch")

    if _is_seller(request):
        b = _get_user_branch(request)
        if b and hasattr(Invoice, "branch_id"):
            qs = qs.filter(branch=b)
        else:
            qs = qs.filter(seller=request.user)

    return qs


def _expense_scope_qs(request):
    qs = Expense.objects.select_related("branch", "created_by")

    if _is_seller(request):
        b = _get_user_branch(request)
        if b and hasattr(Expense, "branch_id"):
            qs = qs.filter(branch=b)

    return qs


def _payment_scope_qs(request):
    qs = Payment.objects.select_related("invoice", "invoice__branch", "created_by")

    if _is_seller(request):
        b = _get_user_branch(request)
        if b and hasattr(Invoice, "branch_id"):
            qs = qs.filter(invoice__branch=b)

    return qs

# -------------------------------------------------------------------
# REPORTS (cashier read-only allowed)
#   ✅ Admin + Cashier: can filter by ?branch=
#   ✅ Seller: locked to own branch (ignores ?branch=)
# -------------------------------------------------------------------

@login_required
@role_required(["admin", "cashier", "seller"])
def sales_history(request):
    selected = _selected_branch(request)  # seller locked; admin/cashier optional

    qs = _invoice_scope_qs(request)

    # ✅ apply branch filter ONLY for admin/cashier (seller already scoped by _invoice_scope_qs)
    if selected and hasattr(Invoice, "branch_id") and _can_filter_branch(request):
        qs = qs.filter(branch=selected)

    paginator = Paginator(qs.order_by("-created_at"), 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    return _render(request, ["sales/sales_history.html", "sales_history.html"], {
        "invoices": page_obj.object_list,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "paginator": paginator,

        # ✅ admin + cashier see dropdown; seller no dropdown
        "branches": _branches_for_sales_dropdown() if _can_filter_branch(request) else [],
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
    selected_date = request.GET.get("date") or today.isoformat()

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

    # ✅ admin/cashier can filter by selected branch
    if selected and hasattr(Invoice, "branch_id") and _can_filter_branch(request):
        inv_qs = inv_qs.filter(branch=selected)

    if selected and hasattr(Expense, "branch_id") and _can_filter_branch(request):
        exp_qs = exp_qs.filter(branch=selected)

    sales_qs = inv_qs.filter(created_at__date=report_date).order_by("-created_at")
    expenses_qs = exp_qs.filter(date=report_date).order_by("-date", "-id")

    total_sales = sales_qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
    total_expenses = expenses_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    net_sales = total_sales - total_expenses

    sales_page = Paginator(sales_qs, 20).get_page(request.GET.get("sales_page", 1))
    exp_page = Paginator(expenses_qs, 20).get_page(request.GET.get("exp_page", 1))

    return _render(request, ["sales/kilasi_sales_report.html", "kilasi_sales_report.html"], {
        "selected_date": selected_date,
        "prev_date": prev_date,
        "next_date": next_date,

        "total_sales": total_sales,
        "total_expenses": total_expenses,
        "net_sales": net_sales,
        "sales_page": sales_page,
        "exp_page": exp_page,

        # ✅ admin + cashier see dropdown; seller no dropdown
        "branches": _branches_for_sales_dropdown() if _can_filter_branch(request) else [],
        "selected_branch": str(selected.id) if selected else "",
        "selected_branch_obj": selected,
        "today_str": today.isoformat(),
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def enhanced_sales_report(request):
    """
    Charts:
    - Daily: last 7 days (or chosen date)
    - Weekly: last 12 weeks
    - Monthly: last 12 months (filter month/year optional)
    - Yearly: last 5 years (filter year optional)
    - Doughnut: Income vs Expenses vs Profit for chosen month/year
      Income = cash+bank (paid) + debt payments
    """
    today = localdate()
    selected = _selected_branch(request)

    # Filters (charts)
    date_filter = (request.GET.get("date") or "").strip()
    month_filter = _safe_int(request.GET.get("month"))
    year_filter = _safe_int(request.GET.get("year"))

    # Filters (doughnut)
    doughnut_month = _safe_int(request.GET.get("doughnut_month")) or today.month
    doughnut_year = _safe_int(request.GET.get("doughnut_year")) or today.year

    inv_qs = _invoice_scope_qs(request)
    exp_qs = _expense_scope_qs(request)
    pay_qs = _payment_scope_qs(request)

    # ✅ admin/cashier can filter by selected branch
    if selected and hasattr(Invoice, "branch_id") and _can_filter_branch(request):
        inv_qs = inv_qs.filter(branch=selected)
        pay_qs = pay_qs.filter(invoice__branch=selected)

    if selected and hasattr(Expense, "branch_id") and _can_filter_branch(request):
        exp_qs = exp_qs.filter(branch=selected)

    # Date range for daily chart
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

    # ------------------ Daily (fill missing days) ------------------
    daily_raw = (
        inv_qs.filter(created_at__date__gte=start, created_at__date__lte=end)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Sum("total_amount"))
        .order_by("day")
    )
    daily_map = {r["day"]: float(r["total"] or 0) for r in daily_raw}
    days_count = (end - start).days + 1
    daily = []
    for i in range(days_count):
        d = start + timedelta(days=i)
        daily.append({"day": d.isoformat(), "total": daily_map.get(d, 0)})

    # ------------------ Weekly (last 12 weeks) ------------------
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

    # ------------------ Monthly (last 12 months, filter optional) ------------------
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
        monthly.append({
            "month": m.month,
            "year": m.year,
            "total": monthly_map.get(m, 0)
        })

    # ------------------ Yearly (last 5 years, filter optional) ------------------
    years_list = [today.year - 4, today.year - 3, today.year - 2, today.year - 1, today.year]
    yearly_raw = (
        inv_qs.annotate(y=TruncYear("created_at"))
        .values("y")
        .annotate(total=Sum("total_amount"))
        .order_by("y")
    )
    yearly_map = {r["y"].year: float(r["total"] or 0) for r in yearly_raw if r["y"]}

    yearly = []
    for y in years_list:
        if year_filter and y != year_filter:
            continue
        yearly.append({"year": y, "total": yearly_map.get(y, 0)})

    # ------------------ Doughnut (Income vs Expenses vs Profit) ------------------
    income_sales = (
        inv_qs.filter(
            created_at__year=doughnut_year,
            created_at__month=doughnut_month,
            status__in=["cash", "bank"],
            paid=True
        ).aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
    )

    income_debt = (
        pay_qs.filter(timestamp__year=doughnut_year, timestamp__month=doughnut_month)
        .aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    )
    doughnut_sales_total = Decimal(income_sales) + Decimal(income_debt)

    doughnut_total_expenses = (
        exp_qs.filter(date__year=doughnut_year, date__month=doughnut_month)
        .aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    )

    doughnut_net_profit = doughnut_sales_total - doughnut_total_expenses
    profit_is_negative = doughnut_net_profit < 0
    doughnut_profit_for_chart = doughnut_net_profit if doughnut_net_profit > 0 else Decimal("0.00")

    month_choices = [
        (1, "January"), (2, "February"), (3, "March"), (4, "April"),
        (5, "May"), (6, "June"), (7, "July"), (8, "August"),
        (9, "September"), (10, "October"), (11, "November"), (12, "December"),
    ]
    year_choices = list(range(today.year - 7, today.year + 1))

    return _render(request, ["sales/enhanced_sales_report.html", "enhanced_sales_report.html"], {
        # ✅ admin + cashier see dropdown; seller no dropdown
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
        "doughnut_net_profit": float(doughnut_net_profit),
        "doughnut_profit_for_chart": float(doughnut_profit_for_chart),
        "profit_is_negative": profit_is_negative,
    })


# -------------------------------------------------------------------
# EXPENSES
# -------------------------------------------------------------------
@login_required
@role_required(["admin", "cashier", "seller"])
def add_expense(request):
    """
    Seller: branch locked to own branch
    Admin/Cashier: can choose branch from dropdown
    """
    branches = _branches_for_sales_dropdown() if not _is_seller(request) else []
    selected = _selected_branch(request)

    if request.method == "POST":
        date_val = request.POST.get("date") or None
        amount = request.POST.get("amount", "0")
        category = (request.POST.get("category") or "").strip()
        description = (request.POST.get("description") or "").strip()

        # determine branch
        branch_obj = None
        if _is_seller(request):
            branch_obj = _get_user_branch(request)
        else:
            # admin/cashier can select in form
            form_branch_id = (request.POST.get("branch") or "").strip()
            if form_branch_id:
                branch_obj = Branch.objects.filter(pk=form_branch_id, is_active=True).first()
            elif selected:
                branch_obj = selected  # fallback from ?branch=

        Expense.objects.create(
            date=date_val,
            amount=amount,
            category=category,
            description=description,
            created_by=request.user,
            branch=branch_obj if hasattr(Expense, "branch_id") else None,
        )
        return redirect("sales:expense_history")

    return _render(request, ["sales/kilasi_add_expense.html", "kilasi_add_expense.html"], {
        "branches": branches,
        "selected_branch": str(selected.id) if selected else "",
        "selected_branch_obj": selected,
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def expense_history(request):
    exact_date = (request.GET.get("date") or "").strip()

    # Branch logic:
    branch_id = (request.GET.get("branch") or "").strip()
    selected_branch = None

    if _is_admin(request):
        if branch_id:
            selected_branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
    else:
        selected_branch = _get_user_branch(request)

    qs = Expense.objects.all().order_by("-date", "-id")

    if selected_branch:
        qs = qs.filter(branch=selected_branch)

    if exact_date:
        qs = qs.filter(date=exact_date)

    branches = Branch.objects.filter(is_active=True).order_by("name") if _is_admin(request) else None

    return _render(request, ["sales/kilasi_expense_history.html", "kilasi_expense_history.html"], {
        "expenses": qs[:300],  # keep fast
        "exact_date": exact_date,

        "branches": branches,
        "selected_branch": str(selected_branch.id) if selected_branch else "",
        "selected_branch_obj": selected_branch,
        "is_admin": _is_admin(request),
    })

@login_required
@role_required(["admin", "cashier", "seller"])
def expense_overview(request):
    today = localdate()

    # Branch logic:
    # - Admin: can filter with ?branch=ID
    # - Cashier/Seller: forced to their own branch
    branch_id = (request.GET.get("branch") or "").strip()
    selected_branch = None

    if _is_admin(request):
        if branch_id:
            selected_branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
    else:
        selected_branch = _get_user_branch(request)

    inv_qs = Invoice.objects.all()
    exp_qs = Expense.objects.all()

    if selected_branch:
        inv_qs = inv_qs.filter(branch=selected_branch)
        exp_qs = exp_qs.filter(branch=selected_branch)

    # Sales Today (cash+bank paid only)
    sales_today = (
        inv_qs.filter(created_at__date=today, status__in=["cash", "bank"], paid=True)
        .aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
    )

    expenses_today = (
        exp_qs.filter(date=today).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    )

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
        qs = qs.annotate(
            branch_qty=Coalesce(Subquery(ps, output_field=IntegerField()), Value(0))
        ).filter(branch_qty__gt=0)

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

    current_stock = _get_branch_qty(p.pk, branch) if branch else int(p.stock)

    return JsonResponse({
        "name": p.name,
        "category": p.category.name if p.category else "",
        "price": f"{p.price:.2f}",
        "current_stock": current_stock,
        "history": history,
    })


@login_required
@role_required(["admin", "cashier", "seller"])
def product_detail(request, pk):
    product = get_object_or_404(Product.objects.select_related("category"), pk=pk)

    is_admin = _is_admin(request)
    branch = _get_user_branch(request) if _is_seller(request) else None

    current_stock = _get_branch_qty(product.pk, branch) if branch else int(product.stock)

    # Admin restock allowed
    if request.method == "POST":
        if not is_admin:
            return HttpResponseForbidden("Only admin can restock.")

        try:
            add_qty = int(request.POST.get("add_qty", 0))
        except ValueError:
            add_qty = 0

        note = (request.POST.get("note") or "").strip()
        branch_id = (request.POST.get("branch_id") or "").strip()
        restock_branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None
        if not restock_branch:
            restock_branch = Branch.objects.first()

        if add_qty > 0 and restock_branch:
            with transaction.atomic():
                # update ProductStock
                _adjust_branch_stock_locked(product, restock_branch, +add_qty)

                # log movement
                StockEntry.objects.create(
                    product=product,
                    change=add_qty,
                    branch=restock_branch,
                    note=note or "Restocked via UI",
                )

        return redirect("sales:product_detail", pk=pk)

    # history: seller sees only their branch movements
    entries_qs = StockEntry.objects.filter(product=product)
    if branch:
        entries_qs = entries_qs.filter(branch=branch)
    entries = entries_qs.order_by("-timestamp")

    # admin can see per-branch stock list
    branch_stocks = []
    if is_admin:
        branch_stocks = (
            ProductStock.objects
            .filter(product=product)
            .select_related("branch")
            .order_by("branch__name")
        )

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

    profile = _get_user_profile(request)
    role = (getattr(profile, "role", "") or "").lower()

    # Seller sees only their branch
    branch = _get_user_branch(request) if role == "seller" else None

    # Base: aggregate from InvoiceItem -> Invoice
    qs = (
        InvoiceItem.objects
        .select_related("product", "invoice")
    )

    # ✅ Branch filter (if your Invoice model has branch)
    if branch and hasattr(Invoice, "branch_id"):
        qs = qs.filter(invoice__branch=branch)

    # ✅ If Invoice has NO branch field, fallback filter:
    # seller sees only invoices they created
    if branch and not hasattr(Invoice, "branch_id"):
        qs = qs.filter(invoice__seller=request.user)

    # optional product search
    if q:
        qs = qs.filter(product__name__icontains=q)

    # Aggregate
    aggregated = (
        qs.values("product_id", "product__name")
          .annotate(total_sold=Sum("quantity"))
          .order_by("-total_sold")
    )

    # Build list for template
    items = []
    # (we fetch Product objects once per row if you want full product object)
    product_map = {p.id: p for p in Product.objects.filter(id__in=[r["product_id"] for r in aggregated])}
    for row in aggregated:
        items.append({
            "product": product_map.get(row["product_id"]),
            "total_sold": row["total_sold"] or 0
        })

    paginator = Paginator(items, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return _render(
        request,
        ["sales/top_selling_products.html", "top_selling_products.html"],
        {
            "top_products": page_obj.object_list,
            "is_paginated": page_obj.has_other_pages(),
            "page_obj": page_obj,
            "paginator": paginator,
            "query": q,
            "branch": branch,
            "role": role,
        }
    )

# ───────────────────────────────────────────────────────────────
# ADMIN ONLY PAGES
# ───────────────────────────────────────────────────────────────
@login_required
@role_required(["admin"])
def customer_list(request):
    qs = Customer.objects.all()
    return _render(request, ["sales/customer_list.html", "customer_list.html"], {"customers": qs})


@login_required
@role_required(["admin"])
def supplier_list(request):
    qs = Supplier.objects.all()
    return _render(request, ["sales/supplier_list.html", "supplier_list.html"], {"suppliers": qs})


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


@login_required
@role_required(["admin"])
def stock_management(request):
    return _render(request, ["sales/stock_management.html", "stock_management.html"], {})




@login_required
@role_required(["admin"])  # keep admin only (as you had)
def customer_list(request):
    q = (request.GET.get("q") or "").strip()

    qs = Customer.objects.all().order_by("-id")
    if q:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(phone__icontains=q)
        )

    page_obj = Paginator(qs, 25).get_page(request.GET.get("page"))

    return _render(request, ["sales/customer_list.html", "customer_list.html"], {
        "customers": page_obj.object_list,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "query": q,
    })


@login_required
@role_required(["admin"])  # keep admin only (as you had)
def supplier_list(request):
    q = (request.GET.get("q") or "").strip()

    qs = Supplier.objects.all().order_by("-id")

    # Support common Supplier fields safely (name/phone/contact/address)
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
@role_required(["seller", "admin", "cashier"])
def invoice_receipt(request, pk):
    inv = get_object_or_404(
        Invoice.objects.select_related("customer", "seller"),
        pk=pk
    )

    if not _must_own_invoice_if_seller(request, inv):
        return HttpResponseForbidden("You cannot access this invoice.")

    autoprint = (request.GET.get("autoprint") or "") == "1"

    # --- Settings (you can move these to settings.py later) ---
    company = {
        "name": "KILASI INVESTMENT",
        "tin": "TIN: 123-456-789",   # <<< change this
        "phone": "Tel: +255 XXX XXX XXX",  # optional
        "location": "Moshi, Tanzania",     # optional
        "footer": "THANK YOU FOR BUYING WITH US",
    }

    return _render(request, ["sales/thermal_receipt.html", "thermal_receipt.html"], {
        "invoice": inv,
        "autoprint": autoprint,
        "company": company,
    })





from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import OuterRef, Subquery, IntegerField, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from django.utils.timezone import localdate

from users.utils import role_required
from inventory.models import Product, Branch, ProductStock, StockEntry


def _branches_for_sales_dropdown():
    """
    Exclude Transport/HQ branches from sales stock UI.
    Adjust keywords to match your exact names.
    """
    return (
        Branch.objects.filter(is_active=True)
        .exclude(name__icontains="transport")
        .exclude(name__icontains="hq")
        .order_by("name")
    )


def _safe_int(val, default=None):
    try:
        return int(val)
    except Exception:
        return default


def _adjust_branch_stock_locked(product, branch, delta):
    """
    Atomic safe change:
      delta positive => restock
      delta negative => reduce stock
    """
    ps = ProductStock.objects.select_for_update().filter(product=product, branch=branch).first()
    if not ps:
        ps = ProductStock(product=product, branch=branch, quantity=0)

    new_qty = int(ps.quantity) + int(delta)
    if new_qty < 0:
        raise ValueError(
            f"Insufficient stock for '{product.name}' in {branch.name}. "
            f"Available: {ps.quantity}"
        )

    ps.quantity = new_qty
    ps.save()
    return new_qty


@login_required
@role_required(["admin"])
def stock_management(request):
    """
    Admin Stock Management:
    - Choose branch
    - Search product
    - Add (restock) or Reduce stock
    - Logs movement in StockEntry
    - Shows recent movements
    """
    branches = _branches_for_sales_dropdown()

    # default branch
    branch_id = (request.GET.get("branch") or request.POST.get("branch") or "").strip()
    selected_branch = branches.filter(pk=branch_id).first() if branch_id else branches.first()

    # Recent movements (filter by branch if selected)
    movements_qs = StockEntry.objects.select_related("product", "branch").order_by("-timestamp")
    if selected_branch:
        movements_qs = movements_qs.filter(branch=selected_branch)

    if request.method == "POST":
        product_id = (request.POST.get("product_id") or "").strip()
        qty = _safe_int(request.POST.get("quantity"), 0)
        action = (request.POST.get("action") or "add").strip().lower()
        note = (request.POST.get("note") or "").strip()

        if not selected_branch:
            messages.error(request, "Please select a branch.")
            return render(request, "sales/stock_management.html", {
                "branches": branches,
                "selected_branch": "",
                "selected_branch_obj": None,
                "movements": movements_qs[:60],
            })

        if not product_id:
            messages.error(request, "Please choose a product.")
        elif qty is None or qty <= 0:
            messages.error(request, "Enter a valid quantity (greater than 0).")
        else:
            product = get_object_or_404(Product, pk=product_id)

            # delta based on action
            if action == "reduce":
                delta = -abs(int(qty))
                action_label = "Reduced"
            else:
                delta = abs(int(qty))
                action_label = "Added"

            try:
                with transaction.atomic():
                    new_qty = _adjust_branch_stock_locked(product, selected_branch, delta)

                    # Log movement
                    StockEntry.objects.create(
                        product=product,
                        branch=selected_branch,
                        change=delta,
                        note=note or f"{action_label} via Stock Management",
                    )

                messages.success(
                    request,
                    f"{action_label} {abs(delta)} to '{product.name}' ({selected_branch.name}). New stock: {new_qty}"
                )
            except ValueError as e:
                messages.error(request, str(e))

    return render(request, "sales/stock_management.html", {
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

    # annotate branch stock
    if branch:
        ps = ProductStock.objects.filter(product=OuterRef("pk"), branch=branch).values("quantity")[:1]
        qs = qs.annotate(
            branch_qty=Coalesce(Subquery(ps, output_field=IntegerField()), Value(0))
        )
    else:
        qs = qs.annotate(branch_qty=Value(0, output_field=IntegerField()))

    if q:
        qs = qs.filter(name__icontains=q)

    results = []
    for p in qs[:30]:
        results.append({
            "id": p.id,
            "name": p.name,
            "price": float(p.price or 0),
            "stock": int(getattr(p, "branch_qty", 0) or 0),
        })

    return JsonResponse(results, safe=False)
