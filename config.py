"""Application configuration.

Values come from the environment (optionally via a local ``.env`` file) and fall
back to safe development defaults.  The MongoDB database name defaults to
``fis_freebuff`` so this build is isolated from the other copies of the app that
may be running against the same MongoDB server.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_list_int(name: str, default: str) -> list[int]:
    raw = os.getenv(name, default)
    out: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(float(chunk)))
        except ValueError:
            continue
    return out or [int(x) for x in default.split(",")]


def _with_database(uri: str, dbname: str) -> str:
    """Return ``uri`` with ``dbname`` as its database path.

    Flask-PyMongo 3 derives ``mongo.db`` from the database named in the
    connection URI rather than from ``MONGO_DBNAME``, so the name has to be part
    of the URI.  An explicit database already present in the URI wins, and any
    query string (authSource and friends) is preserved.
    """
    if not uri:
        return uri
    scheme, separator, rest = uri.partition("://")
    if not separator:
        return uri
    host_part, _, path = rest.partition("/")
    db_part, query_separator, query = path.partition("?")
    if db_part.strip():
        return uri
    rebuilt = f"{scheme}://{host_part}/{dbname}"
    if query_separator:
        rebuilt = f"{rebuilt}?{query}"
    return rebuilt


class Config:
    """Base configuration shared by every environment."""

    # --- Flask -------------------------------------------------------------
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-only-secret-change-me")
    DEBUG: bool = _env_bool("FLASK_DEBUG", True)
    RUN_HOST: str = os.getenv("FLASK_RUN_HOST", "127.0.0.1")
    RUN_PORT: int = _env_int("FLASK_RUN_PORT", 5000)

    # --- MongoDB -----------------------------------------------------------
    MONGO_DBNAME: str = os.getenv("MONGO_DBNAME", "fis_freebuff")
    MONGO_SERVER_URI: str = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    #: Flask-PyMongo reads this and takes the database from its path.
    MONGO_URI: str = _with_database(MONGO_SERVER_URI, MONGO_DBNAME)
    MONGO_TIMEOUT_MS: int = _env_int("MONGO_TIMEOUT_MS", 3000)

    # --- filesystem --------------------------------------------------------
    UPLOAD_FOLDER: Path = BASE_DIR / "uploads"
    EXPORT_FOLDER: Path = BASE_DIR / "exports"
    IMPORT_FOLDER: Path = BASE_DIR / "imports"
    LOG_FOLDER: Path = BASE_DIR / "logs"
    MAX_CONTENT_LENGTH: int = 32 * 1024 * 1024  # 32 MiB KML/KMZ uploads

    # --- engineering defaults ---------------------------------------------
    DEFAULT_SECTOR_AREA_HA: float = _env_float("DEFAULT_SECTOR_AREA_HA", 1.0)
    DEFAULT_ZONES_PER_SECTOR: int = _env_int("DEFAULT_ZONES_PER_SECTOR", 3)
    DEFAULT_PIPE_DIAMETERS_MM: list[int] = _env_list_int(
        "DEFAULT_PIPE_DIAMETERS_MM", "90,63,32"
    )
    DEFAULT_DRIPPER_FLOW_LPH: float = _env_float("DEFAULT_DRIPPER_FLOW_LPH", 2.0)
    DEFAULT_DRIPPER_SPACING_M: float = _env_float("DEFAULT_DRIPPER_SPACING_M", 0.40)
    DEFAULT_LATERAL_SPACING_M: float = _env_float("DEFAULT_LATERAL_SPACING_M", 1.50)

    # --- i18n --------------------------------------------------------------
    DEFAULT_LANGUAGE: str = os.getenv("DEFAULT_LANGUAGE", "en")
    SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "ar")

    # --- profiles ----------------------------------------------------------
    PROFILES: tuple[str, ...] = ("full_admin", "admin_entrepreneur", "customer")

    # --- security ----------------------------------------------------------
    PASSWORD_MIN_LENGTH: int = 8
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"


class TestConfig(Config):
    """Configuration used by the test-suite.

    The test database is a separate name, so a test run can never touch the data
    the running application uses.
    """

    TESTING = True
    MONGO_DBNAME = os.getenv("TEST_MONGO_DBNAME", "fis_freebuff_test")
    MONGO_URI = _with_database(Config.MONGO_SERVER_URI, MONGO_DBNAME)


CONFIG_MAP = {
    "development": Config,
    "testing": TestConfig,
    "default": Config,
}


def get_config(name: str | None = None):
    """Return the configuration object for ``name`` (or the default)."""
    return CONFIG_MAP.get(name or "default", Config)
