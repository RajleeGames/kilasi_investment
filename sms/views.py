from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from users.utils import role_required

from .forms import (
    ContactForm,
    ContactImportForm,
    QuickSMSForm,
    SalesCustomersSMSForm,
    SMSCampaignForm,
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
def _style_form_fields(form):
    """
    Apply Bootstrap-friendly classes to Django form widgets
    so all SMS templates look clean without repeating code.
    """
    for _, field in form.fields.items():
        widget = field.widget
        existing = widget.attrs.get("class", "").strip()
        widget_name = widget.__class__.__name__

        if widget_name in ["CheckboxInput"]:
            css = "form-check-input"
        elif widget_name in ["CheckboxSelectMultiple"]:
            css = "form-check-input"
        elif widget_name in ["SelectMultiple"]:
            css = "form-select"
        elif widget_name in ["Select"]:
            css = "form-select"
        elif widget_name in ["Textarea"]:
            css = "form-control"
        elif widget_name in ["ClearableFileInput", "FileInput"]:
            css = "form-control"
        else:
            css = "form-control"

        widget.attrs["class"] = f"{existing} {css}".strip()

    return form

# =========================================================
# SALES CUSTOMERS SMS
# Personalized SMS using customer names
# =========================================================

def _looks_like_phone_name(name, phone):
    """
    Detect contacts where the name was accidentally saved as phone number.
    Example:
      name = 255712345678
      phone = 255712345678
    """
    name_digits = "".join(ch for ch in (name or "") if ch.isdigit())
    phone_digits = "".join(ch for ch in (phone or "") if ch.isdigit())

    return bool(name_digits and phone_digits and name_digits == phone_digits)


def _first_name(full_name):
    """
    Get first name only.
    Example:
      Miraji Ally => Miraji
    """
    full_name = (full_name or "").strip()

    if not full_name:
        return "Customer"

    parts = full_name.split()
    return parts[0].strip() if parts else "Customer"


def _message_has_name_placeholder(message):
    """
    If user manually writes a placeholder, we replace it.
    If user does NOT write placeholder, we automatically add the name.
    """
    message = (message or "").lower()

    tokens = [
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

    return any(token in message for token in tokens)


def _clean_existing_greeting(message):
    """
    Remove generic greeting if user wrote it without name.

    Examples:
      Hello, thanks...        => thanks...
      Ndugu, asante...        => asante...
      Ndugu mteja, asante...  => asante...
      Habari, karibu...       => karibu...

    But:
      Asante kwa kununua...   => stays same
      Karibu tena...          => stays same

    Then we force:
      Ndugu Miraji, asante...
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

    # Extra cleanup for patterns like:
    # "mteja, asante..."
    # "customer, thanks..."
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
      1. SMS contact name, if it is real
      2. Sales customer name from invoice
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

    weak_names = ["", "customer", "walk-in", "unknown", "n/a", "-", "—", "mteja"]

    name = contact_name or row_name or "Customer"

    # If SMS contact name is weak or phone-like, use sales customer name
    if row_name:
        if name.strip().lower() in weak_names or _looks_like_phone_name(name, phone):
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

    Case 2: User writes:
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

    # If user wrote placeholder manually, only replace placeholders
    if _message_has_name_placeholder(message):
        for key, value in replacements.items():
            message = message.replace(key, value)
        return message.strip()

    # If user wrote normal message, force name automatically
    body = _clean_existing_greeting(message)

    if not body:
        body = "Asante kwa kutembelea Kilasi Investment. Karibu tena."

    return f"Ndugu {first_name}, {body}".strip()


def _sync_sales_customer_to_sms_contact(customer, created_by=None):
    """
    Create/update SMS Contact from sales Customer.
    This keeps sales customers available in SMS Center.

    Important:
    - If existing SMS contact name is phone number, update it to real saved customer name.
    - If existing SMS contact name is Customer / Walk-in / Unknown, update it.
    """
    if not customer:
        return None

    raw_name = (getattr(customer, "name", "") or "").strip()
    raw_phone = (getattr(customer, "phone", "") or "").strip()

    if not raw_phone:
        return None

    try:
        from .services import clean_phone, is_valid_tz_phone
    except Exception:
        return None

    phone = clean_phone(raw_phone)

    if not is_valid_tz_phone(phone):
        return None

    name = raw_name or phone

    group, _ = ContactGroup.objects.get_or_create(
        name="Sales Customers",
        defaults={
            "description": "Customers collected automatically from sales invoices."
        }
    )

    contact = Contact.objects.filter(phone=phone).first()

    if contact:
        changed = False

        weak_names = ["", "customer", "walk-in", "unknown", "n/a", "-", "—", "mteja"]
        old_name = (contact.name or "").strip()
        old_name_lower = old_name.lower()

        should_update_name = (
            name
            and (
                old_name_lower in weak_names
                or _looks_like_phone_name(old_name, phone)
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
        notes="Auto-saved from sales invoice.",
    )
    contact.groups.add(group)

    return contact


def _sales_customer_rows(start_date, end_date, payment_method=""):
    """
    Get unique customers from invoices between date range.
    Deduplication is by normalized phone number.
    """
    qs = (
        Invoice.objects
        .select_related("customer", "seller", "branch")
        .filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
            customer__isnull=False,
        )
        .exclude(customer__phone__isnull=True)
        .exclude(customer__phone__exact="")
        .order_by("-created_at")
    )

    if hasattr(Invoice, "invoice_state"):
        qs = qs.filter(invoice_state="active")

    if payment_method:
        qs = qs.filter(status=payment_method)

    rows_by_phone = {}

    try:
        from .services import clean_phone, is_valid_tz_phone
    except Exception:
        clean_phone = None
        is_valid_tz_phone = None

    for inv in qs:
        customer = inv.customer
        raw_phone = (getattr(customer, "phone", "") or "").strip()

        if clean_phone:
            phone = clean_phone(raw_phone)
        else:
            phone = raw_phone.replace(" ", "").replace("-", "")

        if is_valid_tz_phone and not is_valid_tz_phone(phone):
            continue

        if not phone:
            continue

        name = (getattr(customer, "name", "") or "").strip() or "Customer"
        amount = int(getattr(inv, "total_amount", 0) or 0)
        status = (getattr(inv, "status", "") or "").upper()

        if phone not in rows_by_phone:
            rows_by_phone[phone] = {
                "name": name,
                "phone": phone,
                "raw_phone": raw_phone,
                "invoice_count": 0,
                "total_amount": 0,
                "first_date": inv.created_at,
                "last_date": inv.created_at,
                "payments": set(),
                "customer": customer,
            }

        row = rows_by_phone[phone]
        row["invoice_count"] += 1
        row["total_amount"] += amount
        row["payments"].add(status)

        if inv.created_at < row["first_date"]:
            row["first_date"] = inv.created_at

        if inv.created_at > row["last_date"]:
            row["last_date"] = inv.created_at

        weak_names = ["customer", "walk-in", "unknown", "n/a", "-", "—", "mteja"]

        if row["name"].strip().lower() in weak_names and name:
            row["name"] = name

    rows = list(rows_by_phone.values())

    for row in rows:
        row["payments_text"] = ", ".join(sorted(row["payments"]))

    rows.sort(key=lambda x: x["last_date"], reverse=True)

    return rows


@login_required
@role_required(["admin", "cashier"])
def sales_customers_sms(request):
    """
    SMS Center page:
    - filter sales customers by date range/payment method
    - show unique phone numbers
    - send personalized SMS to selected customers
    - automatically adds customer name even if user types normal message
    """
    if request.method == "POST":
        form = SalesCustomersSMSForm(request.POST)
        form = _style_form_fields(form)

        if form.is_valid():
            start_date = form.cleaned_data["start_date"]
            end_date = form.cleaned_data["end_date"]
            payment_method = form.cleaned_data.get("payment_method") or ""
            sender = form.cleaned_data.get("sender_id")
            message_template = (form.cleaned_data.get("message") or "").strip()

            selected_phones = request.POST.getlist("selected_phones")

            redirect_url = (
                f"{request.path}"
                f"?start_date={start_date}"
                f"&end_date={end_date}"
                f"&payment_method={payment_method}"
            )

            if not message_template:
                messages.error(request, "Type the SMS message first.")
                return redirect(redirect_url)

            if not selected_phones:
                messages.error(request, "Select at least one customer.")
                return redirect(redirect_url)

            rows = _sales_customer_rows(start_date, end_date, payment_method)
            selected_set = set(selected_phones)

            sent_count = 0
            failed_count = 0
            skipped_count = 0

            for row in rows:
                if row["phone"] not in selected_set:
                    continue

                contact = _sync_sales_customer_to_sms_contact(
                    row.get("customer"),
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

                # Send one-by-one because each customer gets a different name/message
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
    else:
        today = timezone.localdate()
        start_date = today
        end_date = today
        payment_method = ""

    rows = _sales_customer_rows(start_date, end_date, payment_method)

    total_invoice_count = sum(int(r["invoice_count"] or 0) for r in rows)
    total_amount = sum(int(r["total_amount"] or 0) for r in rows)

    return render(request, "sms/sales_customers_sms.html", {
        "form": form,
        "rows": rows,
        "start_date": start_date,
        "end_date": end_date,
        "payment_method": payment_method,
        "total_customers": len(rows),
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