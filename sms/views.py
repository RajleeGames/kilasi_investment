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