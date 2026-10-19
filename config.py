import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(32)
    MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/FIS')
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB max upload
