import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


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
