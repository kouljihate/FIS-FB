"""Project-scoped CRUD for the hierarchical records.

Every domain collection (``sectors``, ``zones``, ``rows``, ``trees``, ``pipes``,
``fittings``, ``water_resources`` ...) stores flat documents carrying a
``project_id``.  Routing all access through these helpers means a project can
never be touched through another project's URL, which is the cheapest way to keep
multi-tenant data separate.

Records are addressed by ``(collection, project_id, item_id)``; child tables add
a parent reference such as ``sector_id`` inside ``fields``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from bson import ObjectId

from ..constants import COLLECTIONS
from ..extensions import mongo
from .serialization import oid

#: Deletes cascade down the documented hierarchy
#: lands > project > sectors > zones > rows > trees.
CASCADE: dict[str, tuple[tuple[str, str], ...]] = {
    "sectors": (("zones", "sector_id"), ("rows", "sector_id"), ("trees", "sector_id")),
    "zones": (("rows", "zone_id"), ("trees", "zone_id")),
    "rows": (("trees", "row_id"),),
}

DEFAULT_SORT = [("name", 1)]


class CrudError(ValueError):
    """Raised for invalid or conflicting record operations."""


def _collection(name: str):
    key = COLLECTIONS.get(name, name)
    return mongo.db[key]


def _stamp() -> datetime:
    return datetime.now(timezone.utc)


def _clean_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise CrudError("a name is required")
    if len(cleaned) > 120:
        raise CrudError("name is limited to 120 characters")
    return cleaned


def _name_is_free(collection: str, project_id: ObjectId, label: str, *,
                  kind: str | None = None,
                  exclude_id: ObjectId | None = None) -> bool:
    """Whether ``label`` is unused in ``collection`` for this project.

    The comparison is anchored and case-insensitive so "Sector 1" and "sector 1"
    collide.  ``label`` is the *record* name; the collection name is a separate
    argument, which is easy to conflate.
    """
    query: dict[str, Any] = {
        "project_id": project_id,
        "name": {"$regex": f"^{re.escape(label)}$", "$options": "i"},
    }
    if kind is not None:
        query["kind"] = kind
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    return _collection(collection).find_one(query, {"_id": 1}) is None


def list_items(
    name: str,
    project_id: ObjectId | str,
    *,
    kind: str | None = None,
    filters: dict[str, Any] | None = None,
    sort: list[tuple[str, int]] | None = None,
) -> list[dict[str, Any]]:
    """Return the project's records, optionally filtered by ``kind``/``filters``."""
    pid = oid(project_id)
    if pid is None:
        return []
    query: dict[str, Any] = {"project_id": pid}
    if kind:
        query["kind"] = kind
    if filters:
        query.update(filters)
    cursor = _collection(name).find(query).sort(sort or DEFAULT_SORT)
    return list(cursor)


def count_items(name: str, project_id: ObjectId | str, *,
                kind: str | None = None, filters: dict[str, Any] | None = None) -> int:
    pid = oid(project_id)
    if pid is None:
        return 0
    query: dict[str, Any] = {"project_id": pid}
    if kind:
        query["kind"] = kind
    if filters:
        query.update(filters)
    return _collection(name).count_documents(query)


def get_item(name: str, project_id: ObjectId | str,
             item_id: ObjectId | str) -> dict[str, Any] | None:
    pid, iid = oid(project_id), oid(item_id)
    if pid is None or iid is None:
        return None
    return _collection(name).find_one({"_id": iid, "project_id": pid})


def add_item(
    name: str,
    project_id: ObjectId | str,
    item_name: str,
    *,
    kind: str | None = None,
    unique_kind: bool = False,
    **fields: Any,
) -> ObjectId:
    """Insert a record and return its id.

    ``unique_kind`` scopes the duplicate-name check to the ``kind``, so that a
    "Well" and a "Basin" may both be called ``North``.
    """
    pid = oid(project_id)
    if pid is None:
        raise CrudError("unknown project")
    label = _clean_name(item_name)
    if not _name_is_free(name, pid, label, kind=kind if unique_kind else None):
        raise CrudError(f"a record named '{label}' already exists")

    doc: dict[str, Any] = {
        "project_id": pid,
        "name": label,
        "created_at": _stamp(),
        "updated_at": _stamp(),
    }
    if kind:
        doc["kind"] = kind
    doc.update(fields)
    return _collection(name).insert_one(doc).inserted_id


def update_item(name: str, project_id: ObjectId | str, item_id: ObjectId | str,
                fields: dict[str, Any]) -> bool:
    pid, iid = oid(project_id), oid(item_id)
    if pid is None or iid is None:
        return False
    payload = dict(fields)
    if "name" in payload:
        payload["name"] = _clean_name(payload["name"])
    payload["updated_at"] = _stamp()
    result = _collection(name).update_one({"_id": iid, "project_id": pid},
                                          {"$set": payload})
    return result.matched_count == 1


def rename_item(name: str, project_id: ObjectId | str, item_id: ObjectId | str,
                new_name: str, *, kind: str | None = None,
                unique_kind: bool = False) -> bool:
    pid, iid = oid(project_id), oid(item_id)
    if pid is None or iid is None:
        return False
    label = _clean_name(new_name)
    if not _name_is_free(name, pid, label, kind=kind if unique_kind else None,
                         exclude_id=iid):
        raise CrudError(f"a record named '{label}' already exists")
    result = _collection(name).update_one(
        {"_id": iid, "project_id": pid},
        {"$set": {"name": label, "updated_at": _stamp()}},
    )
    return result.matched_count == 1


def remove_item(name: str, project_id: ObjectId | str, item_id: ObjectId | str,
                *, cascade: bool = True) -> bool:
    """Delete a record, cascading to its children when it is a parent."""
    pid, iid = oid(project_id), oid(item_id)
    if pid is None or iid is None:
        return False
    result = _collection(name).delete_one({"_id": iid, "project_id": pid})
    if result.deleted_count != 1:
        return False

    if cascade:
        for child, field in CASCADE.get(name, ()):
            _collection(child).delete_many({"project_id": pid, field: iid})
    return True


def remove_many(name: str, project_id: ObjectId | str,
                item_ids: Iterable[ObjectId | str], *, cascade: bool = True) -> int:
    removed = 0
    for item_id in item_ids:
        if remove_item(name, project_id, item_id, cascade=cascade):
            removed += 1
    return removed


def remove_all_for_project(project_id: ObjectId | str, *, include_parent: bool = False) -> dict[str, int]:
    """Purge every child record of a project (used when deleting a project)."""
    pid = oid(project_id)
    if pid is None:
        return {}
    names = ["trees", "rows", "zones", "sectors", "pipes", "fittings",
             "water_resources", "estimations", "reports", "kml_files"]
    deleted: dict[str, int] = {}
    for name in names:
        deleted[name] = _collection(name).delete_many({"project_id": pid}).deleted_count
    if include_parent:
        # The project *is* the parent document: it is found by ``_id``, not by the
        # ``project_id`` key its children carry.  Deleting it by ``project_id``
        # matched nothing and left the project in the list after a remove.
        deleted["projects"] = _collection("projects").delete_many({"_id": pid}).deleted_count
    return deleted


def swap_items(name: str, project_id: ObjectId | str,
               first_id: ObjectId | str, second_id: ObjectId | str,
               fields: tuple[str, ...] = ("name",)) -> bool:
    """Exchange the named fields between two records of the same collection.

    Geometry swap is the important case: the two sectors keep their identity and
    their children, but trade boundaries with each other.
    """
    pid = oid(project_id)
    a, b = oid(first_id), oid(second_id)
    if pid is None or a is None or b is None:
        return False
    first = get_item(name, pid, a)
    second = get_item(name, pid, b)
    if not first or not second:
        return False

    stamp = _stamp()
    payload_a = {f: second.get(f) for f in fields if f in second}
    payload_b = {f: first.get(f) for f in fields if f in first}
    payload_a["updated_at"] = stamp
    payload_b["updated_at"] = stamp
    _collection(name).update_one({"_id": a}, {"$set": payload_a})
    _collection(name).update_one({"_id": b}, {"$set": payload_b})
    return True
