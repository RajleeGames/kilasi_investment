from __future__ import annotations

import csv
import io
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Dict, Any

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import (
    Contact,
    ContactGroup,
    ContactImport,
    SMSCampaign,
    SMSMessage,
    SenderID,
)
from .utils import send_sms_batch, get_delivery_report, normalize_phone


User = get_user_model()


# =========================================================
# HELPERS
# =========================================================
def get_default_sender() -> SenderID | None:
    return SenderID.objects.filter(is_active=True, is_default=True).first()


def normalize_scientific_phone(value: str) -> str:
    """
    Convert values like 2.55765E+11 into 255765000000 style string safely.
    """
    raw = (value or "").strip()
    if not raw:
        return ""

    if "e+" in raw.lower() or "e" in raw.lower():
        try:
            dec = Decimal(raw)
            return format(dec.quantize(Decimal("1")), "f")
        except (InvalidOperation, ValueError):
            return raw

    return raw


def clean_phone(phone: str) -> str:
    """
    Normalize Tanzania phone numbers into:
    2557XXXXXXXX or 2556XXXXXXXX
    """
    raw = (phone or "").strip()

    raw = normalize_scientific_phone(raw)

    try:
        raw = normalize_phone(raw or "")
    except Exception:
        pass

    raw = str(raw).strip()
    raw = raw.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    raw = raw.replace(":::", ",")
    raw = re.sub(r"[^\d+,]", "", raw)

    # if there are multiple numbers in one field, keep the first one
    if "," in raw:
        raw = raw.split(",", 1)[0].strip()

    if raw.startswith("+"):
        raw = raw[1:]

    if raw.startswith("0") and len(raw) == 10:
        raw = "255" + raw[1:]

    if raw.startswith(("6", "7")) and len(raw) == 9:
        raw = "255" + raw

    if raw.startswith("255") and len(raw) > 12:
        raw = raw[:12]

    return raw.strip()


def is_valid_tz_phone(phone: str) -> bool:
    if not phone:
        return False
    if not phone.isdigit():
        return False
    if not phone.startswith("255"):
        return False
    if len(phone) != 12:
        return False
    if phone[3] not in {"6", "7"}:
        return False
    return True


def looks_like_phone_name(name: str, phone: str) -> bool:
    value = (name or "").strip()
    if not value:
        return True

    cleaned_name = re.sub(r"[^\d]", "", value)
    cleaned_phone = re.sub(r"[^\d]", "", phone or "")

    return cleaned_name == cleaned_phone


def extract_csv_name(row: dict) -> str:
    """
    Extract the best possible name from many possible CSV formats.
    Supports:
    - Google Contacts style
    - split First/Middle/Last format
    - File As / Organization fallback
    """
    direct_candidates = [
        row.get("Name"),
        row.get("Display Name"),
        row.get("Full Name"),
        row.get("File As"),
        row.get("Nickname"),
        row.get("Organization Name"),
        row.get("Organization 1 - Name"),
        row.get("Company"),
    ]

    for value in direct_candidates:
        value = (value or "").strip()
        if value:
            return value

    first = (row.get("First Name") or "").strip()
    middle = (row.get("Middle Name") or "").strip()
    last = (row.get("Last Name") or "").strip()

    split_name = " ".join(part for part in [first, middle, last] if part).strip()
    if split_name:
        return split_name

    given = (row.get("Given Name") or "").strip()
    additional = (row.get("Additional Name") or "").strip()
    family = (row.get("Family Name") or "").strip()

    google_split = " ".join(part for part in [given, additional, family] if part).strip()
    if google_split:
        return google_split

    return ""


def extract_csv_phone(row: dict) -> str:
    """
    Supports common CSV headers including Google Contacts export.
    """
    phone_keys = [
        "phone",
        "Phone",
        "mobile",
        "Mobile",
        "Phone 1 - Value",
        "Phone 2 - Value",
        "Phone 3 - Value",
        "Primary Phone",
        "Mobile Phone",
        "Value",
        "Number",
    ]

    for key in phone_keys:
        value = (row.get(key) or "").strip()
        if value:
            return value

    for key, value in row.items():
        key_text = str(key or "").lower()
        if any(token in key_text for token in ["phone", "mobile", "number"]):
            value = (value or "").strip()
            if value:
                return value

    return ""


def unique_contacts_from_campaign(campaign: SMSCampaign):
    phone_map = {}

    for contact in campaign.contacts.filter(is_active=True):
        phone = clean_phone(contact.phone)
        if is_valid_tz_phone(phone):
            phone_map[phone] = contact

    for group in campaign.groups.all():
        for contact in group.contacts.filter(is_active=True):
            phone = clean_phone(contact.phone)
            if is_valid_tz_phone(phone):
                phone_map[phone] = contact

    return list(phone_map.values())


def parse_balance_amount(balance_resp: dict) -> str:
    if not isinstance(balance_resp, dict):
        return "N/A"

    data = balance_resp.get("json", {})
    if not isinstance(data, dict):
        return "N/A"

    for key in ["credit_balance", "balance", "amount", "sms_balance"]:
        value = data.get(key)
        if value not in [None, ""]:
            return str(value)

    data_field = data.get("data")
    if isinstance(data_field, dict):
        for key in ["credit_balance", "balance", "amount", "sms_balance"]:
            value = data_field.get(key)
            if value not in [None, ""]:
                return str(value)

    return "N/A"


# =========================================================
# CONTACT IMPORT
# =========================================================
@transaction.atomic
def import_contacts_from_csv(
    *,
    file_obj,
    group: ContactGroup | None = None,
    overwrite_names: bool = False,
    created_by=None,
) -> Dict[str, Any]:
    """
    Behavior:
    - duplicates inside same CSV are skipped
    - duplicates already in DB are not re-created
    - existing contacts get name updated if current name is blank/phone-like
    """
    content = file_obj.read()
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="ignore")

    reader = csv.DictReader(io.StringIO(content))

    imported = 0
    skipped = 0
    duplicates = 0
    updated_names = 0
    errors = []

    import_log = ContactImport.objects.create(
        source="CSV",
        file_name=getattr(file_obj, "name", "") or "",
        created_by=created_by,
    )

    seen_in_file = set()

    for idx, row in enumerate(reader, start=2):
        raw_name = extract_csv_name(row)
        raw_phone = extract_csv_phone(row)

        phone = clean_phone(raw_phone)

        if not is_valid_tz_phone(phone):
            skipped += 1
            errors.append(f"Row {idx}: invalid phone '{raw_phone}'")
            continue

        if phone in seen_in_file:
            duplicates += 1
            continue
        seen_in_file.add(phone)

        existing = Contact.objects.filter(phone=phone).first()

        if existing:
            duplicates += 1

            should_update_name = (
                raw_name
                and (
                    overwrite_names
                    or looks_like_phone_name(existing.name, existing.phone)
                    or not (existing.name or "").strip()
                )
            )

            changed = False

            if should_update_name and existing.name != raw_name:
                existing.name = raw_name
                changed = True
                updated_names += 1

            if group:
                existing.groups.add(group)

            if created_by and getattr(existing, "created_by_id", None) is None:
                existing.created_by = created_by
                changed = True

            if changed:
                update_fields = []
                if should_update_name:
                    update_fields.append("name")
                if created_by and getattr(existing, "created_by_id", None) is None:
                    update_fields.append("created_by")
                if hasattr(existing, "updated_at"):
                    update_fields.append("updated_at")

                if update_fields:
                    existing.save(update_fields=update_fields)

            continue

        contact = Contact.objects.create(
            phone=phone,
            name=raw_name if raw_name else phone,
            created_by=created_by,
        )

        if group:
            contact.groups.add(group)

        imported += 1

    note_lines = errors[:200]
    note_lines.append(f"Duplicates skipped: {duplicates}")
    note_lines.append(f"Names updated: {updated_names}")

    import_log.imported_count = imported
    import_log.skipped_count = skipped
    import_log.notes = "\n".join(note_lines)
    import_log.save(update_fields=["imported_count", "skipped_count", "notes"])

    return {
        "imported": imported,
        "skipped": skipped,
        "duplicates": duplicates,
        "updated_names": updated_names,
        "errors": errors,
        "import_log": import_log,
    }


@transaction.atomic
def import_contacts_from_text(
    *,
    text: str,
    group: ContactGroup | None = None,
    overwrite_names: bool = False,
    created_by=None,
) -> Dict[str, Any]:
    imported = 0
    skipped = 0
    duplicates = 0
    updated_names = 0
    errors = []

    import_log = ContactImport.objects.create(
        source="PASTE",
        file_name="",
        created_by=created_by,
    )

    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    seen_in_text = set()

    for idx, line in enumerate(lines, start=1):
        if "," in line:
            name_part, phone_part = line.split(",", 1)
            raw_name = name_part.strip()
            raw_phone = phone_part.strip()
        else:
            raw_name = ""
            raw_phone = line.strip()

        phone = clean_phone(raw_phone)

        if not is_valid_tz_phone(phone):
            skipped += 1
            errors.append(f"Line {idx}: invalid phone '{raw_phone}'")
            continue

        if phone in seen_in_text:
            duplicates += 1
            continue
        seen_in_text.add(phone)

        existing = Contact.objects.filter(phone=phone).first()

        if existing:
            duplicates += 1

            should_update_name = (
                raw_name
                and (
                    overwrite_names
                    or looks_like_phone_name(existing.name, existing.phone)
                    or not (existing.name or "").strip()
                )
            )

            changed = False

            if should_update_name and existing.name != raw_name:
                existing.name = raw_name
                changed = True
                updated_names += 1

            if group:
                existing.groups.add(group)

            if created_by and getattr(existing, "created_by_id", None) is None:
                existing.created_by = created_by
                changed = True

            if changed:
                update_fields = []
                if should_update_name:
                    update_fields.append("name")
                if created_by and getattr(existing, "created_by_id", None) is None:
                    update_fields.append("created_by")
                if hasattr(existing, "updated_at"):
                    update_fields.append("updated_at")

                if update_fields:
                    existing.save(update_fields=update_fields)

            continue

        contact = Contact.objects.create(
            phone=phone,
            name=raw_name if raw_name else phone,
            created_by=created_by,
        )

        if group:
            contact.groups.add(group)

        imported += 1

    note_lines = errors[:200]
    note_lines.append(f"Duplicates skipped: {duplicates}")
    note_lines.append(f"Names updated: {updated_names}")

    import_log.imported_count = imported
    import_log.skipped_count = skipped
    import_log.notes = "\n".join(note_lines)
    import_log.save(update_fields=["imported_count", "skipped_count", "notes"])

    return {
        "imported": imported,
        "skipped": skipped,
        "duplicates": duplicates,
        "updated_names": updated_names,
        "errors": errors,
        "import_log": import_log,
    }


# =========================================================
# DIRECT SEND
# =========================================================
@transaction.atomic
def send_quick_sms(
    *,
    message: str,
    contacts: Iterable[Contact] | None = None,
    manual_numbers: Iterable[str] | None = None,
    sender: SenderID | None = None,
    created_by=None,
    campaign: SMSCampaign | None = None,
) -> Dict[str, Any]:
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "Message cannot be empty."}

    sender = sender or get_default_sender()

    recipients_payload = []
    created_message_ids = []

    seen = set()
    seq = 1

    for contact in contacts or []:
        phone = clean_phone(contact.phone)
        if not is_valid_tz_phone(phone):
            continue
        if phone in seen:
            continue
        seen.add(phone)

        sms = SMSMessage.objects.create(
            campaign=campaign,
            contact=contact,
            sender_id=sender,
            dest_addr=phone,
            message=message,
            status="PENDING",
            created_by=created_by,
        )
        created_message_ids.append(sms.id)
        recipients_payload.append({
            "recipient_id": str(seq),
            "dest_addr": phone,
        })
        seq += 1

    for raw_phone in manual_numbers or []:
        phone = clean_phone(raw_phone)
        if not is_valid_tz_phone(phone):
            continue
        if phone in seen:
            continue
        seen.add(phone)

        sms = SMSMessage.objects.create(
            campaign=campaign,
            contact=None,
            sender_id=sender,
            dest_addr=phone,
            message=message,
            status="PENDING",
            created_by=created_by,
        )
        created_message_ids.append(sms.id)
        recipients_payload.append({
            "recipient_id": str(seq),
            "dest_addr": phone,
        })
        seq += 1

    if not recipients_payload:
        return {"ok": False, "error": "No valid recipients found."}

    response = send_sms_batch(
        recipients=recipients_payload,
        message=message,
        source_addr=sender.name if sender else None,
    )

    status_code = response.get("status_code")
    raw_json = response.get("json", {})
    api_ok = 200 <= (status_code or 0) < 300

    update_data = {"response_raw": raw_json}
    now = timezone.now()

    if api_ok:
        update_data["status"] = "SENT"
        update_data["sent_at"] = now
    else:
        update_data["status"] = "FAILED"
        update_data["error_text"] = str(raw_json)

    SMSMessage.objects.filter(id__in=created_message_ids).update(**update_data)

    messages = list(SMSMessage.objects.filter(id__in=created_message_ids).order_by("id"))
    msg_by_phone = {m.dest_addr: m for m in messages}

    if isinstance(raw_json, dict):
        data = raw_json.get("data")
        if isinstance(data, list):
            for item in data:
                dest = clean_phone(str(item.get("dest_addr", "")))
                req_id = item.get("request_id")
                recip_id = item.get("recipient_id")
                status = item.get("status") or "SENT"
                msg = msg_by_phone.get(dest)
                if msg:
                    msg.request_id = req_id
                    msg.recipient_id = recip_id
                    if api_ok:
                        msg.status = str(status).upper() if status else "SENT"
                        msg.sent_at = now
                    else:
                        msg.status = "FAILED"
                    msg.response_raw = item
                    msg.save(update_fields=[
                        "request_id", "recipient_id", "status", "sent_at", "response_raw"
                    ])

    if campaign:
        campaign.total_recipients = SMSMessage.objects.filter(campaign=campaign).count()
        campaign.sent_count = SMSMessage.objects.filter(campaign=campaign, status="SENT").count()
        campaign.failed_count = SMSMessage.objects.filter(campaign=campaign, status="FAILED").count()
        campaign.status = "SENT" if campaign.sent_count and campaign.failed_count == 0 else (
            "PARTIAL" if campaign.sent_count else "FAILED"
        )
        campaign.sent_at = now if campaign.sent_count else None
        campaign.save(update_fields=[
            "total_recipients", "sent_count", "failed_count", "status", "sent_at"
        ])

    return {
        "ok": api_ok,
        "status_code": status_code,
        "response": raw_json,
        "created_message_ids": created_message_ids,
        "count": len(created_message_ids),
    }


# =========================================================
# CAMPAIGN SEND
# =========================================================
@transaction.atomic
def send_campaign(campaign: SMSCampaign, created_by=None) -> Dict[str, Any]:
    message = (campaign.get_message_text() or "").strip()
    if not message:
        return {"ok": False, "error": "Campaign message is empty."}

    contacts = unique_contacts_from_campaign(campaign)
    sender = campaign.sender_id or get_default_sender()

    return send_quick_sms(
        message=message,
        contacts=contacts,
        manual_numbers=[],
        sender=sender,
        created_by=created_by,
        campaign=campaign,
    )


# =========================================================
# DELIVERY REPORT SYNC
# =========================================================
def sync_message_delivery(message_obj: SMSMessage) -> Dict[str, Any]:
    if not message_obj.request_id or not message_obj.dest_addr:
        return {"ok": False, "error": "Message has no request_id or destination address."}

    response = get_delivery_report(
        dest_addr=message_obj.dest_addr,
        request_id=message_obj.request_id,
    )

    raw_json = response.get("json", {})
    status_code = response.get("status_code", 0)

    if status_code and 200 <= status_code < 300:
        delivery_status = None

        if isinstance(raw_json, dict):
            delivery_status = raw_json.get("delivery_status") or raw_json.get("status")
            data = raw_json.get("data")
            if not delivery_status and isinstance(data, dict):
                delivery_status = data.get("delivery_status") or data.get("status")

        if delivery_status:
            status_upper = str(delivery_status).upper()

            if "DELIVER" in status_upper:
                message_obj.status = "DELIVERED"
                message_obj.delivered_at = timezone.now()
            elif "UNDELIVER" in status_upper:
                message_obj.status = "UNDELIVERED"
            elif "FAIL" in status_upper:
                message_obj.status = "FAILED"

        message_obj.response_raw = raw_json
        message_obj.save(update_fields=["status", "delivered_at", "response_raw"])

        campaign = message_obj.campaign
        if campaign:
            campaign.delivered_count = SMSMessage.objects.filter(
                campaign=campaign, status="DELIVERED"
            ).count()
            campaign.failed_count = SMSMessage.objects.filter(
                campaign=campaign, status__in=["FAILED", "UNDELIVERED"]
            ).count()
            campaign.save(update_fields=["delivered_count", "failed_count"])

        return {
            "ok": True,
            "response": raw_json,
            "message_status": message_obj.status,
        }

    message_obj.response_raw = raw_json
    message_obj.save(update_fields=["response_raw"])

    return {
        "ok": False,
        "response": raw_json,
        "message_status": message_obj.status,
    }


def sync_campaign_delivery(campaign: SMSCampaign) -> Dict[str, Any]:
    qs = campaign.messages.exclude(request_id__isnull=True).exclude(request_id__exact="")
    total = 0
    updated = 0

    for msg in qs:
        total += 1
        result = sync_message_delivery(msg)
        if result.get("ok"):
            updated += 1

    campaign.delivered_count = SMSMessage.objects.filter(
        campaign=campaign, status="DELIVERED"
    ).count()
    campaign.failed_count = SMSMessage.objects.filter(
        campaign=campaign, status__in=["FAILED", "UNDELIVERED"]
    ).count()
    campaign.save(update_fields=["delivered_count", "failed_count"])

    return {
        "ok": True,
        "checked": total,
        "updated": updated,
    }