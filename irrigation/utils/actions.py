"""Reusable form handlers for the domain blueprints.

Each blueprint keeps a thin route whose body is one call into this module, so the
validation and flash-message behaviour stays identical across all eight tabs.
"""

from __future__ import annotations

from typing import Any, Iterable

from flask import flash, request

from . import crud


def coerce(raw: Any) -> Any:
    """Best-effort conversion of a submitted string into a number.

    Blank input becomes ``None`` so that "field left empty" is recorded as
    "unknown" rather than as a misleading zero.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    try:
        number = float(text)
    except ValueError:
        return text
    return int(number) if number.is_integer() and "." not in text and "e" not in text.lower() else number


def collect(fields: Iterable[str]) -> dict[str, Any]:
    """Read the named form fields, coercing each one."""
    return {name: coerce(request.form.get(name)) for name in fields}


def _name_field() -> str:
    return request.form.get("name", "")


def add_record(collection: str, project_id, *, kind: str | None = None,
               unique_kind: bool = False, fields: Iterable[str] = (),
               extra: dict[str, Any] | None = None,
               success: str = "Record added.") -> bool:
    payload = collect(fields)
    if extra:
        payload.update(extra)
    try:
        crud.add_item(collection, project_id, _name_field(), kind=kind,
                      unique_kind=unique_kind, **payload)
    except crud.CrudError as exc:
        flash(str(exc), "danger")
        return False
    flash(success, "success")
    return True


def rename_record(collection: str, project_id, item_id, *,
                  kind: str | None = None, unique_kind: bool = False) -> bool:
    try:
        ok = crud.rename_item(collection, project_id, item_id,
                              request.form.get("name", ""),
                              kind=kind, unique_kind=unique_kind)
    except crud.CrudError as exc:
        flash(str(exc), "danger")
        return False
    flash("Renamed." if ok else "Record not found.",
          "success" if ok else "danger")
    return ok


def update_record(collection: str, project_id, item_id,
                  fields: Iterable[str] = ()) -> bool:
    ok = crud.update_item(collection, project_id, item_id, collect(fields))
    flash("Saved." if ok else "Record not found.",
          "success" if ok else "danger")
    return ok


def remove_record(collection: str, project_id, item_id, *,
                  success: str = "Removed.") -> bool:
    ok = crud.remove_item(collection, project_id, item_id)
    flash(success if ok else "Record not found.",
          "success" if ok else "danger")
    return ok


def swap_records(collection: str, project_id, first_id, second_id,
                 fields: tuple[str, ...] = ("name",),
                 success: str = "Swapped.") -> bool:
    ok = crud.swap_items(collection, project_id, first_id, second_id, fields=fields)
    flash(success if ok else "Both records must exist to swap.",
          "success" if ok else "danger")
    return ok
