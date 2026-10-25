"""
Read a KML file, add elevation from Open-Elevation,
and create a new KML file with elevation values.

Usage:
    python kml_add_elevation.py input.kml output_with_elevation.kml
"""

import sys
import time
import requests
from lxml import etree

KML_NS = "http://www.opengis.net/kml/2.2"
NS = {"kml": KML_NS}

API_URL = "https://api.open-elevation.com/api/v1/lookup"
BATCH_SIZE = 100  # safe batch size


def parse_coord_text(coord_text):
    """Parse KML coordinates text: 'lon,lat[,alt] lon,lat[,alt] ...'"""
    coords = []

    for token in coord_text.strip().split():
        parts = token.split(",")

        if len(parts) < 2:
            continue

        lon = float(parts[0])
        lat = float(parts[1])

        # Keep any original altitude, but it will be replaced later
        alt = float(parts[2]) if len(parts) >= 3 else None

        coords.append({
            "lon": lon,
            "lat": lat,
            "old_alt": alt
        })

    return coords


def get_elevations(points):
    """
    Request elevations from Open-Elevation in batches.
    points: list of dicts with 'lat' and 'lon'
    Returns list of elevations in the same order.
    """
    elevations = []

    for start in range(0, len(points), BATCH_SIZE):
        batch = points[start:start + BATCH_SIZE]

        payload = {
            "locations": [
                {
                    "latitude": p["lat"],
                    "longitude": p["lon"]
                }
                for p in batch
            ]
        }

        response = requests.post(API_URL, json=payload, timeout=60)
        response.raise_for_status()

        data = response.json()
        results = data.get("results", [])

        if len(results) != len(batch):
            raise RuntimeError(
                f"API returned {len(results)} results for {len(batch)} points"
            )

        for result in results:
            elevations.append(float(result["elevation"]))

        # Be polite to the public API
        time.sleep(0.5)

    return elevations


def format_coord(p):
    """Format KML coordinate as lon,lat,elevation."""
    return f"{p['lon']:.8f},{p['lat']:.8f},{p['elevation']:.2f}"


def process_kml(input_path, output_path):
    parser = etree.XMLParser(remove_blank_text=True)
    tree = etree.parse(input_path, parser)
    root = tree.getroot()

    # Find every <coordinates> element in the KML
    coord_elements = root.findall(".//kml:coordinates", NS)

    if not coord_elements:
        raise ValueError("No <coordinates> elements found in the KML file.")

    all_points = []
    groups = []

    # First pass: collect all coordinates
    for coord_el in coord_elements:
        points = parse_coord_text(coord_el.text or "")
        groups.append((coord_el, points))
        all_points.extend(points)

    print(f"Found {len(all_points)} coordinates.")
    print("Requesting elevations from Open-Elevation...")

    elevations = get_elevations(all_points)

    # Add elevation back to each point
    index = 0
    for coord_el, points in groups:
        for point in points:
            point["elevation"] = elevations[index]
            index += 1

        coord_el.text = " ".join(format_coord(p) for p in points)

    tree.write(
        output_path,
        encoding="UTF-8",
        xml_declaration=True,
        pretty_print=True
    )

    print(f"Done. Output saved to: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage:")
        print("  python kml_add_elevation.py input.kml output.kml")
        sys.exit(1)

    input_kml = sys.argv[1]
    output_kml = sys.argv[2]

    process_kml(input_kml, output_kml)