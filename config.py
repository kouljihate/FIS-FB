import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Local settings (API keys, SMTP passwords) stay in .env, which git ignores.
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, '.env'))
except ImportError:  # python-dotenv is optional at runtime
    pass


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(32)
    MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/FIS')
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB max upload

    # Outgoing mail (temporary passwords). Leave SMTP_HOST empty on a local
    # install: messages are then written to the application log instead.
    SMTP_HOST = os.environ.get('SMTP_HOST')
    SMTP_PORT = int(os.environ.get('SMTP_PORT') or 587)
    SMTP_USER = os.environ.get('SMTP_USER')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')
    SMTP_FROM = os.environ.get('SMTP_FROM') or os.environ.get('SMTP_USER')
    SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', '1') != '0'

    # WhatsApp (Blueticks): temporary passwords and project messages.  The key
    # is minted at https://dev.blueticks.co; without it messages go to the log.
    BLUETICKS_API_KEY = os.environ.get('BLUETICKS_API_KEY')
    BLUETICKS_API_URL = os.environ.get('BLUETICKS_API_URL') or 'https://api.blueticks.co/v1'
    # Local numbers (without a country prefix) are completed with this code.
    WHATSAPP_DEFAULT_COUNTRY_CODE = os.environ.get('WHATSAPP_DEFAULT_COUNTRY_CODE') or '216'
    WHATSAPP_TIMEOUT = int(os.environ.get('WHATSAPP_TIMEOUT') or 15)

    # --- Deployment / microservice runtime -------------------------------- #
    # Which service this process is: it travels in every log line and in the
    # events it publishes, so one container's output is identifiable.
    SERVICE_NAME = os.environ.get('SERVICE_NAME') or 'fis-web'

    # Domain events (RabbitMQ).  EVENTS_ENABLED=0 keeps the app fully working
    # on a machine without a broker: nothing is published and nothing fails.
    EVENTS_ENABLED = os.environ.get('EVENTS_ENABLED', '1') != '0'
    RABBITMQ_URL = (os.environ.get('RABBITMQ_URL')
                    or os.environ.get('CLOUDAMQP_URL')
                    or 'amqp://fis:fis@localhost:5672/%2F')
    EVENTS_EXCHANGE = os.environ.get('EVENTS_EXCHANGE') or 'fis.events'
    EVENTS_DEAD_EXCHANGE = os.environ.get('EVENTS_DEAD_EXCHANGE') or 'fis.dead'
    EVENTS_AUDIT_QUEUE = os.environ.get('EVENTS_AUDIT_QUEUE') or 'fis.audit'
    EVENTS_QUEUE_MAX = int(os.environ.get('EVENTS_QUEUE_MAX') or 2000)
    EVENTS_CONNECT_TIMEOUT = float(os.environ.get('EVENTS_CONNECT_TIMEOUT') or 3)

    # MongoClient pool/timings: a short server-selection timeout turns an
    # unreachable database into a fast 503 instead of a 30 s hung request.
    MONGO_MAX_POOL_SIZE = int(os.environ.get('MONGO_MAX_POOL_SIZE') or 50)
    MONGO_TIMEOUT_MS = int(os.environ.get('MONGO_TIMEOUT_MS') or 5000)

    # Behind a container/load balancer: static assets can be cached hard, and
    # the session cookie is pinned to the deployment's TLS settings.
    STATIC_CACHE_SECONDS = int(os.environ.get('STATIC_CACHE_SECONDS') or 3600)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get('SESSION_COOKIE_SAMESITE') or 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'
