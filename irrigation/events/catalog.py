"""The FIS domain event catalogue.

Every event carries a stable ``type`` (also used as the RabbitMQ routing key)
so a service can subscribe to exactly what it needs.  Names are
``<aggregate>.<past-tense action>``: an event says what already happened, never
what a consumer should do.

Adding an event:

1. add the constant and a row in :data:`CATALOG`;
2. publish it from the route that made the change (``emit(...)``);
3. subscribe in the consumer that cares (``fis.audit`` takes everything).
"""

from __future__ import annotations

# --- projects ------------------------------------------------------------- #
PROJECT_CREATED = "project.created"
PROJECT_UPDATED = "project.updated"
PROJECT_DELETED = "project.deleted"

# --- geometry (sectors / zones) ------------------------------------------- #
SECTOR_CREATED = "sector.created"
SECTORS_REMOVED = "sectors.removed"
ZONE_CREATED = "zone.created"

# --- hydrology / network -------------------------------------------------- #
PIPING_GENERATED = "piping.generated"

# --- engineering ---------------------------------------------------------- #
VALIDATION_COMPLETED = "validation.completed"

#: type -> human description.  Also the source of truth for what the
#: documentation lists, so a new event never documents itself twice.
CATALOG = {
    PROJECT_CREATED: "A project was created.",
    PROJECT_UPDATED: "Project settings (spec, water sources, land, owner link) were saved.",
    PROJECT_DELETED: "A project and its embedded children were deleted.",
    SECTOR_CREATED: "A sector polygon was drawn or imported.",
    SECTORS_REMOVED: "One or more sectors were removed with their zones, rows, trees and pipes.",
    ZONE_CREATED: "A zone polygon was created inside a sector.",
    PIPING_GENERATED: "The piping structure (main, second, zone pipes, driplines) was generated.",
    VALIDATION_COMPLETED: "The engineering validation ran and produced a status.",
}

ALL = tuple(CATALOG)

#: Routing-key filters a consumer can bind to.  ``#`` matches everything.
ALL_TOPICS = "#"


def known(event_type: str) -> bool:
    return event_type in CATALOG


def family(event_type: str) -> str:
    """``sector.created`` -> ``sector`` (handy for grouping in a UI)."""
    return event_type.split(".", 1)[0]
