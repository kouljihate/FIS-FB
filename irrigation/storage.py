"""Embedded MongoDB storage for the two-collection FIS model.

The web app was written against separate MongoDB collections (``sectors``,
``zones``, ``tree_rows``, ``trees``, ``network_elements`` ...).  The new model
keeps only ``user`` and ``project``; every child document lives inside its
project::

    project.sectors[].zones[].rows[].trees[]
    project.network.pipes[]          # MP / SP / ZP / dripline
    project.network.components.*[]   # valves, connectors, reductions, ...
    project.reports.piping
    project.reports.validations[]
    project.elevation_models[]

``EmbeddedDatabase`` keeps the PyMongo-style API the routes already use
(``mongo.db.sectors.find(...)``, ``insert_one``, ``update_one`` ...), so the
application code keeps working while the documents are embedded.  Writes are
applied to an in-request copy of the project and flushed once at teardown,
which keeps large generations (thousands of trees) to a single write.
"""

from __future__ import annotations

import copy
import threading
from typing import Any, Iterable

from bson import ObjectId
from flask import g, has_app_context

MISSING = object()

#: Collections that now live inside a project document.
EMBEDDED = {
    "sectors": "sectors",
    "zones": "zones",
    "tree_rows": "rows",
    "trees": "trees",
    "network_elements": "network",
    "engineering_validations": "validations",
    "piping_reports": "piping",
    "project_elevation_models": "elevation_models",
}

#: Real collection names and the plural aliases the app already uses.
REAL_ALIASES = {
    "users": "user",
    "user": "user",
    "projects": "project",
    "project": "project",
}

PIPE_TYPES = ("main_pipe", "second_pipe", "sub_pipe", "dripline")
VALVE_TYPES = ("master_valve", "sector_valve", "zone_valve", "check_valve")
COMPONENT_SLOTS = {
    "reduction": "reductions",
    "air_release": "air_removal",
    "connector": "connectors",
    "pressure_reduction": "pressure_reduction",
    "terminator": "terminators",
}

_PROCESS_CACHE: dict[int, dict] = {}
_PROCESS_LOCK = threading.RLock()


def _request_cache() -> dict[int, dict]:
    if has_app_context():
        cache = getattr(g, "_embedded_projects", None)
        if cache is None:
            cache = {}
            g._embedded_projects = cache
        return cache
    return _PROCESS_CACHE


def _empty_component_slots() -> dict[str, list]:
    return {
        "valves": [],
        "connectors": [],
        "reductions": [],
        "air_removal": [],
        "pressure_reduction": [],
        "terminators": [],
    }


class UpdateResult:
    def __init__(self, matched: int = 0, modified: int = 0, upserted_id=None):
        self.matched_count = matched
        self.modified_count = modified
        self.upserted_id = upserted_id
        self.acknowledged = True

    @property
    def raw_result(self):
        return {
            "n": self.matched_count,
            "nModified": self.modified_count,
            "upserted": self.upserted_id,
        }


class DeleteResult:
    def __init__(self, deleted: int = 0):
        self.deleted_count = deleted
        self.acknowledged = True

    @property
    def raw_result(self):
        return {"n": self.deleted_count}


class InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id
        self.acknowledged = True


class InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids
        self.acknowledged = True


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _get_path(doc: Any, path: str) -> Any:
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


def _set_path(doc: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    current = doc
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _unset_path(doc: dict, path: str) -> None:
    parts = path.split(".")
    current = doc
    for part in parts[:-1]:
        current = current.get(part)
        if not isinstance(current, dict):
            return
    current.pop(parts[-1], None)


def _match_value(value: Any, expected: Any) -> bool:
    if isinstance(expected, dict) and any(str(key).startswith("$") for key in expected):
        for op, operand in expected.items():
            if op == "$in":
                if isinstance(operand, (list, tuple, set)):
                    if value is MISSING or value not in operand:
                        return False
                elif value is MISSING or value != operand:
                    return False
            elif op == "$nin":
                if value is not MISSING and isinstance(operand, (list, tuple, set)) and value in operand:
                    return False
            elif op == "$exists":
                if bool(operand) != (value is not MISSING):
                    return False
            elif op == "$ne":
                if value is not MISSING and value == operand:
                    return False
            elif op == "$eq":
                if value is MISSING or value != operand:
                    return False
            elif op == "$regex":
                import re as _re

                if value is MISSING or not isinstance(value, str):
                    return False
                if not _re.search(operand, value):
                    return False
            elif op == "$gt":
                if value is MISSING or not value > operand:
                    return False
            elif op == "$gte":
                if value is MISSING or not value >= operand:
                    return False
            elif op == "$lt":
                if value is MISSING or not value < operand:
                    return False
            elif op == "$lte":
                if value is MISSING or not value <= operand:
                    return False
            elif op == "$size":
                if value is MISSING or not isinstance(value, list) or len(value) != operand:
                    return False
            elif op == "$not":
                if _match_value(value, operand):
                    return False
            elif op == "$options":
                continue
            else:
                raise NotImplementedError("Unsupported query operator: %s" % op)
        return True

    if value is MISSING:
        return False
    return value == expected


def matches(doc: dict, query: dict | None) -> bool:
    if not query:
        return True
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(doc, clause) for clause in expected):
                return False
            continue
        if key == "$and":
            if not all(matches(doc, clause) for clause in expected):
                return False
            continue
        if key == "$nor":
            if any(matches(doc, clause) for clause in expected):
                return False
            continue
        value = _get_path(doc, key)
        if not _match_value(value, expected):
            return False
    return True


def _sort_key(value: Any):
    if value is MISSING or value is None:
        return (1, "")
    if isinstance(value, (int, float)):
        return (0, value)
    return (0, str(value))


def _sort_docs(docs: list[dict], sort) -> list[dict]:
    if not sort:
        return docs
    if isinstance(sort, str):
        sort = [(sort, 1)]
    for key, direction in reversed(list(sort)):
        docs.sort(key=lambda doc: _sort_key(_get_path(doc, key)), reverse=direction < 0)
    return docs


def _project_doc(doc: dict, projection: dict | None) -> dict:
    if not projection:
        return doc
    include_id = projection.get("_id", 1) != 0
    out = {}
    for key, include in projection.items():
        if key == "_id" or not include:
            continue
        value = _get_path(doc, key)
        if value is not MISSING:
            _set_path(out, key, copy.deepcopy(value))
    if include_id and "_id" in doc:
        out["_id"] = doc["_id"]
    return out


class EmbeddedCursor:
    """Small cursor with the methods the routes use (sort/limit/iteration)."""

    def __init__(self, docs: list[dict]):
        self._docs = list(docs)

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, str):
            key_or_list = [(key_or_list, 1 if direction is None else direction)]
        self._docs = _sort_docs(self._docs, key_or_list)
        return self

    def limit(self, count: int):
        self._docs = self._docs[:count]
        return self

    def skip(self, count: int):
        self._docs = self._docs[count:]
        return self

    def __iter__(self):
        return iter(self._docs)

    def __len__(self):
        return len(self._docs)

    def __getitem__(self, index):
        return self._docs[index]

    def count(self):
        return len(self._docs)


# ---------------------------------------------------------------------------
# Embedded collection
# ---------------------------------------------------------------------------

class EmbeddedCollection:
    def __init__(self, storage: "EmbeddedDatabase", name: str):
        self.storage = storage
        self.name = name

    @property
    def _real(self):
        return self.storage.real

    @property
    def _projects(self):
        return self.storage.real["project"]

    # -- project loading -------------------------------------------------
    def _project_ids(self, query: dict | None) -> list[int]:
        query = query or {}
        pid = query.get("project_id")
        if isinstance(pid, dict):
            if "$in" in pid:
                return [int(value) for value in pid["$in"]]
            return []
        if pid is not None:
            return [int(pid)]
        return [int(doc["id"]) for doc in self._projects.find({}, {"id": 1})]

    def _entry(self, project_id: int):
        return self.storage.entry(project_id)

    def _flat(self, entry: dict) -> list[dict]:
        cache_key = "flat_" + self.name
        cached = entry.get(cache_key)
        if cached is not None:
            return cached

        doc = entry["doc"] or {}
        result: list[dict] = []
        if self.name == "sectors":
            result = list(doc.get("sectors") or [])
        elif self.name == "zones":
            for sector in doc.get("sectors") or []:
                result.extend(sector.get("zones") or [])
        elif self.name == "tree_rows":
            for sector in doc.get("sectors") or []:
                for zone in sector.get("zones") or []:
                    result.extend(zone.get("rows") or [])
        elif self.name == "trees":
            for sector in doc.get("sectors") or []:
                for zone in sector.get("zones") or []:
                    for row in zone.get("rows") or []:
                        result.extend(row.get("trees") or [])
        elif self.name == "network_elements":
            network = doc.get("network") or {}
            result.extend(network.get("pipes") or [])
            # Legacy shape: a flat list of elements.
            result.extend(doc.get("network_elements") or [])
            components = network.get("components") or {}
            for slot in (components.values() if isinstance(components, dict) else []):
                if isinstance(slot, list):
                    result.extend(slot)
        elif self.name == "engineering_validations":
            result = list((doc.get("reports") or {}).get("validations") or [])
        elif self.name == "project_elevation_models":
            result = list(doc.get("elevation_models") or [])
        elif self.name == "piping_reports":
            report = (doc.get("reports") or {}).get("piping")
            if isinstance(report, dict):
                result = [report]

        entry[cache_key] = result
        return result

    def _invalidate(self, entry: dict) -> None:
        for key in list(entry):
            if key.startswith("flat_") or key.startswith("index_"):
                entry.pop(key, None)

    def _lookup(self, entry: dict, query: dict | None) -> list[dict] | None:
        """Fast path for ``{'_id': ...}`` / ``{'id': ...}`` filters."""
        if not query:
            return None
        keys = set(query)
        if not keys <= {"_id", "id", "project_id"}:
            return None
        field = "id" if "id" in query else ("_id" if "_id" in query else None)
        if field is None:
            return None
        wanted = query[field]
        if isinstance(wanted, dict):
            return None
        cache_key = "flat_" + self.name
        flat = self._flat(entry)
        # Linear fallback is fine for small lists; build a map once.
        index_key = "index_" + self.name
        index = entry.get(index_key)
        if index is None:
            index = {}
            for doc in flat:
                if "id" in doc:
                    index.setdefault(("id", doc["id"]), doc)
                if "_id" in doc:
                    index.setdefault(("_id", doc["_id"]), doc)
            entry[index_key] = index
        doc = index.get((field, wanted))
        if doc is None:
            return []
        if "project_id" in query and doc.get("project_id") != query["project_id"]:
            return []
        return [doc]

    def _matches_in(self, query: dict | None, dedupe: bool = True) -> list[dict]:
        result: list[dict] = []
        seen: set[int] = set()
        for pid in self._project_ids(query):
            entry = self._entry(pid)
            if not entry or not entry.get("doc"):
                continue
            found = self._lookup(entry, query)
            docs = found if found is not None else self._flat(entry)
            for doc in docs:
                if not matches(doc, query):
                    continue
                if dedupe:
                    marker = id(doc)
                    if marker in seen:
                        continue
                    seen.add(marker)
                result.append(doc)
        return result

    # -- reads -----------------------------------------------------------
    def find(self, query=None, projection=None, **kwargs):
        docs = [
            _project_doc(copy.deepcopy(doc), projection)
            for doc in self._matches_in(query)
        ]
        if kwargs.get("sort"):
            docs = _sort_docs(docs, kwargs["sort"])
        return EmbeddedCursor(docs)

    def find_one(self, query=None, projection=None, sort=None, **kwargs):
        docs = [_project_doc(copy.deepcopy(doc), projection) for doc in self._matches_in(query)]
        if sort:
            docs = _sort_docs(docs, sort)
        return docs[0] if docs else None

    def count_documents(self, query=None, **kwargs):
        return len(self._matches_in(query, dedupe=False))

    def estimated_document_count(self):
        return len(self._matches_in(None, dedupe=False))

    def distinct(self, field, query=None, **kwargs):
        values = []
        for doc in self._matches_in(query):
            value = _get_path(doc, field)
            if value is not MISSING and value not in values:
                values.append(value)
        return values

    # -- writes ----------------------------------------------------------
    def _parent_array(self, project: dict, doc: dict) -> list:
        name = self.name
        if name == "sectors":
            return project.setdefault("sectors", [])
        if name == "zones":
            for sector in project.get("sectors") or []:
                if sector.get("id") == doc.get("sector_id"):
                    return sector.setdefault("zones", [])
            raise ValueError("Sector %r not found for zone %r" % (doc.get("sector_id"), doc.get("id")))
        if name == "tree_rows":
            for sector in project.get("sectors") or []:
                for zone in sector.get("zones") or []:
                    if zone.get("id") == doc.get("zone_id"):
                        return zone.setdefault("rows", [])
            raise ValueError("Zone %r not found for row %r" % (doc.get("zone_id"), doc.get("id")))
        if name == "trees":
            for sector in project.get("sectors") or []:
                for zone in sector.get("zones") or []:
                    for row in zone.get("rows") or []:
                        if row.get("id") == doc.get("row_id"):
                            return row.setdefault("trees", [])
            raise ValueError("Row %r not found for tree %r" % (doc.get("row_id"), doc.get("id")))
        if name == "network_elements":
            network = project.setdefault("network", {})
            network.setdefault("pipes", [])
            network.setdefault("components", _empty_component_slots())
            elem_type = doc.get("type")
            if elem_type in PIPE_TYPES:
                return network["pipes"]
            if elem_type in VALVE_TYPES:
                return network["components"].setdefault("valves", [])
            slot = COMPONENT_SLOTS.get(elem_type)
            if slot:
                return network["components"].setdefault(slot, [])
            return network["pipes"]
        if name == "engineering_validations":
            reports = project.setdefault("reports", {})
            return reports.setdefault("validations", [])
        if name == "project_elevation_models":
            return project.setdefault("elevation_models", [])
        if name == "piping_reports":
            raise ValueError("piping_reports stores a single document; use update_one")
        raise ValueError("Unknown embedded collection: %s" % name)

    def insert_one(self, doc: dict, **kwargs):
        self.storage.require_project(doc.get("project_id"))
        stored = copy.deepcopy(doc)
        if "_id" not in stored:
            stored["_id"] = ObjectId()
        project_id = int(stored["project_id"])
        entry = self._entry(project_id)
        if self.name == "piping_reports":
            reports = entry["doc"].setdefault("reports", {})
            reports["piping"] = stored
        else:
            self._parent_array(entry["doc"], stored).append(stored)
        entry["dirty"] = True
        self._invalidate(entry)
        doc.setdefault("_id", stored["_id"])
        return InsertOneResult(stored["_id"])

    def insert_many(self, docs: Iterable[dict], **kwargs):
        inserted = []
        for doc in docs:
            result = self.insert_one(doc)
            inserted.append(result.inserted_id)
        return InsertManyResult(inserted)

    def _apply_update(self, target: dict, update: dict) -> bool:
        changed = False
        for operator, values in update.items():
            if operator == "$set":
                for path, value in values.items():
                    current = _get_path(target, path)
                    if current is MISSING or current != value:
                        _set_path(target, path, copy.deepcopy(value))
                        changed = True
            elif operator == "$unset":
                for path in values:
                    _unset_path(target, path)
                    changed = True
            elif operator == "$inc":
                for path, amount in values.items():
                    current = _get_path(target, path)
                    base = 0 if current is MISSING else current
                    _set_path(target, path, base + amount)
                    changed = True
            else:
                raise NotImplementedError("Unsupported update operator: %s" % operator)
        return changed

    def update_one(self, query, update, upsert=False, **kwargs):
        found = self._matches_in(query)
        if not found:
            if upsert and self.name == "piping_reports":
                project_id = (query or {}).get("project_id")
                if project_id is not None:
                    entry = self._entry(int(project_id))
                    if entry and entry.get("doc") is not None:
                        report = {"project_id": int(project_id), "_id": ObjectId()}
                        self._apply_update(report, update)
                        entry["doc"].setdefault("reports", {})["piping"] = report
                        entry["dirty"] = True
                        self._invalidate(entry)
                        return UpdateResult(0, 1, report["_id"])
            return UpdateResult(0, 0)
        target = found[0]
        entry = self.storage.entry(int(target["project_id"]))
        changed = self._apply_update(target, update)
        if changed:
            entry["dirty"] = True
        return UpdateResult(1, 1 if changed else 0)

    def update_many(self, query, update, upsert=False, **kwargs):
        found = self._matches_in(query)
        modified = 0
        for target in found:
            entry = self.storage.entry(int(target["project_id"]))
            if self._apply_update(target, update):
                entry["dirty"] = True
                modified += 1
        return UpdateResult(len(found), modified)

    def delete_one(self, query, **kwargs):
        return self._delete(query, many=False)

    def delete_many(self, query, **kwargs):
        return self._delete(query, many=True)

    def _delete(self, query, many: bool):
        project_ids = self._project_ids(query)
        deleted = 0
        for pid in project_ids:
            entry = self._entry(pid)
            if not entry or not entry.get("doc"):
                continue
            project = entry["doc"]
            if self.name == "piping_reports":
                report = (project.get("reports") or {}).get("piping")
                if report and matches(report, query):
                    project.setdefault("reports", {}).pop("piping", None)
                    entry["dirty"] = True
                    self._invalidate(entry)
                    deleted += 1
                    if not many:
                        break
                continue

            arrays = []
            if self.name == "sectors":
                arrays.append(project.get("sectors") or [])
            elif self.name == "zones":
                for sector in project.get("sectors") or []:
                    arrays.append(sector.get("zones") or [])
            elif self.name == "tree_rows":
                for sector in project.get("sectors") or []:
                    for zone in sector.get("zones") or []:
                        arrays.append(zone.get("rows") or [])
            elif self.name == "trees":
                for sector in project.get("sectors") or []:
                    for zone in sector.get("zones") or []:
                        for row in zone.get("rows") or []:
                            arrays.append(row.get("trees") or [])
            elif self.name == "network_elements":
                network = project.get("network") or {}
                arrays.append(network.get("pipes") or [])
                components = network.get("components") or {}
                for slot in (components.values() if isinstance(components, dict) else []):
                    if isinstance(slot, list):
                        arrays.append(slot)
                arrays.append(project.get("network_elements") or [])
            elif self.name == "engineering_validations":
                arrays.append((project.get("reports") or {}).get("validations") or [])
            elif self.name == "project_elevation_models":
                arrays.append(project.get("elevation_models") or [])

            removed_here = False
            for array in arrays:
                if many:
                    before = len(array)
                    array[:] = [doc for doc in array if not matches(doc, query)]
                    removed = before - len(array)
                    if removed:
                        deleted += removed
                        removed_here = True
                else:
                    for idx, doc in enumerate(array):
                        if matches(doc, query):
                            array.pop(idx)
                            deleted += 1
                            removed_here = True
                            break
                if removed_here and not many:
                    break
            if removed_here:
                entry["dirty"] = True
                self._invalidate(entry)
            if deleted and not many:
                break
        return DeleteResult(deleted)

    def find_one_and_update(self, query, update, upsert=False, **kwargs):
        doc = self.find_one(query)
        if doc is not None:
            self.update_one(query, update)
            return self.find_one(query)
        if upsert:
            self.update_one(query, update, upsert=True)
            return self.find_one(query)
        return None


# ---------------------------------------------------------------------------
# Database wrapper
# ---------------------------------------------------------------------------

class EmbeddedDatabase:
    def __init__(self, real):
        object.__setattr__(self, "real", real)
        object.__setattr__(self, "_collections", {})

    # PyMongo database-like access
    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __getitem__(self, name: str):
        if name in EMBEDDED:
            collections = object.__getattribute__(self, "_collections")
            if name not in collections:
                collections[name] = EmbeddedCollection(self, name)
            return collections[name]
        real_name = REAL_ALIASES.get(name, name)
        return self.real[real_name]

    @property
    def name(self):
        return self.real.name

    # -- cache -----------------------------------------------------------
    def entry(self, project_id: int):
        cache = _request_cache()
        project_id = int(project_id)
        entry = cache.get(project_id)
        if entry is None:
            doc = self.real["project"].find_one({"id": project_id})
            entry = {"doc": doc, "dirty": False}
            cache[project_id] = entry
        return entry

    def require_project(self, project_id):
        if project_id is None:
            raise ValueError("Embedded documents need a project_id")
        entry = self.entry(int(project_id))
        if entry.get("doc") is None:
            raise ValueError("Project %s does not exist" % project_id)
        return entry

    def flush(self):
        cache = _request_cache()
        with _PROCESS_LOCK:
            for entry in list(cache.values()):
                if not entry.get("dirty") or not entry.get("doc"):
                    continue
                doc = entry["doc"]
                payload = {}
                for key in ("sectors", "network", "reports", "elevation_models", "network_elements"):
                    if key in doc:
                        payload[key] = doc[key]
                if not payload:
                    continue
                try:
                    self.real["project"].update_one({"_id": doc["_id"]}, {"$set": payload})
                except Exception:
                    # Do not silently lose the request's work: log the failure
                    # and let the teardown surface it.
                    from flask import current_app

                    current_app.logger.exception("Could not flush embedded project %s", doc.get("id"))
                    raise
            cache.clear()

    def discard(self, project_id: int):
        _request_cache().pop(int(project_id), None)


def install_storage(mongo, app):
    """Wrap ``mongo.db`` and flush embedded writes at request teardown."""
    if not isinstance(mongo.db, EmbeddedDatabase):
        mongo.db = EmbeddedDatabase(mongo.db)

    @app.teardown_appcontext
    def _flush_embedded(_exc):
        if isinstance(mongo.db, EmbeddedDatabase):
            mongo.db.flush()

    return mongo.db


def max_embedded_id(collection_name: str) -> int:
    """Highest ``id`` currently stored for an embedded collection."""
    from irrigation.extensions import mongo

    best = 0
    for doc in mongo.db[collection_name].find({}):
        try:
            best = max(best, int(doc.get("id") or 0))
        except (TypeError, ValueError):
            continue
    return best
