"""
Read KML or CSV coordinates, get elevation from Open-Elevation,
and save the result as either CSV or KML.

Usage:
    python kml_csv_elevation.py input.csv output.csv csv
    python kml_csv_elevation.py input.csv output.kml kml
    python kml_csv_elevation.py input.kml output.csv csv
    python kml_csv_elevation.py input.kml output.kml kml
"""

import csv
import os
import sys
import time
import requests
from lxml import etree

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

    Returns:
        list of {"lat": float, "lon": float}
    """
    points = []

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise ValueError("CSV file has no header.")

        headers = {h.strip().lower(): h for h in reader.fieldnames}

        lat_key = headers.get("lat") or headers.get("latitude")
        lon_key = headers.get("lon") or headers.get("longitude")

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

            points.append({"lat": lat, "lon": lon})

    if not points:
        raise ValueError("No valid coordinates found in CSV.")

    return points


# -----------------------------
# Read KML
# -----------------------------

def parse_kml_coordinates(coord_text):
    """
    Parse KML coordinates text.

    KML format:
        longitude,latitude[,altitude]

    Returns:
        list of {"lat": float, "lon": float}
    """
    points = []

    for token in (coord_text or "").strip().split():
        parts = token.split(",")

        if len(parts) < 2:
            continue

        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue

        points.append({"lat": lat, "lon": lon})

    return points


def read_kml_points(kml_path):
    """
    Read all coordinates from KML.

    Returns:
        list of {"lat": float, "lon": float}
    """
    tree = etree.parse(kml_path)
    root = tree.getroot()

    coord_elements = root.findall(".//kml:coordinates", NS)

    if not coord_elements:
        raise ValueError("No <coordinates> elements found in KML.")

    points = []

    for coord_element in coord_elements:
        points.extend(parse_kml_coordinates(coord_element.text))

    if not points:
        raise ValueError("No valid coordinates found in KML.")

    return points


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
        lat,lon,elevation_m
    """
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["lat", "lon", "elevation_m"])

        for point, elevation in zip(points, elevations):
            writer.writerow([
                f"{point['lat']:.8f}",
                f"{point['lon']:.8f}",
                f"{elevation:.2f}"
            ])


# -----------------------------
# Write KML
# -----------------------------

def write_output_kml(points, elevations, output_path):
    """
    Write KML with one Point Placemark per coordinate.

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
        name.text = f"{point['lat']:.6f}, {point['lon']:.6f}"

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
        print("  python kml_csv_elevation.py input.csv output.csv csv")
        print("  python kml_csv_elevation.py input.csv output.kml kml")
        print("  python kml_csv_elevation.py input.kml output.csv csv")
        print("  python kml_csv_elevation.py input.kml output.kml kml")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    output_type = sys.argv[3].lower()

    if output_type not in ("csv", "kml"):
        raise ValueError("Output type must be 'csv' or 'kml'.")

    input_extension = os.path.splitext(input_path)[1].lower()

    if input_extension == ".csv":
        print("Reading CSV input...")
        points = read_csv_points(input_path)

    elif input_extension == ".kml":
        print("Reading KML input...")
        points = read_kml_points(input_path)

    else:
        raise ValueError("Input file must be .csv or .kml")

    print(f"Found {len(points)} coordinates.")

    elevations = get_elevations(points)

    if output_type == "csv":
        write_output_csv(points, elevations, output_path)
        print(f"CSV with elevation saved to: {output_path}")

    elif output_type == "kml":
        write_output_kml(points, elevations, output_path)
        print(f"KML with elevation saved to: {output_path}")


if __name__ == "__main__":
    main()