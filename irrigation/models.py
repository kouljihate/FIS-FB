"""Document models and the Flask-Login user adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .constants import COLLECTIONS, PROFILE_FULL_ADMIN
from .extensions import mongo


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(UserMixin):
    """User document wrapper.

    ``_id`` is stored as an ``ObjectId``; Flask-Login round-trips it through the
    session as a string, hence the explicit lookup in :meth:`get`.
    """

    def __init__(self, doc: dict[str, Any]):
        self.doc = doc
        self.id: str = str(doc["_id"])
        self.username: str = doc.get("username", "")
        self.email: str = doc.get("email", "")
        self.role: str = doc.get("role", PROFILE_FULL_ADMIN)
        self.active: bool = bool(doc.get("active", True))
        self.full_name: str = doc.get("full_name", "")

    # --- Flask-Login -------------------------------------------------------
    @property
    def is_active(self) -> bool:  # noqa: D401 - Flask-Login contract
        return self.active

    def get_id(self) -> str:
        return self.id

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    @property
    def is_superuser(self) -> bool:
        return self.role == PROFILE_FULL_ADMIN

    # --- persistence -------------------------------------------------------
    @staticmethod
    def collection():
        return mongo.db[COLLECTIONS["users"]]

    @classmethod
    def get(cls, user_id: str) -> "User | None":
        if not user_id or not ObjectId.is_valid(user_id):
            return None
        doc = cls.collection().find_one({"_id": ObjectId(user_id)})
        return cls(doc) if doc else None

    @classmethod
    def by_username(cls, username: str) -> "User | None":
        doc = cls.collection().find_one({"username": username.lower().strip()})
        return cls(doc) if doc else None

    @classmethod
    def count(cls) -> int:
        return cls.collection().count_documents({})

    @classmethod
    def create(
        cls,
        username: str,
        password: str,
        email: str = "",
        role: str = PROFILE_FULL_ADMIN,
        full_name: str = "",
    ) -> "User":
        doc = {
            "username": username.lower().strip(),
            "email": email.strip().lower(),
            "full_name": full_name.strip(),
            "password_hash": generate_password_hash(password),
            "role": role,
            "active": True,
            "created_at": utcnow(),
        }
        result = cls.collection().insert_one(doc)
        doc["_id"] = result.inserted_id
        return cls(doc)

    def check_password(self, password: str) -> bool:
        stored = self.doc.get("password_hash") or ""
        return bool(stored) and check_password_hash(stored, password)

    def set_password(self, password: str) -> None:
        self.collection().update_one(
            {"_id": ObjectId(self.id)},
            {"$set": {"password_hash": generate_password_hash(password),
                      "updated_at": utcnow()}},
        )

    def set_role(self, role: str) -> None:
        self.collection().update_one(
            {"_id": ObjectId(self.id)},
            {"$set": {"role": role, "updated_at": utcnow()}},
        )
        self.role = role

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "active": self.active,
        }
