"""Add elevation to an uploaded KML/KMZ file that arrives without it.

Google Earth, QGIS and most GPS exports write flat coordinates --
``lon,lat`` or ``lon,lat,0`` -- so an uploaded map has no altitude for the
terrain-aware calculations.  :class:`KmlElevation` fills in what is missing
before the file is parsed.

The document layout is preserved: the tree is edited in place and only the
third value of each ``<coordinates>`` tuple is written.  Placemark names,
descriptions, styles, folders and geometry are untouched, because those
names are what tell a water source from a sector during the import.

Elevations come from the project's existing Open-Meteo client
(``irrigation.engineering.elevation``, free, no key required) and reuse its
per-coordinate cache, so re-uploading the same map costs no extra requests.

Typical use from a route::

    from irrigation.utils.kml_elevation import KmlElevation

    filled = KmlElevation().process_file(filepath)   # .kml or .kmz, in place
"""
import os
import zipfile

from lxml import etree

from irrigation.engineering.elevation import (
    cache_elevation,
    fetch_open_meteo_elevations,
    get_cached_elevation,
)


KML_SUFFIXES = ('.kml', '.kmz')

# Coordinate pairs are cached rounded to this many decimals, matching
# irrigation.engineering.elevation._ELEVATION_SAMPLE_CACHE.
_CACHE_DECIMALS = 5


# -----------------------------
# File / tree helpers
# -----------------------------

def find_kml_entry(names):
    """Name of the KML document inside a KMZ archive: ``doc.kml`` if present,
    otherwise the first ``.kml`` entry, otherwise ``None``."""
    for name in names:
        if name.lower().endswith('doc.kml'):
            return name
    for name in names:
        if name.lower().endswith('.kml'):
            return name
    return None


def read_kml_bytes(path):
    """Raw KML bytes of a ``.kml`` file, or of the document inside a ``.kmz``."""
    if path.lower().endswith('.kmz'):
        with zipfile.ZipFile(path, 'r') as zf:
            entry = find_kml_entry(zf.namelist())
            if entry is None:
                raise ValueError('No KML file found inside the KMZ archive.')
            return zf.read(entry)

    with open(path, 'rb') as handle:
        return handle.read()


def parse_kml_bytes(data):
    """Parse KML bytes into an lxml element tree root."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    return etree.fromstring(data)


def _local_name(element):
    """Local name of an element, ignoring any namespace prefix.

    Works for KML in the default namespace, in a prefixed namespace
    (``<kml:coordinates>``) and without a namespace at all.
    """
    tag = element.tag
    if not isinstance(tag, str):
        return ''
    return tag.rpartition('}')[2]


def _parse_tokens(text):
    """Split a ``<coordinates>`` body into raw ``lon,lat[,altitude]`` tokens.

    The original longitude/latitude strings are kept verbatim so no precision
    is lost when the altitude is written back.
    """
    tokens = []

    for token in (text or '').split():
        parts = token.split(',')
        if len(parts) < 2:
            continue
        try:
            float(parts[0])
            float(parts[1])
        except ValueError:
            continue
        tokens.append(parts)

    return tokens


def _missing_elevation(parts):
    """True when a coordinate tuple carries no usable altitude.

    Covers 2D coordinates (``lon,lat``), the flat ``lon,lat,0`` that Google
    Earth writes for a terrain-less export, and a malformed third value.
    """
    if len(parts) < 3:
        return True
    try:
        return float(parts[2]) == 0.0
    except ValueError:
        return True


def coordinate_entries(root):
    """Every ``<coordinates>`` element of a parsed KML tree that holds points.

    Returns a list of ``(element, tokens)`` in document order.
    """
    entries = []

    for element in root.iter():
        if _local_name(element) != 'coordinates':
            continue
        tokens = _parse_tokens(element.text)
        if tokens:
            entries.append((element, tokens))

    return entries


def _render_tokens(tokens):
    """Text body for a ``<coordinates>`` element: one token per line when the
    element holds several, as KML files are normally written."""
    if len(tokens) == 1:
        return ','.join(tokens[0])
    return '\n'.join(','.join(token) for token in tokens) + '\n'


# -----------------------------
# Elevation
# -----------------------------

class KmlElevation:
    """Fill in the altitudes a KML/KMZ document is missing.

    ``fetch`` may be swapped for tests; it must take a list of
    ``{'lat': ..., 'lng': ...}`` dicts and return elevations in metres
    (``None`` where a lookup failed), like ``fetch_open_meteo_elevations``.
    """

    def __init__(self, fetch=None):
        self._fetch = fetch or fetch_open_meteo_elevations

    # -- inspection ------------------------------------------------------

    @staticmethod
    def has_elevation(root):
        """True when every coordinate of the document carries a real altitude.

        A document with no coordinates at all counts as having none.
        """
        entries = coordinate_entries(root)
        if not entries:
            return False
        return not any(
            _missing_elevation(parts)
            for _element, tokens in entries
            for parts in tokens
        )

    @staticmethod
    def missing_count(root):
        """How many coordinate tuples still lack an altitude."""
        return sum(
            1
            for _element, tokens in coordinate_entries(root)
            for parts in tokens
            if _missing_elevation(parts)
        )

    # -- enrichment ------------------------------------------------------

    def add_missing(self, root):
        """Look up and write the altitudes this tree is missing, in place.

        Coordinates that already carry an altitude are left exactly as they
        are, so a partly measured file keeps its own values.  Returns the
        number of coordinate tuples that were filled.
        """
        pending = []   # (tokens, index, lat, lng, element)

        for element, tokens in coordinate_entries(root):
            for index, parts in enumerate(tokens):
                if not _missing_elevation(parts):
                    continue
                try:
                    lat = float(parts[1])
                    lng = float(parts[0])
                except ValueError:
                    continue
                pending.append((tokens, index, lat, lng, element))

        if not pending:
            return 0

        elevations = self._lookup(pending)

        dirty = {}
        filled = 0

        for (tokens, index, _lat, _lng, element), elevation in zip(pending, elevations):
            if elevation is None:
                continue
            parts = tokens[index]
            tokens[index] = [parts[0], parts[1], f'{elevation:.2f}']
            dirty[element] = tokens
            filled += 1

        for element, tokens in dirty.items():
            element.text = _render_tokens(tokens)

        return filled

    def _lookup(self, pending):
        """Elevations for the pending points, cache first, then the API.

        Repeated coordinates (a closed polygon repeats its first vertex) and
        points already sampled by the project's elevation model are resolved
        without a request.
        """
        results = [None] * len(pending)
        wanted = {}       # cache key -> [positions in pending]

        for position, (_tokens, _index, lat, lng, _element) in enumerate(pending):
            cached = get_cached_elevation(lat, lng)
            if cached is not None:
                results[position] = cached
                continue
            key = (round(lat, _CACHE_DECIMALS), round(lng, _CACHE_DECIMALS))
            wanted.setdefault(key, []).append(position)

        if not wanted:
            return results

        keys = list(wanted)
        fetched = self._fetch([
            {'lat': key[0], 'lng': key[1]} for key in keys
        ]) or []

        for key, elevation in zip(keys, fetched):
            if elevation is None:
                continue
            cache_elevation(key[0], key[1], elevation)
            for position in wanted[key]:
                results[position] = elevation

        return results

    # -- whole files -----------------------------------------------------

    def process_file(self, path):
        """Add the missing elevations to a ``.kml`` / ``.kmz`` file, in place.

        The archive members of a KMZ are copied over untouched; only the KML
        document inside changes.  Returns how many coordinate tuples were
        filled, ``0`` when the file needed nothing, and ``0`` for any other
        extension.
        """
        if os.path.splitext(path)[1].lower() not in KML_SUFFIXES:
            return 0

        root = parse_kml_bytes(read_kml_bytes(path))
        filled = self.add_missing(root)

        if not filled:
            return 0

        if path.lower().endswith('.kmz'):
            _write_kmz(path, root)
        else:
            _write_kml(path, root)

        return filled


def ensure_kml_elevation(path, fetch=None):
    """Convenience wrapper: enrich an uploaded KML/KMZ and report the count.

    Raises whatever the lookup or the file system raises; callers that must
    not fail the upload should catch it themselves.
    """
    return KmlElevation(fetch).process_file(path)


# -----------------------------
# Writers
# -----------------------------

def _write_kml(path, root):
    """Rewrite a KML file with the edited tree, keeping its layout."""
    root.getroottree().write(
        path,
        encoding='UTF-8',
        xml_declaration=True,
        pretty_print=True,
    )


def _write_kmz(path, root):
    """Rewrite a KMZ archive with the edited document, every other member
    (icons, linked files) copied over unchanged."""
    document = etree.tostring(
        root, encoding='UTF-8', xml_declaration=True, pretty_print=True
    )
    temp_path = f'{path}.tmp'

    try:
        with zipfile.ZipFile(path, 'r') as source:
            entry = find_kml_entry(source.namelist())
            with zipfile.ZipFile(temp_path, 'w') as target:
                for info in source.infolist():
                    payload = (document if info.filename == entry
                               else source.read(info.filename))
                    target.writestr(info, payload)
        os.replace(temp_path, path)
    except BaseException:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
