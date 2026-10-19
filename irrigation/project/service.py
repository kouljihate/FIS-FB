"""Project aggregate: queries, ownership, statistics and deletion.

A project is the root of the documented hierarchy
``lands > project > sectors > zones > rows > trees``.  Visibility is either
global (``full_admin``) or restricted to the owner plus an explicit member list.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from ..constants import COLLECTIONS
from ..extensions import mongo
from ..utils.crud import remove_all_for_project
from ..utils.serialization import oid

CHILD_COLLECTIONS = ("sectors", "zones", "rows", "trees", "pipes",
                     "fittings", "water_resources", "estimations")


def _projects():
    return mongo.db[COLLECTIONS["projects"]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def list_projects_for(user) -> list[dict[str, Any]]:
    """Projects the user may open, newest activity last."""
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    if user.is_superuser:
        query: dict[str, Any] = {}
    else:
        uid = oid(user.id)
        query = {"$or": [{"owner_id": uid}, {"members": uid}]}
    return list(_projects().find(query).sort("name", 1))


def get_project(project_id: ObjectId | str | None) -> dict[str, Any] | None:
    pid = oid(project_id)
    if pid is None:
        return None
    return _projects().find_one({"_id": pid})


def can_access(user, project: dict[str, Any] | None) -> bool:
    if project is None or user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    uid = oid(user.id)
    return project.get("owner_id") == uid or uid in (project.get("members") or [])


def can_delete(user, project: dict[str, Any] | None) -> bool:
    """Only the owner or a full admin may remove a project.

    The project list uses this to decide whether to offer the Remove button, so
    the card never suggests an action that the route would refuse.
    """
    if project is None or user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    uid = oid(getattr(user, "id", None))
    return uid is not None and project.get("owner_id") == uid


def record_map_file(project_id: ObjectId | str, info: dict[str, Any]) -> None:
    """Store a project's linked map under ``kml_files``, replacing any older one.

    Keeping the metadata in its own collection is what lets a project teardown
    remove it through the ordinary cascade (``kml_files`` is already part of
    ``remove_all_for_project``).
    """
    pid = oid(project_id)
    if pid is None:
        return
    files = mongo.db[COLLECTIONS["kml_files"]]
    files.delete_many({"project_id": pid})
    files.insert_one({**info, "project_id": pid})


def create_project(user, name: str, *, description: str = "",
                   land: dict[str, Any] | None = None) -> ObjectId:
    label = (name or "").strip()
    if not label:
        raise ValueError("a project name is required")
    doc: dict[str, Any] = {
        "name": label,
        "description": (description or "").strip(),
        "owner_id": oid(user.id),
        "members": [],
        "land": land or {},
        "settings": {
            "sector_target_ha": None,
            "zones_per_sector": None,
            "pipe_diameters_mm": None,
        },
        "created_at": _now(),
        "updated_at": _now(),
    }
    return _projects().insert_one(doc).inserted_id


def update_project(project_id: ObjectId | str, fields: dict[str, Any]) -> bool:
    pid = oid(project_id)
    if pid is None:
        return False
    payload = {k: v for k, v in fields.items() if k not in {"_id", "owner_id"}}
    if not payload:
        return False
    payload["updated_at"] = _now()
    return _projects().update_one({"_id": pid}, {"$set": payload}).matched_count == 1


def delete_project(project_id: ObjectId | str) -> dict[str, int]:
    """Delete a project and every record beneath it."""
    pid = oid(project_id)
    if pid is None:
        return {}
    return remove_all_for_project(pid, include_parent=True)


def stats(project_id: ObjectId | str) -> dict[str, int]:
    """Row counts per child collection, for the project cards and dashboard."""
    pid = oid(project_id)
    if pid is None:
        return {}
    counts = {name: mongo.db[COLLECTIONS.get(name, name)].count_documents(
        {"project_id": pid}) for name in CHILD_COLLECTIONS}
    counts["total_records"] = sum(counts.values())
    return counts
