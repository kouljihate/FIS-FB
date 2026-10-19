"""JSON API used by the map, the charts and any external client.

Every endpoint is scoped by ``project_id`` and re-uses the same access check as
the HTML pages, so an API caller can never see more than a signed-in user could.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..project import service
from ..utils import crud
from ..utils.decorators import project_access_required
from ..utils.serialization import to_jsonable

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.get("/project/<project_id>/geometry")
@project_access_required
def project_geometry(project_id: str):
    """Sectors and zones with their boundaries and pre-computed metrics."""
    project = request.project  # type: ignore[attr-defined]
    pid = project["_id"]
    sectors = []
    for sector in crud.list_items("sectors", pid):
        zones = crud.list_items("zones", pid, filters={"sector_id": sector["_id"]})
        sectors.append({
            "id": str(sector["_id"]),
            "name": sector.get("name"),
            "boundary": sector.get("boundary") or [],
            "metrics": sector.get("metrics") or {},
            "zones": [{
                "id": str(zone["_id"]),
                "name": zone.get("name"),
                "boundary": zone.get("boundary") or [],
                "metrics": zone.get("metrics") or {},
            } for zone in zones],
        })

    return jsonify({
        "project": {
            "id": str(pid),
            "name": project.get("name"),
            "land": to_jsonable(project.get("land") or {}),
        },
        "sectors": sectors,
    })


@api_bp.get("/project/<project_id>/summary")
@project_access_required
def project_summary(project_id: str):
    project = request.project  # type: ignore[attr-defined]
    return jsonify({
        "project": {"id": str(project["_id"]), "name": project.get("name")},
        "stats": service.stats(project["_id"]),
        "geometry": {
            "sector_area_ha": round(sum(
                (s.get("metrics") or {}).get("area_ha", 0)
                for s in crud.list_items("sectors", project["_id"])), 4),
            "zone_area_ha": round(sum(
                (z.get("metrics") or {}).get("area_ha", 0)
                for z in crud.list_items("zones", project["_id"])), 4),
        },
    })


@api_bp.get("/project/<project_id>/collection/<name>")
@project_access_required
def collection(project_id: str, name: str):
    """Generic JSON read of a child collection, optionally filtered by ``kind``."""
    project = request.project  # type: ignore[attr-defined]
    allowed = {"sectors", "zones", "rows", "trees", "pipes", "fittings",
               "water_resources"}
    if name not in allowed:
        return jsonify({"error": "unknown_collection", "allowed": sorted(allowed)}), 404
    items = []
    for doc in crud.list_items(name, project["_id"], kind=request.args.get("kind")):
        payload = to_jsonable(doc)
        # Normalise the key so clients see a plain string id everywhere, matching
        # the geometry endpoint instead of a raw Mongo ``_id``.
        payload["id"] = payload.pop("_id")
        payload.pop("project_id", None)
        items.append(payload)
    return jsonify({"count": len(items), "items": items})
