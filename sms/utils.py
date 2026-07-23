import json
import logging

import requests
from django.conf import settings
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

BEEM_SEND_URL = getattr(settings, "BEEM_API_URL_SEND", "https://apisms.beem.africa/v1/send")
BEEM_BALANCE_URL = getattr(
    settings,
    "BEEM_BALANCE_URL",
    "https://apisms.beem.africa/public/v1/vendors/balance",
)
BEEM_DLR_URL = getattr(
    settings,
    "BEEM_DLR_URL",
    "https://dlrapi.beem.africa/public/v1/delivery-reports",
)

API_KEY = getattr(settings, "BEEM_API_KEY", "")
SECRET_KEY = getattr(settings, "BEEM_SECRET_KEY", "")
DEFAULT_SENDER_ID = getattr(settings, "BEEM_SENDER_ID", "")


def _auth():
    return HTTPBasicAuth(API_KEY, SECRET_KEY)


def normalize_phone(phone: str) -> str:
    value = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if value.startswith("0"):
        value = "255" + value[1:]
    elif value.startswith("7") and len(value) == 9:
        value = "255" + value
    return value


def send_sms_batch(recipients, message, source_addr=None):
    source = source_addr or DEFAULT_SENDER_ID
    payload = {
        "source_addr": source,
        "encoding": 0,
        "message": str(message or "").strip(),
        "recipients": recipients,
    }

    if not API_KEY or not SECRET_KEY:
        return {
            "status_code": 0,
            "json": {
                "successful": False,
                "error": "BEEM_API_KEY or BEEM_SECRET_KEY is not configured.",
            },
        }
    if not source:
        return {
            "status_code": 0,
            "json": {
                "successful": False,
                "error": "BEEM_SENDER_ID or an active SenderID is not configured.",
            },
        }

    try:
        logger.info("BEEM OUTBOUND PAYLOAD: %s", json.dumps(payload, ensure_ascii=False))
    except Exception:
        logger.info("BEEM OUTBOUND PAYLOAD RAW: %s", payload)

    try:
        response = requests.post(BEEM_SEND_URL, json=payload, auth=_auth(), timeout=30)
        try:
            response_json = response.json()
        except Exception:
            response_json = {"http_status": response.status_code, "text": response.text}
        logger.info("BEEM RESPONSE status=%s body=%s", response.status_code, response_json)
        return {"status_code": response.status_code, "json": response_json}
    except requests.RequestException as exc:
        logger.exception("BEEM SEND ERROR")
        return {"status_code": 0, "json": {"successful": False, "error": str(exc)}}


def check_balance():
    try:
        response = requests.get(BEEM_BALANCE_URL, auth=_auth(), timeout=20)
        try:
            response_json = response.json()
        except Exception:
            response_json = {"http_status": response.status_code, "text": response.text}
        return {"status_code": response.status_code, "json": response_json}
    except requests.RequestException as exc:
        logger.exception("BEEM BALANCE ERROR")
        return {"status_code": 0, "json": {"successful": False, "error": str(exc)}}


def get_delivery_report(dest_addr, request_id):
    params = {"dest_addr": dest_addr, "request_id": request_id}
    try:
        response = requests.get(BEEM_DLR_URL, params=params, auth=_auth(), timeout=20)
        try:
            response_json = response.json()
        except Exception:
            response_json = {"http_status": response.status_code, "text": response.text}
        return {"status_code": response.status_code, "json": response_json}
    except requests.RequestException as exc:
        logger.exception("BEEM DLR ERROR")
        return {"status_code": 0, "json": {"successful": False, "error": str(exc)}}
