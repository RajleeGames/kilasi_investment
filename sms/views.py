from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from datetime import datetime, time, date
from django.utils import timezone

from sales.models import Invoice, Customer, GenjiSale
from orders.models import CustomerOrder
from topup.models import TopupTransaction, UsedTopupSale
from users.utils import role_required

from transport.models import Booking, Trip

from .forms import (
    ContactForm,
    ContactImportForm,
    QuickSMSForm,
    SalesCustomersSMSForm,
    SMSCampaignForm,
    TransportCustomersSMSForm,
    SenderIDForm,
    SMSTemplateForm,
)
from .models import (
    Contact,
    ContactGroup,
    ContactImport,
    SMSCampaign,
    SMSMessage,
    SenderID,
    SMSTemplate,
)
from .services import (
    import_contacts_from_csv,
    import_contacts_from_text,
    parse_balance_amount,
    send_campaign,
    send_quick_sms,
    sync_campaign_delivery,
)
from .utils import check_balance
from sales.models import Invoice


# =========================================================
# FORM STYLING HELPER
# =========================================================
# ADD THESE IMPORTS AT THE TOP OF views.py IF THEY ARE MISSING:
# from datetime import date, datetime, time
# from django.core.paginator import Paginator
# from django.shortcuts import redirect, render
# from django.contrib import messages
# from django.utils import timezone
#
# Keep your existing imports for:
# login_required, role_required, SalesCustomersSMSForm,
# Contact, ContactGroup, Invoice, CustomerOrder, GenjiSale,
# TopupTransaction, UsedTopupSale, send_quick_sms

from datetime import date, datetime, time

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils import timezone


# =========================================================
# COMMON FORM STYLE HELPER
# =========================================================

def _style_form_fields(form):
    """
    Apply Bootstrap-friendly classes to Django form widgets.
    """
    for _, field in form.fields.items():
        widget = field.widget
        existing = widget.attrs.get("class", "").strip()
        widget_name = widget.__class__.__name__

        if widget_name in ["CheckboxInput", "CheckboxSelectMultiple"]:
            css = "form-check-input"
        elif widget_name in ["Select", "SelectMultiple"]:
            css = "form-select"
        elif widget_name in ["Textarea", "ClearableFileInput", "FileInput"]:
            css = "form-control"
        else:
            css = "form-control"

        widget.attrs["class"] = f"{existing} {css}".strip()

    return form


# =========================================================
# SALES CUSTOMERS SMS
# Normal Sales + Orders + Genji + Top-up
# Deduplicated by phone number
# Personalized SMS using customer names
# =========================================================

SMS_NAME_PLACEHOLDERS = [
    "{name}",
    "{first_name}",
    "{phone}",
    "{{ name }}",
    "{{ first_name }}",
    "{{ phone }}",
    "{{name}}",
    "{{first_name}}",
    "{{phone}}",
    "[name]",
    "[first_name]",
    "[phone]",
    "user.name",
    "user.first_name",
    "user.phone",
    "customer.name",
    "customer.first_name",
    "customer.phone",
]

WEAK_CUSTOMER_NAMES = [
    "",
    "customer",
    "mteja",
    "walk-in",
    "walk in",
    "unknown",
    "n/a",
    "-",
    "—",
]


def _model_has_field(model_class, field_name):
    """
    Safely check if a Django model has a field.
    Prevents crashes if different versions of the project have slightly
    different field names.
    """
    try:
        model_class._meta.get_field(field_name)
        return True
    except Exception:
        return False


def _safe_model(model_name):
    """
    Return model class if it is already imported in this views.py.
    This prevents NameError crashes if one source model is not available.
    """
    return globals().get(model_name)


def _looks_like_phone_name(name, phone):
    """
    Detect contacts where the name was accidentally saved as phone number.
    """
    name_digits = "".join(ch for ch in (name or "") if ch.isdigit())
    phone_digits = "".join(ch for ch in (phone or "") if ch.isdigit())

    return bool(name_digits and phone_digits and name_digits == phone_digits)


def _first_name(full_name):
    """
    Get first name only.
    Example: Miraji Ally -> Miraji
    """
    full_name = (full_name or "").strip()

    if not full_name:
        return "Customer"

    parts = full_name.split()
    return parts[0].strip() if parts else "Customer"


def _message_has_name_placeholder(message):
    """
    If user manually writes a placeholder, we replace it.
    If user does not write a placeholder, we automatically add the name.
    """
    message = (message or "").lower()
    return any(token in message for token in SMS_NAME_PLACEHOLDERS)


def _clean_existing_greeting(message):
    """
    Remove generic greetings before we force the customer name.

    Example:
      Ndugu, asante kwa kununua...
    becomes:
      asante kwa kununua...

    Then final renderer makes it:
      Ndugu Miraji, asante kwa kununua...
    """
    msg = (message or "").strip()

    if not msg:
        return ""

    low = msg.lower().strip()

    starters = [
        "hello,",
        "hello ",
        "hi,",
        "hi ",
        "hey,",
        "hey ",
        "dear,",
        "dear ",
        "dear customer,",
        "dear customer ",
        "habari,",
        "habari ",
        "hujambo,",
        "hujambo ",
        "mambo,",
        "mambo ",
        "salam,",
        "salam ",
        "salamu,",
        "salamu ",
        "ndugu,",
        "ndugu ",
        "ndg,",
        "ndg ",
        "mteja,",
        "mteja ",
        "mteja wetu,",
        "mteja wetu ",
        "bwana,",
        "bwana ",
        "bi,",
        "bi ",
    ]

    for starter in starters:
        if low.startswith(starter):
            msg = msg[len(starter):].strip()
            break

    low2 = msg.lower().strip()

    second_starters = [
        "mteja,",
        "mteja ",
        "customer,",
        "customer ",
        "wetu,",
        "wetu ",
    ]

    for starter in second_starters:
        if low2.startswith(starter):
            msg = msg[len(starter):].strip()
            break

    return msg.strip()


def _get_best_customer_name(row, contact=None):
    """
    Choose the best saved customer name.

    Priority:
      1. SMS contact name, if real
      2. Row/customer name from sales/order/genji/topup
      3. Customer
    """
    row_name = (row.get("name") or "").strip()
    row_phone = (row.get("phone") or "").strip()

    contact_name = ""
    contact_phone = ""

    if contact:
        contact_name = (getattr(contact, "name", "") or "").strip()
        contact_phone = (getattr(contact, "phone", "") or "").strip()

    phone = contact_phone or row_phone
    name = contact_name or row_name or "Customer"

    if row_name:
        if name.strip().lower() in WEAK_CUSTOMER_NAMES or _looks_like_phone_name(name, phone):
            name = row_name

    if not name:
        name = "Customer"

    return name, phone


def _render_customer_message(message_template, row, contact=None):
    """
    Final SMS renderer.

    Case 1: User writes normal message:
      Asante kwa kutembelea Kilasi Investment.

    Customer receives:
      Ndugu Miraji, Asante kwa kutembelea Kilasi Investment.

    Case 2: User writes placeholder:
      Hello {{ name }}, thanks for visiting Kilasi Investment.

    Customer receives:
      Hello Miraji, thanks for visiting Kilasi Investment.
    """
    message = (message_template or "").strip()

    name, phone = _get_best_customer_name(row, contact=contact)
    first_name = _first_name(name)

    replacements = {
        "{name}": name,
        "{first_name}": first_name,
        "{phone}": phone,
        "{{ name }}": name,
        "{{ first_name }}": first_name,
        "{{ phone }}": phone,
        "{{name}}": name,
        "{{first_name}}": first_name,
        "{{phone}}": phone,
        "[name]": name,
        "[first_name]": first_name,
        "[phone]": phone,
        "user.name": name,
        "user.first_name": first_name,
        "user.phone": phone,
        "customer.name": name,
        "customer.first_name": first_name,
        "customer.phone": phone,
    }

    # If user wrote placeholder manually, only replace placeholders.
    if _message_has_name_placeholder(message):
        for key, value in replacements.items():
            message = message.replace(key, str(value or ""))
        return message.strip()

    # If user wrote normal message, force name automatically.
    body = _clean_existing_greeting(message)

    if not body:
        body = "Asante kwa kutembelea Kilasi Investment. Karibu tena."

    return f"Ndugu {first_name}, {body}".strip()


def _sms_to_datetime(value):
    """
    Convert date/datetime/empty values into timezone-aware datetime.
    """
    if not value:
        return timezone.now()

    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value)
        return value

    if isinstance(value, date):
        return timezone.make_aware(datetime.combine(value, time.min))

    return timezone.now()


def _sms_safe_int(value):
    """
    Convert money-like values to int safely for display totals.
    """
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _get_sales_customer_group():
    """
    Create/get SMS group safely.
    Works whether ContactGroup has description field or not.
    """
    group_defaults = {}

    try:
        group_fields = {f.name for f in ContactGroup._meta.fields}
        if "description" in group_fields:
            group_defaults["description"] = "Customers collected automatically from sales invoices, orders, genji and top-up."
    except Exception:
        group_defaults = {}

    try:
        group, _ = ContactGroup.objects.get_or_create(
            name="Sales Customers",
            defaults=group_defaults,
        )
        return group
    except Exception:
        return None


def _add_customer_sms_row(
    rows_by_phone,
    *,
    name,
    phone,
    amount=0,
    source="Sales",
    when=None,
    status="",
    customer=None,
):
    """
    Add/update one customer row by normalized phone.
    This is where duplicate customers are combined.
    """
    try:
        from .services import clean_phone, is_valid_tz_phone
    except Exception:
        return

    raw_phone = (phone or "").strip()
    normalized_phone = clean_phone(raw_phone)

    if not is_valid_tz_phone(normalized_phone):
        return

    name = (name or "").strip() or "Customer"
    when = _sms_to_datetime(when)

    if normalized_phone not in rows_by_phone:
        rows_by_phone[normalized_phone] = {
            "name": name,
            "phone": normalized_phone,
            "raw_phone": raw_phone,
            "invoice_count": 0,
            "total_amount": 0,
            "first_date": when,
            "last_date": when,
            "payments": set(),
            "sources": set(),
            "customer": customer,
        }

    row = rows_by_phone[normalized_phone]

    if row["name"].strip().lower() in WEAK_CUSTOMER_NAMES and name:
        row["name"] = name

    if not row.get("customer") and customer:
        row["customer"] = customer

    row["invoice_count"] += 1
    row["total_amount"] += _sms_safe_int(amount)

    if source:
        row["sources"].add(source)

    if status:
        row["payments"].add(status)

    if when < row["first_date"]:
        row["first_date"] = when

    if when > row["last_date"]:
        row["last_date"] = when


def _sales_customer_rows(start_date, end_date, payment_method="", customer_source="all"):
    """
    Collect unique customers from:
      - Normal Sales
      - Customer Orders
      - Genji
      - Top-up transactions
      - Used Top-up sales

    Deduplicate by normalized phone number.
    """
    rows_by_phone = {}
    customer_source = customer_source or "all"

    # =====================================================
    # 1. NORMAL SALES CUSTOMERS
    # =====================================================
    InvoiceModel = _safe_model("Invoice")

    if InvoiceModel and customer_source in ("all", "sales"):
        sales_qs = (
            InvoiceModel.objects
            .select_related("customer")
            .filter(
                created_at__date__gte=start_date,
                created_at__date__lte=end_date,
                customer__isnull=False,
            )
            .exclude(customer__phone__isnull=True)
            .exclude(customer__phone__exact="")
            .order_by("-created_at")
        )

        if _model_has_field(InvoiceModel, "invoice_state"):
            sales_qs = sales_qs.filter(invoice_state="active")

        if payment_method:
            sales_qs = sales_qs.filter(status=payment_method)

        for inv in sales_qs:
            customer = getattr(inv, "customer", None)
            if not customer:
                continue

            _add_customer_sms_row(
                rows_by_phone,
                name=getattr(customer, "name", "") or "",
                phone=getattr(customer, "phone", "") or "",
                amount=getattr(inv, "total_amount", 0) or 0,
                source="Sales",
                when=getattr(inv, "created_at", None),
                status=(getattr(inv, "status", "") or "").upper(),
                customer=customer,
            )

    # =====================================================
    # 2. CUSTOMER ORDERS
    # =====================================================
    CustomerOrderModel = _safe_model("CustomerOrder")

    if CustomerOrderModel and customer_source in ("all", "orders"):
        orders_qs = (
            CustomerOrderModel.objects
            .filter(
                created_at__date__gte=start_date,
                created_at__date__lte=end_date,
            )
            .exclude(customer_phone__isnull=True)
            .exclude(customer_phone__exact="")
            .order_by("-created_at")
        )

        # Only select_related fields that exist in your model.
        select_fields = []
        for field_name in ["branch", "created_by", "product"]:
            if _model_has_field(CustomerOrderModel, field_name):
                select_fields.append(field_name)
        if select_fields:
            orders_qs = orders_qs.select_related(*select_fields)

        for order in orders_qs:
            order_status = (getattr(order, "order_status", "") or "").upper()
            payment_status = (getattr(order, "payment_status", "") or "").upper()

            status_text = "ORDER"
            if order_status:
                status_text = f"ORDER-{order_status}"
            if payment_status:
                status_text = f"{status_text}-{payment_status}"

            _add_customer_sms_row(
                rows_by_phone,
                name=getattr(order, "customer_name", "") or "",
                phone=getattr(order, "customer_phone", "") or "",
                amount=getattr(order, "total_amount", 0) or 0,
                source="Orders",
                when=getattr(order, "created_at", None),
                status=status_text,
                customer=None,
            )

    # =====================================================
    # 3. GENJI CUSTOMERS
    # =====================================================
    GenjiSaleModel = _safe_model("GenjiSale")

    if GenjiSaleModel and customer_source in ("all", "genji"):
        genji_qs = (
            GenjiSaleModel.objects
            .filter(
                sell_date__gte=start_date,
                sell_date__lte=end_date,
            )
            .exclude(customer_phone__isnull=True)
            .exclude(customer_phone__exact="")
            .order_by("-sell_date", "-id")
        )

        select_fields = []
        for field_name in ["branch", "created_by"]:
            if _model_has_field(GenjiSaleModel, field_name):
                select_fields.append(field_name)
        if select_fields:
            genji_qs = genji_qs.select_related(*select_fields)

        for genji in genji_qs:
            try:
                amount = genji.total_selling
            except Exception:
                amount = getattr(genji, "selling_price", 0) or 0

            _add_customer_sms_row(
                rows_by_phone,
                name=getattr(genji, "customer_name", "") or "",
                phone=getattr(genji, "customer_phone", "") or "",
                amount=amount,
                source="Genji",
                when=getattr(genji, "sell_date", None),
                status="GENJI",
                customer=None,
            )

    # =====================================================
    # 4. TOP-UP CUSTOMERS
    # =====================================================
    TopupTransactionModel = _safe_model("TopupTransaction")

    if TopupTransactionModel and customer_source in ("all", "topup"):
        topup_qs = (
            TopupTransactionModel.objects
            .filter(
                created_at__date__gte=start_date,
                created_at__date__lte=end_date,
            )
            .exclude(customer__isnull=True)
            .exclude(customer__phone__isnull=True)
            .exclude(customer__phone__exact="")
            .order_by("-created_at")
        )

        select_fields = []
        for field_name in ["customer", "branch", "new_product", "created_by"]:
            if _model_has_field(TopupTransactionModel, field_name):
                select_fields.append(field_name)
        if select_fields:
            topup_qs = topup_qs.select_related(*select_fields)

        if _model_has_field(TopupTransactionModel, "status"):
            topup_qs = topup_qs.filter(status="completed")

        for tx in topup_qs:
            customer = getattr(tx, "customer", None)
            if not customer:
                continue

            _add_customer_sms_row(
                rows_by_phone,
                name=getattr(customer, "name", "") or "",
                phone=getattr(customer, "phone", "") or "",
                amount=getattr(tx, "addon_amount", 0) or 0,
                source="Top-up",
                when=getattr(tx, "created_at", None),
                status="TOP-UP",
                customer=customer,
            )

    # =====================================================
    # 5. USED TOP-UP SALES CUSTOMERS
    # =====================================================
    UsedTopupSaleModel = _safe_model("UsedTopupSale")

    if UsedTopupSaleModel and customer_source in ("all", "topup"):
        used_sales_qs = (
            UsedTopupSaleModel.objects
            .filter(
                created_at__date__gte=start_date,
                created_at__date__lte=end_date,
            )
            .exclude(customer__isnull=True)
            .exclude(customer__phone__isnull=True)
            .exclude(customer__phone__exact="")
            .order_by("-created_at")
        )

        select_fields = []
        for field_name in ["customer", "branch", "used_stock", "sold_by"]:
            if _model_has_field(UsedTopupSaleModel, field_name):
                select_fields.append(field_name)
        if select_fields:
            used_sales_qs = used_sales_qs.select_related(*select_fields)

        for sale in used_sales_qs:
            customer = getattr(sale, "customer", None)
            if not customer:
                continue

            _add_customer_sms_row(
                rows_by_phone,
                name=getattr(customer, "name", "") or "",
                phone=getattr(customer, "phone", "") or "",
                amount=getattr(sale, "sold_price", 0) or 0,
                source="Used Top-up",
                when=getattr(sale, "created_at", None),
                status="USED-SALE",
                customer=customer,
            )

    rows = list(rows_by_phone.values())

    for row in rows:
        row["payments_text"] = ", ".join(sorted(row["payments"])) if row["payments"] else "-"
        row["source_text"] = ", ".join(sorted(row["sources"])) if row["sources"] else "-"

    rows.sort(key=lambda x: x["last_date"], reverse=True)

    return rows


def _sync_sales_sms_row_to_contact(row, created_by=None):
    """
    Sync any row from sales/orders/genji/topup into SMS Contact.
    Works even when row does not have a sales.Customer object.
    """
    try:
        from .services import clean_phone, is_valid_tz_phone
    except Exception:
        return None

    raw_name = (row.get("name") or "").strip()
    raw_phone = (row.get("phone") or "").strip()

    if not raw_phone:
        return None

    phone = clean_phone(raw_phone)

    if not is_valid_tz_phone(phone):
        return None

    name = raw_name or "Customer"
    group = _get_sales_customer_group()

    contact_fields = {f.name for f in Contact._meta.fields}
    contact = Contact.objects.filter(phone=phone).first()

    if contact:
        changed = False

        old_name = (getattr(contact, "name", "") or "").strip()
        old_name_lower = old_name.lower()

        weak_names = WEAK_CUSTOMER_NAMES + [phone.lower()]

        if "name" in contact_fields:
            if name and (
                old_name_lower in weak_names
                or _looks_like_phone_name(old_name, phone)
            ):
                contact.name = name
                changed = True

        if "is_active" in contact_fields and getattr(contact, "is_active", True) is False:
            contact.is_active = True
            changed = True

        if changed:
            contact.save()

        if group:
            try:
                contact.groups.add(group)
            except Exception:
                pass

        return contact

    kwargs = {}

    if "name" in contact_fields:
        kwargs["name"] = name

    if "phone" in contact_fields:
        kwargs["phone"] = phone

    if "is_active" in contact_fields:
        kwargs["is_active"] = True

    if "notes" in contact_fields:
        kwargs["notes"] = "Auto-saved from Sales Customers SMS sources."

    if "created_by" in contact_fields and created_by:
        kwargs["created_by"] = created_by

    contact = Contact.objects.create(**kwargs)

    if group:
        try:
            contact.groups.add(group)
        except Exception:
            pass

    return contact


def _sync_sales_customer_to_sms_contact(customer, created_by=None):
    """
    Backward-compatible helper.
    Keep this if other sales views already call it after normal invoice sale.
    It now uses the new combined row sync logic, so no duplicate code is needed.
    """
    if not customer:
        return None

    row = {
        "name": (getattr(customer, "name", "") or "").strip(),
        "phone": (getattr(customer, "phone", "") or "").strip(),
    }

    return _sync_sales_sms_row_to_contact(row, created_by=created_by)


@login_required
@role_required(["admin", "cashier"])
def sales_customers_sms(request):
    """
    SMS Center page:
    - Normal sales customers
    - Customer orders customers
    - Genji customers
    - Top-up customers
    - Filter by customer source
    - Pagination: 50 or 100 per page
    - Send personalized SMS using saved names
    """
    if request.method == "POST":
        form = SalesCustomersSMSForm(request.POST)
        form = _style_form_fields(form)

        if form.is_valid():
            start_date = form.cleaned_data["start_date"]
            end_date = form.cleaned_data["end_date"]
            payment_method = form.cleaned_data.get("payment_method") or ""
            customer_source = form.cleaned_data.get("customer_source") or "all"
            sender = form.cleaned_data.get("sender_id")
            message_template = (form.cleaned_data.get("message") or "").strip()

            selected_phones = request.POST.getlist("selected_phones")
            per_page = request.POST.get("per_page") or request.GET.get("per_page") or "50"

            if per_page not in ["50", "100"]:
                per_page = "50"

            redirect_url = (
                f"{request.path}"
                f"?start_date={start_date}"
                f"&end_date={end_date}"
                f"&payment_method={payment_method}"
                f"&customer_source={customer_source}"
                f"&per_page={per_page}"
            )

            if not message_template:
                messages.error(request, "Type the SMS message first.")
                return redirect(redirect_url)

            if not selected_phones:
                messages.error(request, "Select at least one customer.")
                return redirect(redirect_url)

            rows = _sales_customer_rows(
                start_date=start_date,
                end_date=end_date,
                payment_method=payment_method,
                customer_source=customer_source,
            )

            selected_set = set(selected_phones)

            sent_count = 0
            failed_count = 0
            skipped_count = 0

            for row in rows:
                if row["phone"] not in selected_set:
                    continue

                contact = _sync_sales_sms_row_to_contact(
                    row,
                    created_by=request.user,
                )

                if not contact:
                    skipped_count += 1
                    continue

                personalized_message = _render_customer_message(
                    message_template,
                    row,
                    contact=contact,
                )

                if not personalized_message:
                    skipped_count += 1
                    continue

                result = send_quick_sms(
                    message=personalized_message,
                    contacts=[contact],
                    manual_numbers=[],
                    sender=sender,
                    created_by=request.user,
                )

                if result.get("ok"):
                    sent_count += int(result.get("count", 0) or 0)
                else:
                    failed_count += 1

            if sent_count > 0:
                messages.success(
                    request,
                    f"Personalized SMS sent to {sent_count} customer(s). "
                    f"Failed: {failed_count}. Skipped invalid: {skipped_count}."
                )
            else:
                messages.error(
                    request,
                    f"No SMS was sent. Failed: {failed_count}. Skipped invalid: {skipped_count}."
                )

            return redirect(redirect_url)

    else:
        form = SalesCustomersSMSForm(request.GET or None)
        form = _style_form_fields(form)

    if form.is_valid():
        start_date = form.cleaned_data["start_date"]
        end_date = form.cleaned_data["end_date"]
        payment_method = form.cleaned_data.get("payment_method") or ""
        customer_source = form.cleaned_data.get("customer_source") or "all"
    else:
        today = timezone.localdate()
        start_date = today
        end_date = today
        payment_method = ""
        customer_source = "all"

    all_rows = _sales_customer_rows(
        start_date=start_date,
        end_date=end_date,
        payment_method=payment_method,
        customer_source=customer_source,
    )

    total_invoice_count = sum(int(r["invoice_count"] or 0) for r in all_rows)
    total_amount = sum(int(r["total_amount"] or 0) for r in all_rows)

    per_page = request.GET.get("per_page") or "50"
    if per_page not in ["50", "100"]:
        per_page = "50"

    paginator = Paginator(all_rows, int(per_page))
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    return render(request, "sms/sales_customers_sms.html", {
        "form": form,
        "rows": page_obj.object_list,
        "page_obj": page_obj,
        "per_page": per_page,
        "start_date": start_date,
        "end_date": end_date,
        "payment_method": payment_method,
        "customer_source": customer_source,
        "total_customers": len(all_rows),
        "total_invoice_count": total_invoice_count,
        "total_amount": total_amount,
    })

# =========================================================
# DASHBOARD
# =========================================================
@login_required
@role_required(["admin", "cashier"])
def sms_dashboard(request):
    balance_resp = check_balance()
    balance_text = parse_balance_amount(balance_resp)

    today = timezone.localdate()

    total_contacts = Contact.objects.count()
    active_contacts = Contact.objects.filter(is_active=True).count()
    total_groups = ContactGroup.objects.count()
    total_templates = SMSTemplate.objects.count()
    total_campaigns = SMSCampaign.objects.count()

    sent_today = SMSMessage.objects.filter(created_at__date=today, status="SENT").count()
    delivered_today = SMSMessage.objects.filter(created_at__date=today, status="DELIVERED").count()
    failed_today = SMSMessage.objects.filter(
        created_at__date=today,
        status__in=["FAILED", "UNDELIVERED"]
    ).count()

    pending_count = SMSMessage.objects.filter(status="PENDING").count()
    sent_count = SMSMessage.objects.filter(status="SENT").count()
    delivered_count = SMSMessage.objects.filter(status="DELIVERED").count()
    failed_count = SMSMessage.objects.filter(status__in=["FAILED", "UNDELIVERED"]).count()

    recent_messages = SMSMessage.objects.select_related(
        "contact", "campaign", "sender_id"
    ).order_by("-created_at")[:10]

    recent_campaigns = SMSCampaign.objects.select_related(
        "template", "sender_id"
    ).order_by("-created_at")[:6]

    recent_imports = ContactImport.objects.order_by("-created_at")[:6]

    context = {
        "balance_resp": balance_resp,
        "balance_text": balance_text,
        "total_contacts": total_contacts,
        "active_contacts": active_contacts,
        "total_groups": total_groups,
        "total_templates": total_templates,
        "total_campaigns": total_campaigns,
        "sent_today": sent_today,
        "delivered_today": delivered_today,
        "failed_today": failed_today,
        "pending_count": pending_count,
        "sent_count": sent_count,
        "delivered_count": delivered_count,
        "failed_count": failed_count,
        "recent_messages": recent_messages,
        "recent_campaigns": recent_campaigns,
        "recent_imports": recent_imports,
    }
    return render(request, "sms/dashboard.html", context)


# =========================================================
# CONTACTS
# =========================================================
@login_required
@role_required(["admin", "cashier"])
def contact_list(request):
    q = (request.GET.get("q") or "").strip()
    group_id = (request.GET.get("group") or "").strip()
    status = (request.GET.get("status") or "").strip()

    qs = Contact.objects.prefetch_related("groups").all()

    if q:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(phone__icontains=q) |
            Q(email__icontains=q)
        )

    if group_id:
        qs = qs.filter(groups__id=group_id)

    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)

    qs = qs.distinct().order_by("name", "phone")

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "groups": ContactGroup.objects.all().order_by("name"),
        "q": q,
        "group_id": group_id,
        "status": status,
    }
    return render(request, "sms/contact_list.html", context)


@login_required
@role_required(["admin", "cashier"])
def contact_create(request):
    if request.method == "POST":
        form = ContactForm(request.POST)
        form = _style_form_fields(form)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()
            form.save_m2m()
            messages.success(request, "Contact added successfully.")
            return redirect("sms:contact_list")
    else:
        form = ContactForm()
        form = _style_form_fields(form)

    return render(request, "sms/contact_form.html", {
        "form": form,
        "page_title": "Add Contact",
    })


@login_required
@role_required(["admin", "cashier"])
def contact_update(request, pk):
    contact = get_object_or_404(Contact, pk=pk)

    if request.method == "POST":
        form = ContactForm(request.POST, instance=contact)
        form = _style_form_fields(form)
        if form.is_valid():
            form.save()
            messages.success(request, "Contact updated successfully.")
            return redirect("sms:contact_list")
    else:
        form = ContactForm(instance=contact)
        form = _style_form_fields(form)

    return render(request, "sms/contact_form.html", {
        "form": form,
        "contact": contact,
        "page_title": "Edit Contact",
    })


@login_required
@role_required(["admin", "cashier"])
def contact_delete(request, pk):
    contact = get_object_or_404(Contact, pk=pk)

    if request.method == "POST":
        contact.delete()
        messages.success(request, "Contact deleted successfully.")
        return redirect("sms:contact_list")

    return render(request, "sms/contact_form.html", {
        "contact": contact,
        "delete_mode": True,
        "page_title": "Delete Contact",
    })


# =========================================================
# IMPORT CONTACTS
# =========================================================
@login_required
@role_required(["admin", "cashier"])
def contact_import(request):
    if request.method == "POST":
        form = ContactImportForm(request.POST, request.FILES)
        form = _style_form_fields(form)
        if form.is_valid():
            csv_file = form.cleaned_data.get("csv_file")
            pasted_contacts = form.cleaned_data.get("pasted_contacts")
            group = form.cleaned_data.get("group")
            overwrite_names = form.cleaned_data.get("overwrite_names")

            result = None

            if csv_file:
                result = import_contacts_from_csv(
                    file_obj=csv_file,
                    group=group,
                    overwrite_names=overwrite_names,
                    created_by=request.user,
                )
            elif pasted_contacts:
                result = import_contacts_from_text(
                    text=pasted_contacts,
                    group=group,
                    overwrite_names=overwrite_names,
                    created_by=request.user,
                )
            else:
                messages.error(request, "Upload a CSV file or paste contacts.")
                return redirect("sms:contact_import")

            messages.success(
                request,
                f"Import finished. Imported: {result['imported']}, Skipped: {result['skipped']}."
            )
            return redirect("sms:contact_list")
    else:
        form = ContactImportForm()
        form = _style_form_fields(form)

    return render(request, "sms/contact_import.html", {
        "form": form,
    })


# =========================================================
# QUICK SEND
# =========================================================
@login_required
@role_required(["admin", "cashier"])
def send_sms_view(request):
    if request.method == "POST":
        form = QuickSMSForm(request.POST)
        form = _style_form_fields(form)
        if form.is_valid():
            sender = form.cleaned_data.get("sender_id")
            message_text = form.cleaned_data.get("message")
            contacts = form.cleaned_data.get("contacts")
            manual_numbers_text = (form.cleaned_data.get("manual_numbers") or "").strip()

            manual_numbers = [line.strip() for line in manual_numbers_text.splitlines() if line.strip()]

            result = send_quick_sms(
                message=message_text,
                contacts=contacts,
                manual_numbers=manual_numbers,
                sender=sender,
                created_by=request.user,
            )

            if result.get("ok"):
                messages.success(
                    request,
                    f"SMS sent successfully to {result.get('count', 0)} recipient(s)."
                )
            else:
                messages.error(request, result.get("error") or "SMS sending failed.")

            return redirect("sms:send_sms")
    else:
        form = QuickSMSForm()
        form = _style_form_fields(form)

    return render(request, "sms/send_sms.html", {
        "form": form,
    })


# =========================================================
# TEMPLATES
# =========================================================
@login_required
@role_required(["admin", "cashier"])
def template_list(request):
    q = (request.GET.get("q") or "").strip()
    qs = SMSTemplate.objects.all().order_by("title")

    if q:
        qs = qs.filter(
            Q(title__icontains=q) |
            Q(message__icontains=q)
        )

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "sms/template_list.html", {
        "page_obj": page_obj,
        "q": q,
    })


@login_required
@role_required(["admin", "cashier"])
def template_create(request):
    if request.method == "POST":
        form = SMSTemplateForm(request.POST)
        form = _style_form_fields(form)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()
            messages.success(request, "Template created successfully.")
            return redirect("sms:template_list")
    else:
        form = SMSTemplateForm()
        form = _style_form_fields(form)

    return render(request, "sms/contact_form.html", {
        "form": form,
        "page_title": "Add SMS Template",
        "is_template_form": True,
    })


@login_required
@role_required(["admin", "cashier"])
def template_update(request, pk):
    obj = get_object_or_404(SMSTemplate, pk=pk)

    if request.method == "POST":
        form = SMSTemplateForm(request.POST, instance=obj)
        form = _style_form_fields(form)
        if form.is_valid():
            form.save()
            messages.success(request, "Template updated successfully.")
            return redirect("sms:template_list")
    else:
        form = SMSTemplateForm(instance=obj)
        form = _style_form_fields(form)

    return render(request, "sms/contact_form.html", {
        "form": form,
        "page_title": "Edit SMS Template",
        "is_template_form": True,
    })


# =========================================================
# SENDER IDS
# =========================================================
@login_required
@role_required(["admin"])
def sender_list(request):
    qs = SenderID.objects.all().order_by("-is_default", "name")
    return render(request, "sms/contact_list.html", {
        "sender_mode": True,
        "senders": qs,
    })


@login_required
@role_required(["admin"])
def sender_create(request):
    if request.method == "POST":
        form = SenderIDForm(request.POST)
        form = _style_form_fields(form)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()

            if obj.is_default:
                SenderID.objects.exclude(pk=obj.pk).update(is_default=False)

            messages.success(request, "Sender ID saved successfully.")
            return redirect("sms:sender_list")
    else:
        form = SenderIDForm()
        form = _style_form_fields(form)

    return render(request, "sms/contact_form.html", {
        "form": form,
        "page_title": "Add Sender ID",
        "is_sender_form": True,
    })


@login_required
@role_required(["admin"])
def sender_update(request, pk):
    obj = get_object_or_404(SenderID, pk=pk)

    if request.method == "POST":
        form = SenderIDForm(request.POST, instance=obj)
        form = _style_form_fields(form)
        if form.is_valid():
            obj = form.save()

            if obj.is_default:
                SenderID.objects.exclude(pk=obj.pk).update(is_default=False)

            messages.success(request, "Sender ID updated successfully.")
            return redirect("sms:sender_list")
    else:
        form = SenderIDForm(instance=obj)
        form = _style_form_fields(form)

    return render(request, "sms/contact_form.html", {
        "form": form,
        "page_title": "Edit Sender ID",
        "is_sender_form": True,
    })


# =========================================================
# CAMPAIGNS
# =========================================================
@login_required
@role_required(["admin", "cashier"])
def campaign_list(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()

    qs = SMSCampaign.objects.select_related("template", "sender_id").prefetch_related("groups", "contacts")

    if q:
        qs = qs.filter(
            Q(title__icontains=q) |
            Q(message__icontains=q)
        )

    if status:
        qs = qs.filter(status=status)

    qs = qs.order_by("-created_at")

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "sms/campaign_list.html", {
        "page_obj": page_obj,
        "q": q,
        "status": status,
        "status_choices": SMSCampaign.STATUS_CHOICES,
    })


@login_required
@role_required(["admin", "cashier"])
def campaign_create(request):
    if request.method == "POST":
        form = SMSCampaignForm(request.POST)
        form = _style_form_fields(form)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()
            form.save_m2m()
            messages.success(request, "Campaign created successfully.")
            return redirect("sms:campaign_list")
    else:
        form = SMSCampaignForm()
        form = _style_form_fields(form)

    return render(request, "sms/contact_form.html", {
        "form": form,
        "page_title": "Create Campaign",
        "is_campaign_form": True,
    })


@login_required
@role_required(["admin", "cashier"])
def campaign_update(request, pk):
    obj = get_object_or_404(SMSCampaign, pk=pk)

    if request.method == "POST":
        form = SMSCampaignForm(request.POST, instance=obj)
        form = _style_form_fields(form)
        if form.is_valid():
            form.save()
            form.save_m2m()
            messages.success(request, "Campaign updated successfully.")
            return redirect("sms:campaign_list")
    else:
        form = SMSCampaignForm(instance=obj)
        form = _style_form_fields(form)

    return render(request, "sms/contact_form.html", {
        "form": form,
        "page_title": "Edit Campaign",
        "is_campaign_form": True,
    })


@login_required
@role_required(["admin", "cashier"])
def campaign_send_view(request, pk):
    campaign = get_object_or_404(SMSCampaign, pk=pk)

    result = send_campaign(campaign, created_by=request.user)

    if result.get("ok"):
        messages.success(
            request,
            f"Campaign sent successfully to {result.get('count', 0)} recipient(s)."
        )
    else:
        messages.error(request, result.get("error") or "Campaign sending failed.")

    return redirect("sms:campaign_list")


@login_required
@role_required(["admin", "cashier"])
def campaign_sync_delivery_view(request, pk):
    campaign = get_object_or_404(SMSCampaign, pk=pk)
    result = sync_campaign_delivery(campaign)

    if result.get("ok"):
        messages.success(
            request,
            f"Delivery sync completed. Checked {result.get('checked', 0)} message(s)."
        )
    else:
        messages.error(request, "Delivery sync failed.")

    return redirect("sms:campaign_list")


# =========================================================
# SMS LOGS
# =========================================================
@login_required
@role_required(["admin", "cashier"])
def sms_logs(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    campaign_id = (request.GET.get("campaign") or "").strip()

    qs = SMSMessage.objects.select_related(
        "contact", "campaign", "sender_id"
    ).all()

    if q:
        qs = qs.filter(
            Q(dest_addr__icontains=q) |
            Q(message__icontains=q) |
            Q(contact__name__icontains=q) |
            Q(request_id__icontains=q)
        )

    if status:
        qs = qs.filter(status=status)

    if campaign_id:
        qs = qs.filter(campaign_id=campaign_id)

    qs = qs.order_by("-created_at")

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "q": q,
        "status": status,
        "campaign_id": campaign_id,
        "campaigns": SMSCampaign.objects.order_by("-created_at")[:100],
        "status_choices": SMSMessage.STATUS_CHOICES,
    }
    return render(request, "sms/sms_logs.html", context)


from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from users.utils import role_required

from .models import Contact


@login_required
@role_required(["admin"])
def contact_delete_all(request):
    total_contacts = Contact.objects.count()

    if request.method == "POST":
        deleted_count = total_contacts
        Contact.objects.all().delete()
        messages.success(request, f"All contacts deleted successfully. Removed {deleted_count} contact(s).")
        return redirect("sms:contact_list")

    return render(request, "sms/contact_delete_all.html", {
        "total_contacts": total_contacts,
    })

# =========================================================
# TRANSPORT CUSTOMERS SMS
# Separate from Sales Customers SMS
# =========================================================

def _transport_digits(value):
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _transport_normalize_phone(phone):
    phone = (phone or "").strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    if phone.startswith("+"):
        phone = phone[1:]

    if phone.startswith("0"):
        phone = "255" + phone[1:]

    return phone


def _transport_valid_phone(phone):
    phone = _transport_normalize_phone(phone)
    return bool(phone.startswith("255") and len(phone) >= 12)


def _transport_first_name(full_name):
    full_name = (full_name or "").strip()

    if not full_name:
        return "Mteja"

    parts = full_name.split()
    return parts[0].strip() if parts else "Mteja"


def _transport_looks_like_phone_name(name, phone):
    name_digits = _transport_digits(name)
    phone_digits = _transport_digits(phone)
    return bool(name_digits and phone_digits and name_digits == phone_digits)


def _transport_get_role_branch(request):
    profile = getattr(request.user, "profile", None)
    role = (getattr(profile, "role", "") or "").lower()
    branch = getattr(profile, "branch", None)
    return role, branch


def _transport_message_has_placeholder(message):
    message = (message or "").lower()

    tokens = [
        "{name}",
        "{first_name}",
        "{phone}",
        "{pickup}",
        "{destination}",
        "{date}",
        "{source}",

        "{{ name }}",
        "{{ first_name }}",
        "{{ phone }}",
        "{{ pickup }}",
        "{{ destination }}",
        "{{ date }}",
        "{{ source }}",

        "{{name}}",
        "{{first_name}}",
        "{{phone}}",
        "{{pickup}}",
        "{{destination}}",
        "{{date}}",
        "{{source}}",

        "[name]",
        "[first_name]",
        "[phone]",
        "[pickup]",
        "[destination]",
        "[date]",
        "[source]",

        "user.name",
        "user.first_name",
        "customer.name",
        "customer.first_name",
    ]

    return any(token in message for token in tokens)


def _transport_clean_existing_greeting(message):
    """
    Remove only generic greetings so we do not duplicate:
      Ndugu, asante... => asante...
      Hello, thanks... => thanks...

    But if message starts with:
      Asante kwa...
    we keep it and send:
      Ndugu Miraji, Asante kwa...
    """
    msg = (message or "").strip()

    if not msg:
        return ""

    low = msg.lower().strip()

    starters = [
        "hello,",
        "hello ",
        "hi,",
        "hi ",
        "hey,",
        "hey ",
        "dear,",
        "dear ",
        "dear customer,",
        "dear customer ",

        "habari,",
        "habari ",
        "hujambo,",
        "hujambo ",
        "mambo,",
        "mambo ",
        "salam,",
        "salam ",
        "salamu,",
        "salamu ",

        "ndugu,",
        "ndugu ",
        "ndg,",
        "ndg ",
        "mteja,",
        "mteja ",
        "mteja wetu,",
        "mteja wetu ",

        "bwana,",
        "bwana ",
        "bi,",
        "bi ",
    ]

    for starter in starters:
        if low.startswith(starter):
            msg = msg[len(starter):].strip()
            break

    low2 = msg.lower().strip()

    second_starters = [
        "mteja,",
        "mteja ",
        "customer,",
        "customer ",
        "wetu,",
        "wetu ",
    ]

    for starter in second_starters:
        if low2.startswith(starter):
            msg = msg[len(starter):].strip()
            break

    return msg.strip()


def _transport_best_name(row, contact=None):
    row_name = (row.get("name") or "").strip()
    row_phone = (row.get("phone") or "").strip()

    contact_name = ""
    contact_phone = ""

    if contact:
        contact_name = (getattr(contact, "name", "") or "").strip()
        contact_phone = (getattr(contact, "phone", "") or "").strip()

    phone = contact_phone or row_phone
    name = contact_name or row_name or "Mteja"

    weak_names = ["", "customer", "mteja", "transport customer", "walk-in", "unknown", "n/a", "-", "—"]

    if row_name:
        if name.strip().lower() in weak_names or _transport_looks_like_phone_name(name, phone):
            name = row_name

    if not name:
        name = "Mteja"

    return name, phone


def _render_transport_customer_message(message_template, row, contact=None):
    """
    User can type:
      Asante kwa kutumia huduma ya usafiri ya Kilasi Investment.

    Customer receives:
      Ndugu Miraji, Asante kwa kutumia huduma ya usafiri ya Kilasi Investment.
    """
    message = (message_template or "").strip()

    name, phone = _transport_best_name(row, contact=contact)
    first_name = _transport_first_name(name)

    pickup = (row.get("pickup") or "").strip()
    destination = (row.get("destination") or "").strip()
    date_text = (row.get("last_date_text") or "").strip()
    source_text = (row.get("source_text") or "").strip()

    replacements = {
        "{name}": name,
        "{first_name}": first_name,
        "{phone}": phone,
        "{pickup}": pickup,
        "{destination}": destination,
        "{date}": date_text,
        "{source}": source_text,

        "{{ name }}": name,
        "{{ first_name }}": first_name,
        "{{ phone }}": phone,
        "{{ pickup }}": pickup,
        "{{ destination }}": destination,
        "{{ date }}": date_text,
        "{{ source }}": source_text,

        "{{name}}": name,
        "{{first_name}}": first_name,
        "{{phone}}": phone,
        "{{pickup}}": pickup,
        "{{destination}}": destination,
        "{{date}}": date_text,
        "{{source}}": source_text,

        "[name]": name,
        "[first_name]": first_name,
        "[phone]": phone,
        "[pickup]": pickup,
        "[destination]": destination,
        "[date]": date_text,
        "[source]": source_text,

        "user.name": name,
        "user.first_name": first_name,
        "customer.name": name,
        "customer.first_name": first_name,
    }

    if _transport_message_has_placeholder(message):
        for key, value in replacements.items():
            message = message.replace(key, value)
        return message.strip()

    body = _transport_clean_existing_greeting(message)

    if not body:
        body = "Asante kwa kutumia huduma ya usafiri ya Kilasi Investment. Karibu tena."

    return f"Ndugu {first_name}, {body}".strip()


def _sync_transport_customer_to_sms_contact(row, created_by=None):
    """
    Save/update transport customer into SMS Contact.
    Separate group = Transport Customers.
    It does not mix with Sales Customers page.
    """
    raw_name = (row.get("name") or "").strip()
    raw_phone = (row.get("phone") or "").strip()

    if not raw_phone:
        return None

    phone = _transport_normalize_phone(raw_phone)

    if not _transport_valid_phone(phone):
        return None

    name = raw_name or "Transport Customer"

    group, _ = ContactGroup.objects.get_or_create(
        name="Transport Customers",
        defaults={
            "description": "Customers collected automatically from transport bookings and trips."
        }
    )

    contact = Contact.objects.filter(phone=phone).first()

    if contact:
        changed = False

        old_name = (contact.name or "").strip()
        old_name_lower = old_name.lower()
        weak_names = ["", "customer", "mteja", "transport customer", "walk-in", "unknown", "n/a", "-", "—"]

        should_update_name = (
            name
            and (
                old_name_lower in weak_names
                or _transport_looks_like_phone_name(old_name, phone)
                or old_name == phone
            )
        )

        if should_update_name and old_name != name:
            contact.name = name
            changed = True

        if not contact.is_active:
            contact.is_active = True
            changed = True

        if changed:
            contact.save(update_fields=["name", "is_active", "updated_at"])

        contact.groups.add(group)
        return contact

    contact = Contact.objects.create(
        name=name,
        phone=phone,
        is_active=True,
        created_by=created_by,
        notes="Auto-saved from transport bookings/trips.",
    )

    contact.groups.add(group)
    return contact


def _transport_customer_name_map(request):
    """
    Get names from bookings by phone.
    Useful because Trip model has customer_phone only.
    """
    role, branch = _transport_get_role_branch(request)

    qs = Booking.objects.select_related("branch").exclude(customer_phone__isnull=True).exclude(customer_phone__exact="")

    if role == "transport" and branch:
        qs = qs.filter(branch=branch)

    name_map = {}

    for b in qs.order_by("-booking_datetime", "-id"):
        phone = _transport_normalize_phone(getattr(b, "customer_phone", "") or "")
        name = (getattr(b, "customer_name", "") or "").strip()

        if phone and name and phone not in name_map:
            name_map[phone] = name

    return name_map


def _add_transport_row(rows_by_phone, phone, name, amount=0, source="Transport", pickup="", destination="", when=None, status=""):
    phone = _transport_normalize_phone(phone)

    if not _transport_valid_phone(phone):
        return

    name = (name or "").strip() or "Transport Customer"

    if phone not in rows_by_phone:
        rows_by_phone[phone] = {
            "name": name,
            "phone": phone,
            "booking_count": 0,
            "trip_count": 0,
            "total_amount": 0,
            "first_date": when,
            "last_date": when,
            "last_date_text": "",
            "sources": set(),
            "statuses": set(),
            "pickup": pickup or "",
            "destination": destination or "",
        }

    row = rows_by_phone[phone]

    weak_names = ["", "customer", "mteja", "transport customer", "unknown", "n/a", "-", "—"]

    if row["name"].strip().lower() in weak_names and name:
        row["name"] = name

    if source:
        row["sources"].add(source)

    if status:
        row["statuses"].add(status)

    if source == "Booking":
        row["booking_count"] += 1
    elif source == "Trip":
        row["trip_count"] += 1

    try:
        row["total_amount"] += int(float(amount or 0))
    except Exception:
        row["total_amount"] += 0

    if pickup and not row["pickup"]:
        row["pickup"] = pickup

    if destination and not row["destination"]:
        row["destination"] = destination

    if when:
        if not row["first_date"] or when < row["first_date"]:
            row["first_date"] = when

        if not row["last_date"] or when > row["last_date"]:
            row["last_date"] = when


def _transport_customer_rows(request, start_date, end_date, source="all", status=""):
    """
    Collect unique transport customers from:
      - Booking.customer_name + Booking.customer_phone
      - Trip.customer_phone

    Separate from sales customers.
    """
    role, branch = _transport_get_role_branch(request)
    rows_by_phone = {}
    name_map = _transport_customer_name_map(request)

    source = source or "all"

    if source in ("all", "bookings"):
        bookings = (
            Booking.objects
            .select_related("branch", "created_by")
            .filter(
                booking_datetime__date__gte=start_date,
                booking_datetime__date__lte=end_date,
            )
            .exclude(customer_phone__isnull=True)
            .exclude(customer_phone__exact="")
            .order_by("-booking_datetime", "-id")
        )

        if role == "transport" and branch:
            bookings = bookings.filter(branch=branch)

        if status:
            bookings = bookings.filter(status=status)

        for b in bookings:
            _add_transport_row(
                rows_by_phone=rows_by_phone,
                phone=getattr(b, "customer_phone", "") or "",
                name=getattr(b, "customer_name", "") or "",
                amount=0,
                source="Booking",
                pickup=getattr(b, "pickup_area", "") or "",
                destination=getattr(b, "destination", "") or "",
                when=getattr(b, "booking_datetime", None),
                status=(getattr(b, "status", "") or "").upper(),
            )

    if source in ("all", "trips"):
        trips = (
            Trip.objects
            .select_related("branch", "vehicle", "driver")
            .filter(
                trip_date__date__gte=start_date,
                trip_date__date__lte=end_date,
            )
            .exclude(customer_phone__isnull=True)
            .exclude(customer_phone__exact="")
            .order_by("-trip_date", "-id")
        )

        if role == "transport" and branch:
            trips = trips.filter(branch=branch)

        for t in trips:
            phone = _transport_normalize_phone(getattr(t, "customer_phone", "") or "")
            trip_name = (getattr(t, "customer_name", "") or "").strip()
            name = trip_name or name_map.get(phone) or "Transport Customer"

            _add_transport_row(
                rows_by_phone=rows_by_phone,
                phone=phone,
                name=name,
                amount=getattr(t, "amount_charged", 0) or 0,
                source="Trip",
                pickup=getattr(t, "origin", "") or "",
                destination=getattr(t, "destination", "") or "",
                when=getattr(t, "trip_date", None),
                status="TRIP",
            )

    rows = list(rows_by_phone.values())

    for row in rows:
        row["source_text"] = ", ".join(sorted(row["sources"])) if row["sources"] else "Transport"
        row["status_text"] = ", ".join(sorted(row["statuses"])) if row["statuses"] else "-"
        if row.get("last_date"):
            try:
                row["last_date_text"] = timezone.localtime(row["last_date"]).strftime("%Y-%m-%d %H:%M")
            except Exception:
                row["last_date_text"] = str(row["last_date"])
        else:
            row["last_date_text"] = "-"

    rows.sort(key=lambda x: x["last_date"] or timezone.now(), reverse=True)

    return rows


@login_required
@role_required(["admin", "cashier", "transport"])
def transport_customers_sms(request):
    """
    SMS Center page:
    - transport customers only
    - separate from sales customers
    - sends personalized SMS with saved names
    """
    if request.method == "POST":
        form = TransportCustomersSMSForm(request.POST)
        form = _style_form_fields(form)

        if form.is_valid():
            start_date = form.cleaned_data["start_date"]
            end_date = form.cleaned_data["end_date"]
            source = form.cleaned_data.get("source") or "all"
            status = form.cleaned_data.get("status") or ""
            sender = form.cleaned_data.get("sender_id")
            message_template = (form.cleaned_data.get("message") or "").strip()

            selected_phones = request.POST.getlist("selected_phones")

            redirect_url = (
                f"{request.path}"
                f"?start_date={start_date}"
                f"&end_date={end_date}"
                f"&source={source}"
                f"&status={status}"
            )

            if not message_template:
                messages.error(request, "Type the SMS message first.")
                return redirect(redirect_url)

            if not selected_phones:
                messages.error(request, "Select at least one transport customer.")
                return redirect(redirect_url)

            rows = _transport_customer_rows(request, start_date, end_date, source=source, status=status)
            selected_set = set(selected_phones)

            sent_count = 0
            failed_count = 0
            skipped_count = 0

            for row in rows:
                if row["phone"] not in selected_set:
                    continue

                contact = _sync_transport_customer_to_sms_contact(
                    row,
                    created_by=request.user,
                )

                if not contact:
                    skipped_count += 1
                    continue

                personalized_message = _render_transport_customer_message(
                    message_template,
                    row,
                    contact=contact,
                )

                if not personalized_message:
                    skipped_count += 1
                    continue

                result = send_quick_sms(
                    message=personalized_message,
                    contacts=[contact],
                    manual_numbers=[],
                    sender=sender,
                    created_by=request.user,
                )

                if result.get("ok"):
                    sent_count += int(result.get("count", 0) or 0)
                else:
                    failed_count += 1

            if sent_count > 0:
                messages.success(
                    request,
                    f"Transport SMS sent to {sent_count} customer(s). "
                    f"Failed: {failed_count}. Skipped invalid: {skipped_count}."
                )
            else:
                messages.error(
                    request,
                    f"No transport SMS was sent. Failed: {failed_count}. Skipped invalid: {skipped_count}."
                )

            return redirect(redirect_url)

    else:
        form = TransportCustomersSMSForm(request.GET or None)
        form = _style_form_fields(form)

    if form.is_valid():
        start_date = form.cleaned_data["start_date"]
        end_date = form.cleaned_data["end_date"]
        source = form.cleaned_data.get("source") or "all"
        status = form.cleaned_data.get("status") or ""
    else:
        today = timezone.localdate()
        start_date = today
        end_date = today
        source = "all"
        status = ""

    rows = _transport_customer_rows(request, start_date, end_date, source=source, status=status)

    total_booking_count = sum(int(r["booking_count"] or 0) for r in rows)
    total_trip_count = sum(int(r["trip_count"] or 0) for r in rows)
    total_amount = sum(int(r["total_amount"] or 0) for r in rows)

    return render(request, "sms/transport_customers_sms.html", {
        "form": form,
        "rows": rows,
        "start_date": start_date,
        "end_date": end_date,
        "source": source,
        "status": status,
        "total_customers": len(rows),
        "total_booking_count": total_booking_count,
        "total_trip_count": total_trip_count,
        "total_amount": total_amount,
    })