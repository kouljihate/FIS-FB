"""
Read KML or CSV coordinates, get elevation from Open-Elevation,
and save the result as either CSV or KML.

KML and KMZ input keeps its original layout: the file is edited in
place and only the altitude (the third value of each <coordinates>
tuple) is replaced with the looked-up elevation. Placemark names,
descriptions, styles, folders and geometry (Polygon, LineString,
Point, ...) are preserved exactly as in the input.

Usage:
    python kml_kmz_csv_add_elevation.py input.csv output.csv csv
    python kml_kmz_csv_add_elevation.py input.csv output.kml kml
    python kml_kmz_csv_add_elevation.py input.kml output.csv csv
    python kml_kmz_csv_add_elevation.py input.kml output.kml kml
    python kml_kmz_csv_add_elevation.py input.kmz output.kml kml
    python kml_kmz_csv_add_elevation.py input.kmz output.csv csv
"""

import csv
import os
import sys
import time
import requests
from lxml import etree
import zipfile


KML_NS = "http://www.opengis.net/kml/2.2"
NS = {"kml": KML_NS}

API_URL = "https://api.open-elevation.com/api/v1/lookup"
BATCH_SIZE = 100


# -----------------------------
# Read CSV
# -----------------------------

def read_csv_points(csv_path):
    """
    Read CSV with columns:
        lat,lon
    or:
        latitude,longitude

    An optional 'name' column is carried along so the naming of the
    points survives when writing KML or CSV output.

    Returns:
        list of {"lat": float, "lon": float, "name": str}
    """
    points = []

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise ValueError("CSV file has no header.")

        headers = {h.strip().lower(): h for h in reader.fieldnames}

        lat_key = headers.get("lat") or headers.get("latitude")
        lon_key = headers.get("lon") or headers.get("longitude")
        name_key = headers.get("name")

        if not lat_key or not lon_key:
            raise ValueError(
                "CSV must contain 'lat,lon' or 'latitude,longitude' columns."
            )

        for row_number, row in enumerate(reader, start=2):
            try:
                lat = float(str(row[lat_key]).strip())
                lon = float(str(row[lon_key]).strip())
            except (TypeError, ValueError):
                print(f"Skipping invalid CSV row {row_number}: {row}")
                continue

            name = ""
            if name_key and row.get(name_key) is not None:
                name = str(row[name_key]).strip()

            points.append({"lat": lat, "lon": lon, "name": name})

    if not points:
        raise ValueError("No valid coordinates found in CSV.")

    return points


# -----------------------------
# Read KML / KMZ
# -----------------------------

def local_name(element):
    """
    Local name of an element, ignoring any namespace prefix.

    Works for KML in the default namespace, in a prefixed namespace
    (<kml:coordinates>) and without a namespace at all.
    """
    tag = element.tag

    if not isinstance(tag, str):
        return ""

    return tag.rpartition("}")[2]


def parse_coordinate_tokens(coord_text):
    """
    Parse KML coordinates text.

    KML format:
        longitude,latitude[,altitude]

    Returns:
        list of raw token parts, e.g. [["-4.725138", "33.616968", "0"], ...]

    The original longitude/latitude strings are kept untouched so no
    precision is lost when the altitude is written back.
    """
    tokens = []

    for token in (coord_text or "").split():
        parts = token.split(",")

        if len(parts) < 2:
            continue

        try:
            float(parts[0])
            float(parts[1])
        except ValueError:
            continue

        tokens.append(parts)

    return tokens


def find_placemark_name(coord_element):
    """
    Name of the Placemark owning this <coordinates> element,
    or "" when it is not inside a Placemark.
    """
    node = coord_element.getparent()

    while node is not None:
        if local_name(node) == "Placemark":
            for child in node:
                if local_name(child) == "name":
                    return (child.text or "").strip()
            return ""

        node = node.getparent()

    return ""


def collect_kml_coordinates(root):
    """
    Walk every <coordinates> element of a parsed KML tree.

    Returns:
        entries: [(element, tokens, name), ...] in document order
        points:  [{"lat": float, "lon": float, "name": str}, ...]
                 flattened in the same order as the tokens
    """
    entries = []
    points = []

    for element in root.iter():
        if local_name(element) != "coordinates":
            continue

        tokens = parse_coordinate_tokens(element.text)

        if not tokens:
            continue

        name = find_placemark_name(element)
        entries.append((element, tokens, name))

        for parts in tokens:
            points.append({
                "lat": float(parts[1]),
                "lon": float(parts[0]),
                "name": name,
            })

    if not points:
        raise ValueError("No valid coordinates found in KML.")

    return entries, points


def read_kml_points(kml_path):
    """
    Read all coordinates from a KML file.

    Returns:
        list of {"lat": float, "lon": float, "name": str}
    """
    tree = etree.parse(kml_path)
    _, points = collect_kml_coordinates(tree.getroot())
    return points


def extract_kmz_kml(kmz_path):
    """
    Return the raw KML bytes stored inside a KMZ archive.

    Prefers doc.kml, otherwise the first .kml file found.
    """
    if not kmz_path.lower().endswith(".kmz"):
        raise ValueError("Input file must be a .kmz file.")

    with zipfile.ZipFile(kmz_path, "r") as kmz:
        names = kmz.namelist()

        kml_name = None

        for name in names:
            if name.lower().endswith("doc.kml"):
                kml_name = name
                break

        if kml_name is None:
            for name in names:
                if name.lower().endswith(".kml"):
                    kml_name = name
                    break

        if kml_name is None:
            raise ValueError("No KML file found inside the KMZ archive.")

        return kmz.read(kml_name)


def kmz_to_kml(kmz_path, kml_output_path):
    """
    Convert a KMZ file to a KML file (no elevation lookup).

    A KMZ is a ZIP archive containing one main KML file,
    normally named doc.kml.
    """
    kml_content = extract_kmz_kml(kmz_path)

    with open(kml_output_path, "wb") as output_file:
        output_file.write(kml_content)

    print(f"Converted KMZ to KML:")
    print(f"  Input:  {kmz_path}")
    print(f"  Output: {kml_output_path}")

    return kml_output_path


def parse_kml(input_path):
    """
    Parse a .kml or .kmz file and return the root element of its tree.
    """
    if input_path.lower().endswith(".kmz"):
        return etree.fromstring(extract_kmz_kml(input_path))

    return etree.parse(input_path).getroot()


# -----------------------------
# Open-Elevation API
# -----------------------------

def get_elevations(points):
    """
    Request elevations from Open-Elevation in batches.

    Returns:
        list of elevations, same order as input points
    """
    elevations = []

    for start in range(0, len(points), BATCH_SIZE):
        batch = points[start:start + BATCH_SIZE]

        payload = {
            "locations": [
                {
                    "latitude": point["lat"],
                    "longitude": point["lon"]
                }
                for point in batch
            ]
        }

        print(
            f"Requesting elevations for points "
            f"{start + 1} to {start + len(batch)}..."
        )

        response = requests.post(
            API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60
        )

        response.raise_for_status()
        data = response.json()

        results = data.get("results", [])

        if len(results) != len(batch):
            raise RuntimeError(
                f"API returned {len(results)} results "
                f"for {len(batch)} requested points."
            )

        for result in results:
            elevations.append(float(result["elevation"]))

        time.sleep(0.5)

    return elevations


# -----------------------------
# Write CSV
# -----------------------------

def write_output_csv(points, elevations, output_path):
    """
    Write CSV with:
        lat,lon,elevation_m[,name]

    The 'name' column is added only when at least one point carries a
    name, so existing lat,lon,elevation_m consumers keep working.
    """
    include_name = any(point.get("name") for point in points)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["lat", "lon", "elevation_m"] + (["name"] if include_name else [])
        )

        for point, elevation in zip(points, elevations):
            row = [
                f"{point['lat']:.8f}",
                f"{point['lon']:.8f}",
                f"{elevation:.2f}",
            ]

            if include_name:
                row.append(point.get("name", ""))

            writer.writerow(row)


# -----------------------------
# Write KML
# -----------------------------

def apply_elevations(entries, elevations):
    """
    Write the elevations back into the original KML tree.

    Only the altitude of each coordinate tuple changes:
        lon,lat,OLD  ->  lon,lat,NEW

    Longitude, latitude, names, descriptions, styles, folders and
    geometry are left untouched.
    """
    index = 0

    for element, tokens, _name in entries:
        lines = []

        for parts in tokens:
            elevation = elevations[index]
            index += 1
            lines.append(f"{parts[0]},{parts[1]},{elevation:.2f}")

        element.text = "\n".join(lines) + "\n" if len(lines) > 1 else lines[0]


def write_kml_tree(root, output_path):
    """
    Save the (edited) KML tree, preserving its layout.
    """
    tree = root.getroottree()
    tree.write(
        output_path,
        encoding="UTF-8",
        xml_declaration=True,
        pretty_print=True
    )


def write_output_kml(points, elevations, output_path):
    """
    Write a new KML with one Point Placemark per coordinate.

    Used only when the input is a CSV: a KML/KMZ input keeps its own
    layout through apply_elevations().

    KML coordinate order:
        longitude,latitude,elevation
    """
    kml_root = etree.Element(
        f"{{{KML_NS}}}kml",
        nsmap={"kml": KML_NS}
    )

    document = etree.SubElement(kml_root, f"{{{KML_NS}}}Document")

    doc_name = etree.SubElement(document, f"{{{KML_NS}}}name")
    doc_name.text = "Coordinates with elevation"

    for point, elevation in zip(points, elevations):
        placemark = etree.SubElement(document, f"{{{KML_NS}}}Placemark")

        name = etree.SubElement(placemark, f"{{{KML_NS}}}name")
        name.text = point.get("name") or f"{point['lat']:.6f}, {point['lon']:.6f}"

        description = etree.SubElement(placemark, f"{{{KML_NS}}}description")
        description.text = f"Elevation: {elevation:.2f} m"

        point_element = etree.SubElement(placemark, f"{{{KML_NS}}}Point")

        coordinates = etree.SubElement(
            point_element,
            f"{{{KML_NS}}}coordinates"
        )

        coordinates.text = (
            f"{point['lon']:.8f},{point['lat']:.8f},{elevation:.2f}"
        )

    tree = etree.ElementTree(kml_root)

    tree.write(
        output_path,
        encoding="UTF-8",
        xml_declaration=True,
        pretty_print=True
    )


# -----------------------------
# Main
# -----------------------------

def main():
    if len(sys.argv) != 4:
        print("Usage:")
        print("  python kml_kmz_csv_add_elevation.py input.csv output.csv csv")
        print("  python kml_kmz_csv_add_elevation.py input.csv output.kml kml")
        print("  python kml_kmz_csv_add_elevation.py input.kml output.csv csv")
        print("  python kml_kmz_csv_add_elevation.py input.kml output.kml kml")
        print("  python kml_kmz_csv_add_elevation.py input.kmz output.kml kml")
        print("  python kml_kmz_csv_add_elevation.py input.kmz output.csv csv")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    output_type = sys.argv[3].lower()

    if output_type not in ("csv", "kml"):
        raise ValueError("Output type must be 'csv' or 'kml'.")

    input_extension = os.path.splitext(input_path)[1].lower()

    if input_extension == ".kmz":
        print("Reading KMZ input...")
        root = parse_kml(input_path)

    elif input_extension == ".kml":
        print("Reading KML input...")
        root = parse_kml(input_path)

    elif input_extension == ".csv":
        print("Reading CSV input...")
        points = read_csv_points(input_path)
        print(f"Found {len(points)} coordinates.")

        elevations = get_elevations(points)

        if output_type == "csv":
            write_output_csv(points, elevations, output_path)
            print(f"CSV with elevation saved to: {output_path}")
        else:
            write_output_kml(points, elevations, output_path)
            print(f"KML with elevation saved to: {output_path}")

        return

    else:
        raise ValueError("Input file must be .csv, .kml or .kmz")

    entries, points = collect_kml_coordinates(root)
    print(f"Found {len(points)} coordinates.")

    elevations = get_elevations(points)

    if output_type == "csv":
        write_output_csv(points, elevations, output_path)
        print(f"CSV with elevation saved to: {output_path}")

    else:
        apply_elevations(entries, elevations)
        write_kml_tree(root, output_path)
        print(f"KML with elevation saved to: {output_path}")


if __name__ == "__main__":
    main()
