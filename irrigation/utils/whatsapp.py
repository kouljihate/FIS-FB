"""Direct WhatsApp messages through Blueticks.

Blueticks (https://blueticks.co) sends from the WhatsApp number already linked
to the account, so no Meta business verification or message template is needed.
One message is one authenticated POST:

    POST {BLUETICKS_API_URL}/messages/{chat_id}
    Authorization: Bearer <BLUETICKS_API_KEY>
    {"type": "text", "text": "..."}

``chat_id`` is WhatsApp's addressing format: the phone number in international
form followed by ``@c.us`` (``21612345678@c.us``).

The key lives in ``BLUETICKS_API_KEY`` (``.env`` on a local install).  Without a
key the message is written to the application log instead, like the SMTP
fallback, so the temporary-password flow still works on a fresh install.
"""
import json
import re
import urllib.error
import urllib.request

from flask import current_app

#: Base URL of the REST API; every call below it starts with /v1.
DEFAULT_API_URL = 'https://api.blueticks.co/v1'


def whatsapp_configured():
    """True when an API key is available."""
    return bool((current_app.config.get('BLUETICKS_API_KEY') or '').strip())


def normalize_phone(raw, country_code=None):
    """Return a phone number as international digits, or ``None``.

    Accepts ``+216 12 345 678``, ``0021612345678`` and local forms such as
    ``12345678`` or ``01234567``.  A number written without an international
    prefix gets ``WHATSAPP_DEFAULT_COUNTRY_CODE`` (216, Tunisia, by default).
    """
    digits = re.sub(r'\D', '', str(raw or ''))
    if digits.startswith('00'):
        digits = digits[2:]

    if country_code is None:
        country_code = current_app.config.get('WHATSAPP_DEFAULT_COUNTRY_CODE') or ''
    prefix = re.sub(r'\D', '', str(country_code))
    if prefix:
        if digits.startswith('0'):
            digits = prefix + digits.lstrip('0')
        elif len(digits) <= 9:
            digits = prefix + digits

    if not 8 <= len(digits) <= 15:
        return None
    return digits


def chat_id(phone):
    """WhatsApp recipient identifier for a phone number."""
    return '%s@c.us' % phone


def _api_base():
    return (current_app.config.get('BLUETICKS_API_URL') or DEFAULT_API_URL).rstrip('/')


def _error_message(raw):
    """Human-readable message from a Blueticks error envelope."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return ''
    error = payload.get('error') if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return error.get('message') or error.get('code') or ''
    return ''


def send_whatsapp(phone, text):
    """Send one WhatsApp text message; returns ``(status, detail)``.

    ``status`` is ``'sent'``, ``'logged'`` (no API key: the message went to the
    log), ``'invalid'`` (no usable phone number or empty text) or ``'failed'``
    (the API refused the message or the network failed).
    """
    logger = current_app.logger

    digits = normalize_phone(phone)
    if not digits:
        return 'invalid', 'no usable phone number'

    text = (text or '').strip()
    if not text:
        return 'invalid', 'empty message'

    if not whatsapp_configured():
        logger.warning(
            'WhatsApp is not configured (BLUETICKS_API_KEY) — the message to '
            '+%s was not sent.\n%s', digits, text)
        return 'logged', 'BLUETICKS_API_KEY is not set'

    payload = json.dumps({'type': 'text', 'text': text},
                         ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        '%s/messages/%s' % (_api_base(), chat_id(digits)),
        data=payload,
        method='POST',
        headers={
            'Authorization': 'Bearer %s' % current_app.config['BLUETICKS_API_KEY'].strip(),
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
    )

    timeout = int(current_app.config.get('WHATSAPP_TIMEOUT') or 15)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode('utf-8', 'replace')
        logger.info('WhatsApp message sent to +%s: %s', digits, raw[:200])
        return 'sent', raw[:200]
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', 'replace')
        detail = _error_message(raw) or ('HTTP %s' % exc.code)
        logger.warning('WhatsApp message to +%s was refused (%s): %s',
                       digits, detail, raw[:500])
        return 'failed', detail
    except Exception:
        logger.exception(
            'Could not send the WhatsApp message to +%s — the message follows.\n%s',
            digits, text)
        return 'failed', 'network error'
