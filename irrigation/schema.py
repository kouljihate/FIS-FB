"""MongoDB schema for the two-collection FIS model.

Only two collections are used:

``user``
    id, full_name, username (login), email, phone, password_hash,
    profile (Admin / Project Admin / Project Owner), cin, photo.

``project``
    Land map, water sources, sector -> zone -> row -> tree hierarchy,
    pipes and components, reports and elevation models.

``ensure_schema`` creates both collections with JSON-schema validation and the
indexes the app relies on.  It is idempotent and safe to run on every start.
"""

from __future__ import annotations

from pymongo import ASCENDING
from pymongo.errors import CollectionInvalid, OperationFailure

ROLES = ("Admin", "Project Admin", "Project Owner")

_ID = {"bsonType": ["int", "long"]}

USER_SCHEMA = {
    "bsonType": "object",
    "required": ["id", "username", "email", "password_hash", "profile"],
    "properties": {
        "id": _ID,
        "full_name": {"bsonType": "string"},
        "username": {"bsonType": "string", "minLength": 1},
        "email": {"bsonType": "string"},
        "phone": {"bsonType": "string"},
        "password_hash": {"bsonType": "string"},
        "profile": {"enum": list(ROLES)},
        "cin": {"bsonType": "string"},
        "photo": {"bsonType": ["string", "null"]},
        "is_active": {"bsonType": "bool"},
        "created_at": {"bsonType": "date"},
        # Company agreement of a local admin (Project Admin profile).
        "company": {
            "bsonType": ["object", "null"],
            "properties": {
                "name": {"bsonType": "string"},
                "agreement_number": {"bsonType": "string"},
                "agreement_date": {"bsonType": "string"},
                "agreement_file": {"bsonType": ["string", "null"]},
            },
        },
    },
}

_SECTOR = {
    "bsonType": "object",
    "required": ["id"],
    "properties": {
        "id": _ID,
        "zones": {"bsonType": "array"},
    },
}
_ROW = {
    "bsonType": "object",
    "required": ["id"],
    "properties": {
        "id": _ID,
        "trees": {"bsonType": "array"},
    },
}
_ZONE = {
    "bsonType": "object",
    "required": ["id"],
    "properties": {
        "id": _ID,
        "rows": {"bsonType": "array", "items": _ROW},
    },
}
_SECTOR["properties"]["zones"] = {"bsonType": "array", "items": _ZONE}

PROJECT_SCHEMA = {
    "bsonType": "object",
    "required": ["id", "user_id", "name", "land", "water", "sectors", "network"],
    "properties": {
        "id": _ID,
        "user_id": _ID,
        "admin_user_id": {"bsonType": ["int", "long", "null"]},
        # Account of the landowner (Project Owner profile): the link that lets
        # that owner see the project, even when a Project Admin created it.
        "owner_user_id": {"bsonType": ["int", "long", "null"]},
        "name": {"bsonType": "string"},
        "location": {"bsonType": "string"},
        "description": {"bsonType": "string"},
        "status": {"bsonType": "string"},
        "land": {
            "bsonType": "object",
            "properties": {
                "map": {"bsonType": ["object", "null"]},
                "kml_file": {"bsonType": ["string", "null"]},
                "documents": {"bsonType": "array"},
            },
        },
        "water": {"bsonType": "array"},
        "sectors": {"bsonType": "array", "items": _SECTOR},
        "network": {
            "bsonType": "object",
            "required": ["pipes", "components"],
            "properties": {
                "pipes": {"bsonType": "array"},
                "components": {
                    "bsonType": "object",
                    "properties": {
                        "valves": {"bsonType": "array"},
                        "connectors": {"bsonType": "array"},
                        "reductions": {"bsonType": "array"},
                        "air_removal": {"bsonType": "array"},
                        "pressure_reduction": {"bsonType": "array"},
                        "terminators": {"bsonType": "array"},
                    },
                },
            },
        },
        "reports": {"bsonType": "object"},
        "elevation_models": {"bsonType": "array"},
        "spec": {"bsonType": "object"},
        "boundary": {"bsonType": ["object", "null"]},
        "water_source": {"bsonType": "object"},
        "kml_file": {"bsonType": ["string", "null"]},
        "kml_features": {"bsonType": "array"},
        "created_at": {"bsonType": "date"},
    },
}


def _ensure_collection(db, name, validator):
    options = {
        "validator": {"$jsonSchema": validator},
        "validationLevel": "moderate",
        "validationAction": "error",
    }
    if name not in db.list_collection_names():
        try:
            db.create_collection(name, **options)
        except CollectionInvalid:
            pass
        return
    try:
        db.command("collMod", name, **options)
    except OperationFailure:
        # Older servers may refuse one of the options; the collections and
        # indexes still exist, so the app can run.
        pass


def ensure_schema(db):
    """Create/refresh the two collections, validators and indexes."""
    _ensure_collection(db, "user", USER_SCHEMA)
    _ensure_collection(db, "project", PROJECT_SCHEMA)

    db.user.create_index([("id", ASCENDING)], unique=True, name="user_id_unique")
    db.user.create_index([("username", ASCENDING)], unique=True, name="username_unique")
    db.user.create_index(
        [("email", ASCENDING)],
        unique=True,
        name="email_unique",
        partialFilterExpression={"email": {"$type": "string"}},
    )
    db.project.create_index([("id", ASCENDING)], unique=True, name="project_id_unique")
    db.project.create_index([("user_id", ASCENDING)], name="project_owner")
    db.project.create_index([("owner_user_id", ASCENDING)],
                            name="project_owner_account", sparse=True)

    link_owner_accounts(db)

    return {
        "user": db.user.count_documents({}),
        "project": db.project.count_documents({}),
    }


def link_owner_accounts(db):
    """Link older projects to the Project Owner account that owns them.

    A project only carries the owner's name, e-mail and phone as free text in
    ``spec.owner``; a Project Owner can only open the projects linked to their
    account (``owner_user_id``).  Projects created before that link existed are
    matched against the Project Owner accounts by e-mail, then by full name, so
    their owner finds them again.  Idempotent: only projects without a link are
    looked at.
    """
    by_email = {}
    by_name = {}
    for owner in db.user.find({"profile": "Project Owner"},
                              {"id": 1, "email": 1, "full_name": 1}):
        email = str(owner.get("email") or "").strip().lower()
        if email:
            by_email.setdefault(email, owner["id"])
        name = str(owner.get("full_name") or "").strip().lower()
        if name:
            by_name.setdefault(name, owner["id"])
    if not by_email and not by_name:
        return 0

    linked = 0
    for project in db.project.find({"owner_user_id": None}, {"id": 1, "spec": 1}):
        owner_info = (project.get("spec") or {}).get("owner") or {}
        email = str(owner_info.get("email") or "").strip().lower()
        name = str(owner_info.get("name") or "").strip().lower()
        owner_id = by_email.get(email) or by_name.get(name)
        if owner_id is None:
            continue
        db.project.update_one({"id": project["id"]},
                              {"$set": {"owner_user_id": owner_id}})
        linked += 1
    return linked


def real_database(mongo):
    """Return the underlying PyMongo database (unwrap the embedded layer)."""
    db = mongo.db
    return getattr(db, "real", db)


if __name__ == "__main__":
    from irrigation import create_app
    from irrigation.extensions import mongo

    app = create_app()
    with app.app_context():
        counts = ensure_schema(real_database(mongo))
        print("FIS schema ready:", counts)
