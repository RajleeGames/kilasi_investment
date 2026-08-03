# transport/services.py

import logging
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from sms.models import Contact, ContactGroup, SMSMessage, SenderID
from sms.utils import normalize_phone, send_sms_batch

from .models import Booking, TransportSMSTemplate, Trip


logger = logging.getLogger(__name__)
User = get_user_model()


# =========================================================
# DEFAULT AUTOMATIC SMS MESSAGES
# =========================================================

DEFAULT_PENDING_MESSAGE = (
    "Ndugu {{ first_name }},\n\n"
    "Safari yako kutoka {{ origin }} kwenda {{ destination }} "
    "itaanza muda si mrefu.\n\n"
    "Gari: {{ plate_number }}\n"
    "Dereva: {{ driver_name }}\n\n"
    "Endelea kufurahia huduma zetu ukiwa safarini.\n\n"
    "Asante kwa kuchagua Kilasi Investment.\n"
    "Kwa msaada piga {{ company_phone }}."
)


DEFAULT_COMPLETED_MESSAGE = (
    "Ndugu {{ first_name }},\n\n"
    "Tumefika salama {{ destination }}.\n\n"
    "Tunakushukuru kwa kusafiri nasi na kwa kutuamini.\n\n"
    "Asante kwa kuchagua Kilasi Investment.\n"
    "Kwa msaada piga {{ company_phone }}."
)


DEFAULT_BOSS_PENDING_MESSAGE = (
    "Ndugu {{ boss_name }},\n\n"
    "Gari namba {{ plate_number }} limepangiwa safari "
    "kutoka {{ origin }} kwenda {{ destination }}.\n\n"
    "Gharama: TZS {{ amount }}\n"
    "Dereva: {{ driver_name }}\n"
    "Mteja: {{ name }}\n"
    "Simu: {{ phone }}\n"
    "Tawi: {{ branch }}\n\n"
    "Safari ID: {{ trip_id }}"
)


DEFAULT_BOOKING_CONFIRMED_MESSAGE = (
    "Ndugu {{ first_name }},\n\n"
    "Booking yako ya usafiri imethibitishwa.\n\n"
    "Kutoka: {{ origin }}\n"
    "Kwenda: {{ destination }}\n"
    "Tarehe na muda: {{ booking_datetime }}\n\n"
    "Tafadhali jiandae na ufike eneo la kuchukuliwa kwa wakati.\n\n"
    "Asante kwa kuchagua Kilasi Investment.\n"
    "Kwa msaada piga {{ company_phone }}."
)


DEFAULT_BOOKING_BOSS_MESSAGE = (
    "Ndugu {{ boss_name }},\n\n"
    "Booking mpya ya usafiri imewekwa.\n\n"
    "Mteja: {{ name }}\n"
    "Simu: {{ phone }}\n"
    "Kutoka: {{ origin }}\n"
    "Kwenda: {{ destination }}\n"
    "Tarehe na muda: {{ booking_datetime }}\n"
    "Tawi: {{ branch }}\n\n"
    "Booking ID: {{ booking_id }}"
)


DEFAULT_BOOKING_REMINDER_MESSAGE = (
    "Ndugu {{ first_name }},\n\n"
    "Imebaki takribani saa moja kabla ya booking yako ya usafiri.\n\n"
    "Kutoka: {{ origin }}\n"
    "Kwenda: {{ destination }}\n"
    "Muda: {{ booking_datetime }}\n\n"
    "Tafadhali jiandae na uwe eneo la kuchukuliwa kwa wakati.\n\n"
    "Asante kwa kuchagua Kilasi Investment.\n"
    "Kwa msaada piga {{ company_phone }}."
)


DEFAULTS = {
    TransportSMSTemplate.EVENT_PENDING: DEFAULT_PENDING_MESSAGE,
    TransportSMSTemplate.EVENT_COMPLETED: DEFAULT_COMPLETED_MESSAGE,
    TransportSMSTemplate.EVENT_BOSS_PENDING: DEFAULT_BOSS_PENDING_MESSAGE,
    TransportSMSTemplate.EVENT_BOOKING_CONFIRMED: DEFAULT_BOOKING_CONFIRMED_MESSAGE,
    TransportSMSTemplate.EVENT_BOOKING_BOSS: DEFAULT_BOOKING_BOSS_MESSAGE,
    TransportSMSTemplate.EVENT_BOOKING_REMINDER: DEFAULT_BOOKING_REMINDER_MESSAGE,
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
    "{{ booking_datetime }}",
    "{{ company_phone }}",
    "{{ boss_name }}",
    "{{ amount }}",
    "{{ trip_id }}",
    "{{ booking_id }}",
    "{{ branch }}",
]


# =========================================================
# DEFAULT TEMPLATE CREATION
# =========================================================

def ensure_default_transport_templates():
    """
    Make sure all required automatic transport SMS templates exist.

    Existing templates are never overwritten, so admin edits remain saved.
    """
    templates = []

    for event, message in DEFAULTS.items():
        obj, _created = TransportSMSTemplate.objects.get_or_create(
            event=event,
            defaults={
                "message": message,
                "is_active": True,
            },
        )

        templates.append(obj)

    return templates


# =========================================================
# MESSAGE HELPERS
# =========================================================

def _first_name(full_name: str) -> str:
    """
    Extract the customer's first name.

    Example:
        Miraji Ally -> Miraji
    """
    parts = str(full_name or "").strip().split()

    return parts[0] if parts else "Mteja"


def _normalize_message_line_breaks(message: str) -> str:
    """
    Preserve paragraphs and blank lines entered by admin.

    This function:
    - Converts Windows CRLF line endings to normal newline characters.
    - Removes trailing spaces from each line.
    - Preserves empty lines between paragraphs.
    - Removes empty space only from the beginning and end.

    It intentionally does not use:

        " ".join(message.split())

    because that would convert the entire message into one line.
    """
    message = str(message or "")

    # Normalize line endings from Windows, Linux and old Mac formats.
    message = message.replace("\r\n", "\n")
    message = message.replace("\r", "\n")

    # Remove spaces at the end of each individual line,
    # but preserve all Enter presses and blank lines.
    cleaned_lines = [
        line.rstrip()
        for line in message.split("\n")
    ]

    return "\n".join(cleaned_lines).strip()


def render_trip_sms(template_text: str, trip: Trip) -> str:
    """
    Replace placeholders using the trip details.

    New lines and blank lines from the saved template are preserved.
    """
    local_trip_date = timezone.localtime(trip.trip_date)

    name = str(
        trip.customer_name or "Mteja"
    ).strip()

    driver = getattr(trip, "driver", None)
    vehicle = getattr(trip, "vehicle", None)

    driver_name = ""

    if driver:
        driver_name = str(
            getattr(driver, "full_name", "") or driver
        ).strip()

    plate_number = ""

    if vehicle:
        plate_number = str(
            getattr(vehicle, "plate_number", "") or ""
        ).strip()

    company_phone = getattr(
        settings,
        "TRANSPORT_SUPPORT_PHONE",
        "0780850748",
    )

    values = {
        "name": name,
        "first_name": _first_name(name),
        "phone": normalize_phone(trip.customer_phone),
        "origin": str(trip.origin or "").strip(),
        "destination": str(trip.destination or "").strip(),
        "plate_number": plate_number,
        "vehicle": str(vehicle or "").strip(),
        "driver_name": driver_name,
        "trip_date": local_trip_date.strftime(
            "%d/%m/%Y %H:%M"
        ),
        "company_phone": str(company_phone or "").strip(),
    }

    message = _normalize_message_line_breaks(
        template_text
    )

    for key, value in values.items():
        replacement = str(value or "")

        supported_tokens = (
            f"{{{{ {key} }}}}",
            f"{{{{{key}}}}}",
            f"{{{key}}}",
            f"[{key}]",
        )

        for token in supported_tokens:
            message = message.replace(
                token,
                replacement,
            )

    # Normalize again after replacement, while keeping
    # all paragraphs and blank lines.
    return _normalize_message_line_breaks(message)


def render_boss_trip_sms(template_text: str, trip: Trip) -> str:
    """
    Replace placeholders in the editable boss notification template.

    New lines and blank lines from the saved template are preserved.
    """
    local_trip_date = timezone.localtime(trip.trip_date)

    customer_name = str(
        trip.customer_name or "Mteja"
    ).strip()

    customer_phone = str(
        trip.customer_phone or ""
    ).strip()

    vehicle = getattr(trip, "vehicle", None)
    driver = getattr(trip, "driver", None)
    branch = getattr(trip, "branch", None)

    plate_number = (
        str(getattr(vehicle, "plate_number", "") or "").strip()
        if vehicle
        else "Haijapangwa"
    )

    vehicle_name = (
        str(vehicle).strip()
        if vehicle
        else "Haijapangwa"
    )

    driver_name = (
        str(getattr(driver, "full_name", "") or driver).strip()
        if driver
        else "Haijapangwa"
    )

    branch_name = (
        str(getattr(branch, "name", "") or branch).strip()
        if branch
        else "Haijatajwa"
    )

    try:
        amount_text = f"{trip.amount_charged:,.0f}"
    except (TypeError, ValueError):
        amount_text = str(trip.amount_charged or "0")

    boss_name = str(
        getattr(settings, "TRANSPORT_BOSS_NAME", "Kilasi")
        or "Kilasi"
    ).strip()

    company_phone = str(
        getattr(
            settings,
            "TRANSPORT_SUPPORT_PHONE",
            "0780850748",
        )
        or ""
    ).strip()

    values = {
        "boss_name": boss_name,
        "name": customer_name,
        "first_name": _first_name(customer_name),
        "phone": customer_phone,
        "origin": str(trip.origin or "").strip(),
        "destination": str(trip.destination or "").strip(),
        "plate_number": plate_number,
        "vehicle": vehicle_name,
        "driver_name": driver_name,
        "trip_date": local_trip_date.strftime("%d/%m/%Y %H:%M"),
        "company_phone": company_phone,
        "amount": amount_text,
        "trip_id": trip.pk,
        "branch": branch_name,
    }

    message = _normalize_message_line_breaks(template_text)

    for key, value in values.items():
        replacement = str(value or "")

        supported_tokens = (
            f"{{{{ {key} }}}}",
            f"{{{{{key}}}}}",
            f"{{{key}}}",
            f"[{key}]",
        )

        for token in supported_tokens:
            message = message.replace(token, replacement)

    return _normalize_message_line_breaks(message)


# =========================================================
# SENDER ID
# =========================================================

def _get_sender(template: TransportSMSTemplate):
    """
    Use the sender selected on the transport SMS template.

    If none was selected, use the default active Sender ID.
    """
    if (
        template.sender_id
        and template.sender_id.is_active
    ):
        return template.sender_id

    default_sender = SenderID.objects.filter(
        is_active=True,
        is_default=True,
    ).first()

    if default_sender:
        return default_sender

    return SenderID.objects.filter(
        is_active=True,
    ).first()


# =========================================================
# TRANSPORT CUSTOMER CONTACT SYNC
# =========================================================

def _sync_contact(trip: Trip, created_by=None):
    """
    Save or update the trip customer in the SMS Contact database.

    The customer is added to the Transport Customers group.
    """
    phone = normalize_phone(
        trip.customer_phone
    )

    if not phone:
        return None

    customer_name = str(
        trip.customer_name or ""
    ).strip()

    if not customer_name:
        customer_name = "Transport Customer"

    group, _created = ContactGroup.objects.get_or_create(
        name="Transport Customers",
        defaults={
            "description": (
                "Customers collected automatically "
                "from transport trips."
            ),
        },
    )

    contact = Contact.objects.filter(
        phone=phone
    ).first()

    if contact:
        changed_fields = []

        weak_names = {
            "",
            "customer",
            "mteja",
            "transport customer",
            "walk-in",
            "unknown",
            "n/a",
            "-",
            "—",
        }

        current_name = str(
            contact.name or ""
        ).strip()

        if (
            customer_name
            and current_name.lower() in weak_names
            and current_name != customer_name
        ):
            contact.name = customer_name
            changed_fields.append("name")

        if not contact.is_active:
            contact.is_active = True
            changed_fields.append("is_active")

        if changed_fields:
            changed_fields.append("updated_at")

            contact.save(
                update_fields=changed_fields
            )

    else:
        contact = Contact.objects.create(
            name=customer_name,
            phone=phone,
            is_active=True,
            notes="Auto-saved from a transport trip.",
            created_by=created_by,
        )

    contact.groups.add(group)

    return contact


# =========================================================
# BEEM RESPONSE HELPERS
# =========================================================

def _extract_request_id(payload: Any) -> str:
    """
    Extract the Beem request ID from different possible response formats.
    """
    if not isinstance(payload, dict):
        return ""

    for key in (
        "request_id",
        "requestId",
        "request-id",
    ):
        value = payload.get(key)

        if value:
            return str(value)

    data = payload.get("data")

    if isinstance(data, dict):
        return _extract_request_id(data)

    return ""


def _extract_recipient_id(payload: Any) -> str:
    """
    Extract the Beem recipient ID from different response formats.
    """
    if not isinstance(payload, dict):
        return ""

    for key in (
        "recipient_id",
        "recipientId",
    ):
        value = payload.get(key)

        if value:
            return str(value)

    recipients = (
        payload.get("recipients")
        or payload.get("data")
    )

    if (
        isinstance(recipients, list)
        and recipients
        and isinstance(recipients[0], dict)
    ):
        first_recipient = recipients[0]

        return str(
            first_recipient.get("recipient_id")
            or first_recipient.get("recipientId")
            or ""
        )

    if isinstance(recipients, dict):
        return _extract_recipient_id(
            recipients
        )

    return ""


def _response_is_success(
    status_code: int,
    payload: Any,
) -> bool:
    """
    Decide whether the Beem response represents a successful request.
    """
    if not 200 <= int(status_code or 0) < 300:
        return False

    if isinstance(payload, dict):
        if payload.get("successful") is False:
            return False

        response_status = str(
            payload.get("status", "")
        ).strip().lower()

        if response_status in {
            "failed",
            "error",
            "rejected",
        }:
            return False

    return True


def _extract_error_message(payload: Any) -> str:
    """
    Return a readable error from the Beem response.
    """
    if isinstance(payload, dict):
        error = (
            payload.get("error")
            or payload.get("message")
            or payload.get("detail")
        )

        if error:
            return str(error)

        data = payload.get("data")

        if isinstance(data, dict):
            nested_error = _extract_error_message(
                data
            )

            if nested_error:
                return nested_error

    if payload:
        return str(payload)

    return "SMS sending failed."


# =========================================================
# UPDATE TRIP SMS RESULT
# =========================================================

def _set_trip_sms_result(
    trip_id: int,
    event: str,
    ok: bool,
    error: str = "",
):
    """
    Update the departure or completion SMS status on the Trip.
    """
    now = timezone.now()

    if event == TransportSMSTemplate.EVENT_PENDING:
        updates = {
            "pending_sms_sent": ok,
            "pending_sms_error": (
                ""
                if ok
                else str(error or "")[:2000]
            ),
        }

        if ok:
            updates["pending_sms_sent_at"] = now

    else:
        updates = {
            "completed_sms_sent": ok,
            "completed_sms_error": (
                ""
                if ok
                else str(error or "")[:2000]
            ),
        }

        if ok:
            updates["completed_sms_sent_at"] = now

    # update() avoids triggering model save methods or signals.
    Trip.objects.filter(
        pk=trip_id
    ).update(**updates)


# =========================================================
# AUTOMATIC TRIP SMS SENDER
# =========================================================

def send_trip_sms(
    trip_id: int,
    event: str,
    created_by_id: int | None = None,
    force: bool = False,
):
    """
    Send one automatic trip SMS.

    Supported events:
    - trip_pending
    - trip_completed

    The message is also recorded in the existing SMSMessage model.
    """
    allowed_events = {
        TransportSMSTemplate.EVENT_PENDING,
        TransportSMSTemplate.EVENT_COMPLETED,
    }

    if event not in allowed_events:
        return {
            "ok": False,
            "skipped": True,
            "reason": "Unknown automatic transport SMS event.",
        }

    try:
        ensure_default_transport_templates()

        trip = (
            Trip.objects
            .select_related(
                "vehicle",
                "driver__user",
                "branch",
            )
            .get(pk=trip_id)
        )

        created_by = None

        if created_by_id:
            created_by = User.objects.filter(
                pk=created_by_id
            ).first()

        # Prevent duplicate pending/departure SMS.
        if (
            event == TransportSMSTemplate.EVENT_PENDING
            and trip.pending_sms_sent
            and not force
        ):
            return {
                "ok": True,
                "skipped": True,
                "reason": "Pending SMS was already sent.",
            }

        # Prevent duplicate arrival/completed SMS.
        if (
            event == TransportSMSTemplate.EVENT_COMPLETED
            and trip.completed_sms_sent
            and not force
        ):
            return {
                "ok": True,
                "skipped": True,
                "reason": "Completed SMS was already sent.",
            }

        # Completion message can only be sent when trip is completed.
        if (
            event == TransportSMSTemplate.EVENT_COMPLETED
            and trip.status != Trip.STATUS_COMPLETED
        ):
            return {
                "ok": False,
                "skipped": True,
                "reason": "Trip is not completed.",
            }

        template = TransportSMSTemplate.objects.get(
            event=event
        )

        if not template.is_active:
            return {
                "ok": True,
                "skipped": True,
                "reason": "Template is disabled.",
            }

        phone = normalize_phone(
            trip.customer_phone
        )

        if not phone:
            error = (
                "Customer phone is empty or invalid."
            )

            _set_trip_sms_result(
                trip.pk,
                event,
                False,
                error,
            )

            return {
                "ok": False,
                "error": error,
            }

        # This rendered message now preserves all Enter presses
        # and blank lines from the admin template.
        message = render_trip_sms(
            template.message,
            trip,
        )

        if not message:
            error = "The automatic SMS message is empty."

            _set_trip_sms_result(
                trip.pk,
                event,
                False,
                error,
            )

            return {
                "ok": False,
                "error": error,
            }

        sender = _get_sender(template)

        source_addr = (
            sender.name
            if sender
            else getattr(
                settings,
                "BEEM_SENDER_ID",
                "",
            )
        )

        contact = _sync_contact(
            trip,
            created_by=created_by,
        )

        # Save an SMS log before contacting Beem.
        sms_log = SMSMessage.objects.create(
            contact=contact,
            sender_id=sender,
            dest_addr=phone,
            message=message,
            status="PENDING",
            created_by=created_by,
        )

        result = send_sms_batch(
            recipients=[
                {
                    "recipient_id": 1,
                    "dest_addr": phone,
                }
            ],
            message=message,
            source_addr=source_addr,
        )

        status_code = int(
            result.get("status_code", 0) or 0
        )

        payload = (
            result.get("json")
            or {}
        )

        request_succeeded = _response_is_success(
            status_code,
            payload,
        )

        sms_log.response_raw = payload
        sms_log.request_id = (
            _extract_request_id(payload)
            or None
        )
        sms_log.recipient_id = (
            _extract_recipient_id(payload)
            or None
        )

        if request_succeeded:
            sms_log.status = "SENT"
            sms_log.sent_at = timezone.now()
            sms_log.error_text = ""

            sms_log.save(
                update_fields=[
                    "response_raw",
                    "request_id",
                    "recipient_id",
                    "status",
                    "sent_at",
                    "error_text",
                ]
            )

            _set_trip_sms_result(
                trip.pk,
                event,
                True,
            )

            return {
                "ok": True,
                "status_code": status_code,
                "response": payload,
                "message_id": sms_log.pk,
            }

        error = _extract_error_message(
            payload
        )

        sms_log.status = "FAILED"
        sms_log.error_text = error[:2000]

        sms_log.save(
            update_fields=[
                "response_raw",
                "request_id",
                "recipient_id",
                "status",
                "error_text",
            ]
        )

        _set_trip_sms_result(
            trip.pk,
            event,
            False,
            error,
        )

        return {
            "ok": False,
            "status_code": status_code,
            "error": error,
            "message_id": sms_log.pk,
        }

    except Trip.DoesNotExist:
        error = "The selected transport trip does not exist."

        logger.warning(
            "Automatic SMS skipped because trip=%s does not exist.",
            trip_id,
        )

        return {
            "ok": False,
            "error": error,
        }

    except TransportSMSTemplate.DoesNotExist:
        error = (
            "The automatic transport SMS template does not exist."
        )

        logger.exception(
            "Automatic transport SMS template missing for event=%s.",
            event,
        )

        try:
            _set_trip_sms_result(
                trip_id,
                event,
                False,
                error,
            )
        except Exception:
            pass

        return {
            "ok": False,
            "error": error,
        }

    except Exception as exc:
        error = str(exc)

        logger.exception(
            "Automatic transport SMS failed for trip=%s event=%s",
            trip_id,
            event,
        )

        try:
            _set_trip_sms_result(
                trip_id,
                event,
                False,
                error,
            )
        except Exception:
            pass

        return {
            "ok": False,
            "error": error,
        }


def send_boss_trip_sms(
    trip_id: int,
    created_by_id: int | None = None,
    force: bool = False,
):
    """
    Send the editable boss notification when a new pending trip is created.

    The recipient number comes from TRANSPORT_BOSS_PHONE.
    The message, Sender ID and active/disabled state come from the
    New Trip Notification / Boss template on the transport SMS page.
    """
    try:
        ensure_default_transport_templates()

        trip = (
            Trip.objects
            .select_related(
                "vehicle",
                "driver__user",
                "branch",
            )
            .get(pk=trip_id)
        )

        if trip.status != Trip.STATUS_PENDING:
            return {
                "ok": True,
                "skipped": True,
                "reason": "Boss SMS applies only to pending trips.",
            }

        boss_phone = normalize_phone(
            getattr(
                settings,
                "TRANSPORT_BOSS_PHONE",
                "",
            )
        )

        if not boss_phone:
            return {
                "ok": False,
                "skipped": True,
                "error": (
                    "TRANSPORT_BOSS_PHONE is not configured "
                    "or the phone number is invalid."
                ),
            }

        template = TransportSMSTemplate.objects.get(
            event=TransportSMSTemplate.EVENT_BOSS_PENDING
        )

        if not template.is_active:
            return {
                "ok": True,
                "skipped": True,
                "reason": "Boss notification template is disabled.",
            }

        message = render_boss_trip_sms(
            template.message,
            trip,
        )

        if not message:
            return {
                "ok": False,
                "error": "The boss notification message is empty.",
            }

        # Prevent accidental duplicate sends of the same rendered message.
        if not force:
            already_sent = SMSMessage.objects.filter(
                dest_addr=boss_phone,
                message=message,
                status__in=["PENDING", "SENT"],
            ).exists()

            if already_sent:
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": (
                        "Boss notification for this trip "
                        "was already created."
                    ),
                }

        created_by = None

        if created_by_id:
            created_by = User.objects.filter(
                pk=created_by_id
            ).first()

        sender = _get_sender(template)

        source_addr = (
            sender.name
            if sender
            else getattr(
                settings,
                "BEEM_SENDER_ID",
                "",
            )
        )

        sms_log = SMSMessage.objects.create(
            contact=None,
            sender_id=sender,
            dest_addr=boss_phone,
            message=message,
            status="PENDING",
            created_by=created_by,
        )

        result = send_sms_batch(
            recipients=[
                {
                    "recipient_id": 1,
                    "dest_addr": boss_phone,
                }
            ],
            message=message,
            source_addr=source_addr,
        )

        status_code = int(
            result.get("status_code", 0) or 0
        )

        payload = result.get("json") or {}

        request_succeeded = _response_is_success(
            status_code,
            payload,
        )

        sms_log.response_raw = payload
        sms_log.request_id = (
            _extract_request_id(payload)
            or None
        )
        sms_log.recipient_id = (
            _extract_recipient_id(payload)
            or None
        )

        if request_succeeded:
            sms_log.status = "SENT"
            sms_log.sent_at = timezone.now()
            sms_log.error_text = ""

            sms_log.save(
                update_fields=[
                    "response_raw",
                    "request_id",
                    "recipient_id",
                    "status",
                    "sent_at",
                    "error_text",
                ]
            )

            return {
                "ok": True,
                "status_code": status_code,
                "response": payload,
                "message_id": sms_log.pk,
            }

        error = _extract_error_message(payload)

        sms_log.status = "FAILED"
        sms_log.error_text = error[:2000]

        sms_log.save(
            update_fields=[
                "response_raw",
                "request_id",
                "recipient_id",
                "status",
                "error_text",
            ]
        )

        return {
            "ok": False,
            "status_code": status_code,
            "error": error,
            "message_id": sms_log.pk,
        }

    except Trip.DoesNotExist:
        return {
            "ok": False,
            "error": "The selected transport trip does not exist.",
        }

    except TransportSMSTemplate.DoesNotExist:
        error = (
            "The editable boss SMS template does not exist. "
            "Open the Automatic Transport SMS page once to create it."
        )

        logger.exception(
            "Boss transport SMS template is missing."
        )

        return {
            "ok": False,
            "error": error,
        }

    except Exception as exc:
        logger.exception(
            "Boss transport notification failed for trip=%s",
            trip_id,
        )

        return {
            "ok": False,
            "error": str(exc),
        }

# =========================================================
# BOOKING SMS RENDERING
# =========================================================

def render_booking_sms(
    template_text: str,
    booking: Booking,
) -> str:
    """
    Replace booking placeholders while preserving paragraphs and blank lines.
    """
    local_booking_datetime = timezone.localtime(
        booking.booking_datetime
    )

    customer_name = str(
        booking.customer_name
        or "Mteja"
    ).strip()

    customer_phone = normalize_phone(
        booking.customer_phone
    )

    branch = getattr(
        booking,
        "branch",
        None,
    )

    branch_name = (
        str(
            getattr(branch, "name", "")
            or branch
        ).strip()
        if branch
        else "Haijatajwa"
    )

    destination = str(
        booking.destination
        or "Haijatajwa"
    ).strip()

    company_phone = str(
        getattr(
            settings,
            "TRANSPORT_SUPPORT_PHONE",
            "0780850748",
        )
        or ""
    ).strip()

    boss_name = str(
        getattr(
            settings,
            "TRANSPORT_BOSS_NAME",
            "Kilasi",
        )
        or "Kilasi"
    ).strip()

    booking_datetime_text = (
        local_booking_datetime.strftime(
            "%d/%m/%Y %H:%M"
        )
    )

    values = {
        "name": customer_name,
        "first_name": _first_name(customer_name),
        "phone": customer_phone,
        "origin": str(
            booking.pickup_area
            or ""
        ).strip(),
        "destination": destination,
        "booking_datetime": booking_datetime_text,
        "trip_date": booking_datetime_text,
        "company_phone": company_phone,
        "boss_name": boss_name,
        "booking_id": booking.pk,
        "branch": branch_name,
    }

    message = _normalize_message_line_breaks(
        template_text
    )

    for key, value in values.items():
        replacement = str(value or "")

        supported_tokens = (
            f"{{{{ {key} }}}}",
            f"{{{{{key}}}}}",
            f"{{{key}}}",
            f"[{key}]",
        )

        for token in supported_tokens:
            message = message.replace(
                token,
                replacement,
            )

    return _normalize_message_line_breaks(
        message
    )


# =========================================================
# BOOKING CUSTOMER CONTACT SYNC
# =========================================================

def _sync_booking_contact(
    booking: Booking,
    created_by=None,
):
    """
    Save or update the booking customer in the existing SMS contacts.
    """
    phone = normalize_phone(
        booking.customer_phone
    )

    if not phone:
        return None

    customer_name = str(
        booking.customer_name
        or "Transport Customer"
    ).strip()

    group, _created = ContactGroup.objects.get_or_create(
        name="Transport Customers",
        defaults={
            "description": (
                "Customers collected automatically "
                "from transport trips and bookings."
            ),
        },
    )

    contact = Contact.objects.filter(
        phone=phone
    ).first()

    if contact:
        changed_fields = []

        weak_names = {
            "",
            "customer",
            "mteja",
            "transport customer",
            "walk-in",
            "unknown",
            "n/a",
            "-",
            "—",
        }

        current_name = str(
            contact.name
            or ""
        ).strip()

        if (
            customer_name
            and current_name.lower() in weak_names
            and current_name != customer_name
        ):
            contact.name = customer_name
            changed_fields.append("name")

        if not contact.is_active:
            contact.is_active = True
            changed_fields.append("is_active")

        if changed_fields:
            changed_fields.append("updated_at")
            contact.save(
                update_fields=changed_fields
            )

    else:
        contact = Contact.objects.create(
            name=customer_name,
            phone=phone,
            is_active=True,
            notes="Auto-saved from a transport booking.",
            created_by=created_by,
        )

    contact.groups.add(group)

    return contact


# =========================================================
# UPDATE BOOKING SMS RESULT
# =========================================================

def _booking_sms_fields(event: str):
    field_map = {
        TransportSMSTemplate.EVENT_BOOKING_CONFIRMED: {
            "sent": "confirmation_sms_sent",
            "sent_at": "confirmation_sms_sent_at",
            "error": "confirmation_sms_error",
        },
        TransportSMSTemplate.EVENT_BOOKING_BOSS: {
            "sent": "boss_sms_sent",
            "sent_at": "boss_sms_sent_at",
            "error": "boss_sms_error",
        },
        TransportSMSTemplate.EVENT_BOOKING_REMINDER: {
            "sent": "reminder_sent",
            "sent_at": "reminder_sent_at",
            "error": "reminder_sms_error",
        },
    }

    return field_map.get(event)


def _set_booking_sms_result(
    booking_id: int,
    event: str,
    ok: bool,
    error: str = "",
):
    """
    Store the result of a booking confirmation, boss alert or reminder.
    """
    fields = _booking_sms_fields(event)

    if not fields:
        return

    now = timezone.now()

    updates = {
        fields["error"]: (
            ""
            if ok
            else str(error or "")[:2000]
        ),
    }

    if ok:
        updates[fields["sent"]] = True
        updates[fields["sent_at"]] = now

    if event == TransportSMSTemplate.EVENT_BOOKING_REMINDER:
        updates["reminder_last_attempt_at"] = now

    Booking.objects.filter(
        pk=booking_id
    ).update(**updates)


# =========================================================
# AUTOMATIC BOOKING SMS SENDER
# =========================================================

def send_booking_sms(
    booking_id: int,
    event: str,
    created_by_id: int | None = None,
    force: bool = False,
):
    """
    Send one automatic booking SMS and record it in SMSMessage.

    Supported events:
    - booking_confirmed
    - booking_boss
    - booking_reminder
    """
    allowed_events = {
        TransportSMSTemplate.EVENT_BOOKING_CONFIRMED,
        TransportSMSTemplate.EVENT_BOOKING_BOSS,
        TransportSMSTemplate.EVENT_BOOKING_REMINDER,
    }

    if event not in allowed_events:
        return {
            "ok": False,
            "skipped": True,
            "reason": "Unknown booking SMS event.",
        }

    try:
        ensure_default_transport_templates()

        booking = (
            Booking.objects
            .select_related(
                "branch",
                "created_by",
            )
            .get(pk=booking_id)
        )

        if booking.status == Booking.STATUS_CANCELLED:
            return {
                "ok": False,
                "skipped": True,
                "reason": (
                    "SMS was not sent because "
                    "the booking is cancelled."
                ),
            }

        fields = _booking_sms_fields(event)

        if (
            fields
            and getattr(
                booking,
                fields["sent"],
                False,
            )
            and not force
        ):
            return {
                "ok": True,
                "skipped": True,
                "reason": "This booking SMS was already sent.",
            }

        now = timezone.now()

        if (
            event
            == TransportSMSTemplate.EVENT_BOOKING_REMINDER
            and not force
        ):
            if booking.status != Booking.STATUS_PENDING:
                return {
                    "ok": False,
                    "skipped": True,
                    "reason": (
                        "Only pending bookings receive reminders."
                    ),
                }

            if booking.booking_datetime <= now:
                return {
                    "ok": False,
                    "skipped": True,
                    "reason": "The booking time has already passed.",
                }

            if booking.booking_datetime > now + timedelta(hours=1):
                return {
                    "ok": False,
                    "skipped": True,
                    "reason": "The booking reminder is not due yet.",
                }

        template = TransportSMSTemplate.objects.get(
            event=event
        )

        if not template.is_active:
            return {
                "ok": True,
                "skipped": True,
                "reason": "This automatic SMS template is disabled.",
            }

        is_boss_message = (
            event
            == TransportSMSTemplate.EVENT_BOOKING_BOSS
        )

        if is_boss_message:
            phone = normalize_phone(
                getattr(
                    settings,
                    "TRANSPORT_BOSS_PHONE",
                    "",
                )
            )
        else:
            phone = normalize_phone(
                booking.customer_phone
            )

        if not phone:
            error = (
                "Boss phone is missing or invalid."
                if is_boss_message
                else "Customer phone is missing or invalid."
            )

            _set_booking_sms_result(
                booking.pk,
                event,
                False,
                error,
            )

            return {
                "ok": False,
                "error": error,
            }

        message = render_booking_sms(
            template.message,
            booking,
        )

        if not message:
            error = "The automatic booking SMS message is empty."

            _set_booking_sms_result(
                booking.pk,
                event,
                False,
                error,
            )

            return {
                "ok": False,
                "error": error,
            }

        created_by = None

        if created_by_id:
            created_by = User.objects.filter(
                pk=created_by_id
            ).first()

        if not created_by:
            created_by = booking.created_by

        sender = _get_sender(template)

        source_addr = (
            sender.name
            if sender
            else getattr(
                settings,
                "BEEM_SENDER_ID",
                "",
            )
        )

        contact = None

        if not is_boss_message:
            contact = _sync_booking_contact(
                booking,
                created_by=created_by,
            )

        sms_log = SMSMessage.objects.create(
            contact=contact,
            sender_id=sender,
            dest_addr=phone,
            message=message,
            status="PENDING",
            created_by=created_by,
        )

        result = send_sms_batch(
            recipients=[
                {
                    "recipient_id": 1,
                    "dest_addr": phone,
                }
            ],
            message=message,
            source_addr=source_addr,
        )

        status_code = int(
            result.get("status_code", 0)
            or 0
        )

        payload = (
            result.get("json")
            or {}
        )

        request_succeeded = _response_is_success(
            status_code,
            payload,
        )

        sms_log.response_raw = payload
        sms_log.request_id = (
            _extract_request_id(payload)
            or None
        )
        sms_log.recipient_id = (
            _extract_recipient_id(payload)
            or None
        )

        if request_succeeded:
            sms_log.status = "SENT"
            sms_log.sent_at = timezone.now()
            sms_log.error_text = ""

            sms_log.save(
                update_fields=[
                    "response_raw",
                    "request_id",
                    "recipient_id",
                    "status",
                    "sent_at",
                    "error_text",
                ]
            )

            _set_booking_sms_result(
                booking.pk,
                event,
                True,
            )

            return {
                "ok": True,
                "status_code": status_code,
                "response": payload,
                "message_id": sms_log.pk,
            }

        error = _extract_error_message(
            payload
        )

        sms_log.status = "FAILED"
        sms_log.error_text = error[:2000]

        sms_log.save(
            update_fields=[
                "response_raw",
                "request_id",
                "recipient_id",
                "status",
                "error_text",
            ]
        )

        _set_booking_sms_result(
            booking.pk,
            event,
            False,
            error,
        )

        return {
            "ok": False,
            "status_code": status_code,
            "error": error,
            "message_id": sms_log.pk,
        }

    except Booking.DoesNotExist:
        return {
            "ok": False,
            "error": "The selected booking does not exist.",
        }

    except TransportSMSTemplate.DoesNotExist:
        error = (
            "The booking SMS template does not exist. "
            "Open Automatic Transport SMS once to create it."
        )

        logger.exception(
            "Booking SMS template missing for event=%s.",
            event,
        )

        return {
            "ok": False,
            "error": error,
        }

    except Exception as exc:
        error = str(exc)

        logger.exception(
            "Automatic booking SMS failed "
            "for booking=%s event=%s",
            booking_id,
            event,
        )

        try:
            _set_booking_sms_result(
                booking_id,
                event,
                False,
                error,
            )
        except Exception:
            pass

        return {
            "ok": False,
            "error": error,
        }


# =========================================================
# DUE BOOKING REMINDERS
# =========================================================

def process_due_booking_reminders(
    limit: int = 200,
):
    """
    Send reminders for pending bookings taking place within the next hour.

    Failed reminders may be retried after five minutes.
    """
    now = timezone.now()
    one_hour_from_now = now + timedelta(hours=1)
    retry_before = now - timedelta(minutes=5)

    booking_ids = list(
        Booking.objects.filter(
            status=Booking.STATUS_PENDING,
            reminder_sent=False,
            booking_datetime__gt=now,
            booking_datetime__lte=one_hour_from_now,
        )
        .filter(
            Q(reminder_last_attempt_at__isnull=True)
            | Q(
                reminder_last_attempt_at__lte=retry_before
            )
        )
        .order_by("booking_datetime")
        .values_list(
            "pk",
            flat=True,
        )[:limit]
    )

    sent_count = 0
    failed_count = 0
    skipped_count = 0

    for booking_id in booking_ids:
        result = send_booking_sms(
            booking_id=booking_id,
            event=(
                TransportSMSTemplate
                .EVENT_BOOKING_REMINDER
            ),
        )

        if result.get("ok") and not result.get("skipped"):
            sent_count += 1
        elif result.get("skipped"):
            skipped_count += 1
        else:
            failed_count += 1

    return {
        "checked": len(booking_ids),
        "sent": sent_count,
        "failed": failed_count,
        "skipped": skipped_count,
    }

