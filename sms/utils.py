import json
import logging
import requests
from requests.auth import HTTPBasicAuth
from django.conf import settings

logger = logging.getLogger(__name__)

BEEM_SEND_URL = getattr(settings, "BEEM_API_URL_SEND", "https://apisms.beem.africa/v1/send")
BEEM_BALANCE_URL = getattr(settings, "BEEM_BALANCE_URL", "https://apisms.beem.africa/public/v1/vendors/balance")
BEEM_DLR_URL = getattr(settings, "BEEM_DLR_URL", "https://dlrapi.beem.africa/public/v1/delivery-reports")

API_KEY = settings.BEEM_API_KEY
SECRET_KEY = settings.BEEM_SECRET_KEY
DEFAULT_SENDER_ID = settings.BEEM_SENDER_ID


def _auth():
    return HTTPBasicAuth(API_KEY, SECRET_KEY)


def normalize_phone(phone: str) -> str:
    phone = (phone or "").strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("0"):
        phone = "255" + phone[1:]
    return phone


def send_sms_batch(recipients, message, source_addr=None):
    source = source_addr or DEFAULT_SENDER_ID
    payload = {
        "source_addr": source,
        "encoding": 0,
        "message": message,
        "recipients": recipients,
    }

    try:
        logger.info("BEEM OUTBOUND PAYLOAD: %s", json.dumps(payload, ensure_ascii=False))
    except Exception:
        logger.info("BEEM OUTBOUND PAYLOAD RAW: %s", payload)

    try:
        resp = requests.post(BEEM_SEND_URL, json=payload, auth=_auth(), timeout=30)
        try:
            response_json = resp.json()
        except Exception:
            response_json = {"http_status": resp.status_code, "text": resp.text}

        logger.info("BEEM RESPONSE status=%s body=%s", resp.status_code, response_json)
        return {"status_code": resp.status_code, "json": response_json}
    except requests.RequestException as exc:
        logger.exception("BEEM SEND ERROR")
        return {"status_code": 0, "json": {"successful": False, "error": str(exc)}}


def check_balance():
    try:
        resp = requests.get(BEEM_BALANCE_URL, auth=_auth(), timeout=20)
        try:
            response_json = resp.json()
        except Exception:
            response_json = {"http_status": resp.status_code, "text": resp.text}
        return {"status_code": resp.status_code, "json": response_json}
    except requests.RequestException as exc:
        logger.exception("BEEM BALANCE ERROR")
        return {"status_code": 0, "json": {"successful": False, "error": str(exc)}}


def get_delivery_report(dest_addr, request_id):
    params = {
        "dest_addr": dest_addr,
        "request_id": request_id,
    }
    try:
        resp = requests.get(BEEM_DLR_URL, params=params, auth=_auth(), timeout=20)
        try:
            response_json = resp.json()
        except Exception:
            response_json = {"http_status": resp.status_code, "text": resp.text}
        return {"status_code": resp.status_code, "json": response_json}
    except requests.RequestException as exc:
        logger.exception("BEEM DLR ERROR")
        return {"status_code": 0, "json": {"successful": False, "error": str(exc)}}