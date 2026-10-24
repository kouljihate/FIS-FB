"""Plain-text account e-mails over SMTP.

No third-party service is required.  Set ``SMTP_HOST``, ``SMTP_PORT``,
``SMTP_USER``, ``SMTP_PASSWORD`` and ``SMTP_FROM`` (``SMTP_USE_TLS=0`` turns
STARTTLS off) to send through any mail provider.

Without those settings — the usual case on a local install — the message is
written to the application log instead of being sent, so the temporary-password
flow can still be tested end to end and the administrator can read the password
from ``logs/irrigation.log``.
"""
import smtplib
from email.message import EmailMessage

from flask import current_app


def smtp_configured():
    """True when enough settings exist to attempt delivery."""
    config = current_app.config
    return bool(config.get('SMTP_HOST')
                and (config.get('SMTP_FROM') or config.get('SMTP_USER')))


def send_email(to_address, subject, body):
    """Send a plain-text mail; returns ``'sent'``, ``'logged'`` or ``'failed'``.

    ``'logged'`` means SMTP is not configured and the whole message (including
    the temporary password) went to the log instead.
    """
    logger = current_app.logger
    if not to_address:
        return 'failed'

    if not smtp_configured():
        logger.warning(
            'SMTP is not configured — e-mail to %s was not sent.\n'
            'Subject: %s\n%s', to_address, subject, body)
        return 'logged'

    config = current_app.config
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = config.get('SMTP_FROM') or config.get('SMTP_USER')
    message['To'] = to_address
    message.set_content(body)

    try:
        port = int(config.get('SMTP_PORT') or 587)
        with smtplib.SMTP(config['SMTP_HOST'], port, timeout=15) as smtp:
            if config.get('SMTP_USE_TLS', True):
                smtp.starttls()
            if config.get('SMTP_USER') and config.get('SMTP_PASSWORD'):
                smtp.login(config['SMTP_USER'], config['SMTP_PASSWORD'])
            smtp.send_message(message)
        return 'sent'
    except Exception:
        logger.exception(
            'Could not send e-mail to %s — the message follows so it is not lost.\n'
            'Subject: %s\n%s', to_address, subject, body)
        return 'failed'
