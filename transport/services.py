import logging
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from sms.models import Contact, ContactGroup, SMSMessage, SenderID
from sms.utils import normalize_phone, send_sms_batch

from .models import TransportSMSTemplate, Trip

logger = logging.getLogger(__name__)
User = get_user_model()

DEFAULT_PENDING_MESSAGE = (
    "Ndugu {{ first_name }}, safari yako kutoka {{ origin }} kwenda {{ destination }} "
    "itaanza muda si mrefu kwa gari namba {{ plate_number }}. Endelea kufurahia "
    "huduma zetu ukiwa safarini. Asante kwa kuchagua Kilasi Investment. "
    "Kwa msaada piga {{ company_phone }}."
)

DEFAULT_COMPLETED_MESSAGE = (
    "Ndugu {{ first_name }}, tumefika salama {{ destination }}. Tunakushukuru kwa "
    "kusafiri nasi na kwa kutuamini. Asante kwa kuchagua Kilasi Investment. "
    "Kwa msaada piga {{ company_phone }}."
)

DEFAULTS = {
    TransportSMSTemplate.EVENT_PENDING: DEFAULT_PENDING_MESSAGE,
    TransportSMSTemplate.EVENT_COMPLETED: DEFAULT_COMPLETED_MESSAGE,
}

SUPPORTED_PLACEHOLDERS = [
    "{{ name }}",
    "{{ first_name }}",
    "{{ phone }}",
    "{{ origin }}",
    "{{ destination }}",
    "{{ plate_number }}",
    "{{ vehicle }}",
    "{{ driver_name }}",
    "{{ trip_date }}",
    "{{ company_phone }}",
]


def ensure_default_transport_templates():
    templates = []
    for event, message in DEFAULTS.items():
        obj, _ = TransportSMSTemplate.objects.get_or_create(
            event=event,
            defaults={"message": message, "is_active": True},
        )
        templates.append(obj)
    return templates


def _first_name(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    return parts[0] if parts else "Mteja"


def render_trip_sms(template_text: str, trip: Trip) -> str:
    local_trip_date = timezone.localtime(trip.trip_date)
    name = (trip.customer_name or "Mteja").strip()
    driver_name = trip.driver.full_name
    company_phone = getattr(settings, "TRANSPORT_SUPPORT_PHONE", "0780850748")

    values = {
        "name": name,
        "first_name": _first_name(name),
        "phone": normalize_phone(trip.customer_phone),
        "origin": trip.origin,
        "destination": trip.destination,
        "plate_number": trip.vehicle.plate_number,
        "vehicle": str(trip.vehicle),
        "driver_name": driver_name,
        "trip_date": local_trip_date.strftime("%d/%m/%Y %H:%M"),
        "company_phone": company_phone,
    }

    message = (template_text or "").strip()
    for key, value in values.items():
        for token in (f"{{{{ {key} }}}}", f"{{{{{key}}}}}", f"{{{key}}}", f"[{key}]"):
            message = message.replace(token, str(value or ""))
    return " ".join(message.split())


def _get_sender(template: TransportSMSTemplate):
    if template.sender_id and template.sender_id.is_active:
        return template.sender_id
    return SenderID.objects.filter(is_active=True, is_default=True).first() or SenderID.objects.filter(
        is_active=True
    ).first()


def _sync_contact(trip: Trip, created_by=None):
    phone = normalize_phone(trip.customer_phone)
    if not phone:
        return None

    group, _ = ContactGroup.objects.get_or_create(
        name="Transport Customers",
        defaults={"description": "Customers collected automatically from transport trips."},
    )
    contact = Contact.objects.filter(phone=phone).first()
    if contact:
        changed = []
        if trip.customer_name and (not contact.name or contact.name.lower() in {"customer", "mteja", "transport customer"}):
            contact.name = trip.customer_name
            changed.append("name")
        if not contact.is_active:
            contact.is_active = True
            changed.append("is_active")
        if changed:
            changed.append("updated_at")
            contact.save(update_fields=changed)
    else:
        contact = Contact.objects.create(
            name=trip.customer_name,
            phone=phone,
            is_active=True,
            notes="Auto-saved from a transport trip.",
            created_by=created_by,
        )
    contact.groups.add(group)
    return contact


def _extract_request_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("request_id", "requestId", "request-id"):
        if payload.get(key):
            return str(payload[key])
    data = payload.get("data")
    if isinstance(data, dict):
        return _extract_request_id(data)
    return ""


def _extract_recipient_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("recipient_id", "recipientId"):
        if payload.get(key):
            return str(payload[key])
    recipients = payload.get("recipients") or payload.get("data")
    if isinstance(recipients, list) and recipients and isinstance(recipients[0], dict):
        return str(recipients[0].get("recipient_id") or recipients[0].get("recipientId") or "")
    if isinstance(recipients, dict):
        return _extract_recipient_id(recipients)
    return ""


def _response_is_success(status_code: int, payload: Any) -> bool:
    if not 200 <= int(status_code or 0) < 300:
        return False
    if isinstance(payload, dict):
        if payload.get("successful") is False:
            return False
        if str(payload.get("status", "")).lower() in {"failed", "error", "rejected"}:
            return False
    return True


def _set_trip_sms_result(trip_id: int, event: str, ok: bool, error: str = ""):
    now = timezone.now()
    if event == TransportSMSTemplate.EVENT_PENDING:
        updates = {
            "pending_sms_sent": ok,
            "pending_sms_error": "" if ok else error[:2000],
        }
        if ok:
            updates["pending_sms_sent_at"] = now
    else:
        updates = {
            "completed_sms_sent": ok,
            "completed_sms_error": "" if ok else error[:2000],
        }
        if ok:
            updates["completed_sms_sent_at"] = now
    Trip.objects.filter(pk=trip_id).update(**updates)


def send_trip_sms(trip_id: int, event: str, created_by_id: int | None = None, force: bool = False):
    """Send one automatic trip SMS and record it in the existing SMSMessage table."""
    try:
        ensure_default_transport_templates()
        trip = Trip.objects.select_related("vehicle", "driver__user", "branch").get(pk=trip_id)
        created_by = User.objects.filter(pk=created_by_id).first() if created_by_id else None

        if event == TransportSMSTemplate.EVENT_PENDING and trip.pending_sms_sent and not force:
            return {"ok": True, "skipped": True, "reason": "Pending SMS was already sent."}
        if event == TransportSMSTemplate.EVENT_COMPLETED and trip.completed_sms_sent and not force:
            return {"ok": True, "skipped": True, "reason": "Completed SMS was already sent."}
        if event == TransportSMSTemplate.EVENT_COMPLETED and trip.status != Trip.STATUS_COMPLETED:
            return {"ok": False, "skipped": True, "reason": "Trip is not completed."}

        template = TransportSMSTemplate.objects.get(event=event)
        if not template.is_active:
            return {"ok": True, "skipped": True, "reason": "Template is disabled."}

        phone = normalize_phone(trip.customer_phone)
        if not phone:
            error = "Customer phone is empty or invalid."
            _set_trip_sms_result(trip.pk, event, False, error)
            return {"ok": False, "error": error}

        message = render_trip_sms(template.message, trip)
        sender = _get_sender(template)
        source_addr = sender.name if sender else getattr(settings, "BEEM_SENDER_ID", "")
        contact = _sync_contact(trip, created_by=created_by)

        log = SMSMessage.objects.create(
            contact=contact,
            sender_id=sender,
            dest_addr=phone,
            message=message,
            status="PENDING",
            created_by=created_by,
        )

        result = send_sms_batch(
            recipients=[{"recipient_id": 1, "dest_addr": phone}],
            message=message,
            source_addr=source_addr,
        )
        status_code = int(result.get("status_code", 0) or 0)
        payload = result.get("json") or {}
        ok = _response_is_success(status_code, payload)

        log.response_raw = payload
        log.request_id = _extract_request_id(payload) or None
        log.recipient_id = _extract_recipient_id(payload) or None
        if ok:
            log.status = "SENT"
            log.sent_at = timezone.now()
            log.error_text = ""
            log.save(
                update_fields=[
                    "response_raw",
                    "request_id",
                    "recipient_id",
                    "status",
                    "sent_at",
                    "error_text",
                ]
            )
            _set_trip_sms_result(trip.pk, event, True)
            return {"ok": True, "status_code": status_code, "response": payload, "message_id": log.pk}

        error = str(payload.get("error") or payload.get("message") or payload or "SMS sending failed.")
        log.status = "FAILED"
        log.error_text = error[:2000]
        log.save(
            update_fields=[
                "response_raw",
                "request_id",
                "recipient_id",
                "status",
                "error_text",
            ]
        )
        _set_trip_sms_result(trip.pk, event, False, error)
        return {"ok": False, "status_code": status_code, "error": error, "message_id": log.pk}
    except Exception as exc:
        logger.exception("Automatic transport SMS failed for trip=%s event=%s", trip_id, event)
        try:
            _set_trip_sms_result(trip_id, event, False, str(exc))
        except Exception:
            pass
        return {"ok": False, "error": str(exc)}
