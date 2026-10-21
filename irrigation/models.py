import re
import threading

from irrigation.extensions import mongo
from irrigation.storage import max_embedded_id

_ID_COUNTERS = {}
_ID_LOCK = threading.Lock()


def _highest_known_id(collection_name):
    """Highest id already stored, used to seed the in-process counter."""
    if collection_name in ('users', 'user'):
        docs = mongo.db.users.find({}, {'id': 1})
    elif collection_name in ('projects', 'project'):
        docs = mongo.db.projects.find({}, {'id': 1})
    else:
        return max_embedded_id(collection_name)

    best = 0
    for doc in docs:
        try:
            best = max(best, int(doc.get('id') or 0))
        except (TypeError, ValueError):
            continue
    return best


def get_next_id(collection_name):
    """Auto-increment integer ID for a collection.

    The two-collection model has no ``counters`` collection, so the counter is
    kept in memory and seeded once from the highest stored id.  That keeps ids
    unique across restarts without adding a third collection.
    """
    with _ID_LOCK:
        if collection_name not in _ID_COUNTERS:
            _ID_COUNTERS[collection_name] = _highest_known_id(collection_name)
        _ID_COUNTERS[collection_name] += 1
        return _ID_COUNTERS[collection_name]


def serialize_doc(doc):
    """Convert MongoDB doc to JSON-serializable dict with string IDs."""
    if doc is None:
        return None
    doc = dict(doc)
    doc['_id'] = str(doc['_id'])
    return doc


def _sector_short_code(sector, ordinal):
    name = (sector.get('name_en') or '').strip()
    match = re.match(r'^[Ss](\d+)$', re.sub(r'[^Ss0-9]', '', name)) or re.search(r'(\d+)', name)
    if match:
        return 'S{}'.format(match.group(1))
    return 'S{}'.format(ordinal)


#: Short-name levels, outermost first.  A level implies the ones above it.
SHORT_NAME_LEVELS = ('sector', 'zone', 'row', 'tree')


def assign_short_names(project_id, levels=None):
    """Compute and persist hierarchical short names for a project.

    Sector -> ``S1`` (number parsed from the name, else its ordinal),
    Zone -> ``S1Z1`` (numbered per sector), Row -> ``S1Z1R1`` (per zone),
    Tree -> ``S1Z1R1T1`` (per row). Numbering follows ascending ``id``.

    ``levels`` limits the work to the codes a page actually shows: a map that
    only draws sectors must not walk every tree in the project.  The default
    refreshes every level.
    """
    wanted = set(SHORT_NAME_LEVELS if levels is None else levels)
    # A level needs the codes of its parents, so widen the request downwards.
    if 'tree' in wanted:
        wanted.update(('row', 'zone'))
    if 'row' in wanted:
        wanted.add('zone')
    wanted.add('sector')

    def _sorted(collection):
        return sorted(
            list(mongo.db[collection].find({'project_id': project_id})),
            key=lambda doc: doc.get('id', 0)
        )

    def _save(collection, doc, value):
        if doc.get('short_name') != value:
            mongo.db[collection].update_one({'_id': doc['_id']}, {'$set': {'short_name': value}})
        return value

    sector_codes = {}
    for ordinal, sector in enumerate(_sorted('sectors'), start=1):
        sector_codes[sector['id']] = _save('sectors', sector, _sector_short_code(sector, ordinal))

    if 'zone' not in wanted:
        return

    zone_codes = {}
    zone_counters = {}
    for zone in _sorted('zones'):
        sector_id = zone.get('sector_id')
        zone_counters[sector_id] = zone_counters.get(sector_id, 0) + 1
        base = sector_codes.get(sector_id, 'S{}'.format(sector_id))
        zone_codes[zone['id']] = _save(
            'zones', zone, '{}Z{}'.format(base, zone_counters[sector_id])
        )

    if 'row' not in wanted:
        return

    row_codes = {}
    row_counters = {}
    for row in _sorted('tree_rows'):
        zone_id = row.get('zone_id')
        row_counters[zone_id] = row_counters.get(zone_id, 0) + 1
        base = zone_codes.get(zone_id, 'Z{}'.format(zone_id))
        row_codes[row['id']] = _save(
            'tree_rows', row, '{}R{}'.format(base, row_counters[zone_id])
        )

    if 'tree' not in wanted:
        return

    tree_counters = {}
    for tree in _sorted('trees'):
        row_id = tree.get('row_id')
        tree_counters[row_id] = tree_counters.get(row_id, 0) + 1
        base = row_codes.get(row_id, 'R{}'.format(row_id))
        _save('trees', tree, '{}T{}'.format(base, tree_counters[row_id]))
