#!/usr/bin/env python3
"""Update KML coordinate altitudes from a CSV elevation table.
Usage:
  python update_kml_elevations.py input.kml elevations.csv output.kml

CSV fields required:
  longitude, latitude, elevation_m
Optional fields are ignored.
"""
import csv
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

KML_NS = "http://www.opengis.net/kml/2.2"
NS = {"k": KML_NS}
ET.register_namespace("", KML_NS)


def load_elevations(csv_file: Path, decimals: int = 7) -> dict[tuple[float, float], float]:
    elevations = {}
    with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"longitude", "latitude", "elevation_m"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"CSV must contain columns: {', '.join(sorted(required))}")
        for line_no, row in enumerate(reader, start=2):
            try:
                lon = round(float(row["longitude"]), decimals)
                lat = round(float(row["latitude"]), decimals)
                elevation = float(row["elevation_m"])
                elevations[(lon, lat)] = elevation
            except (TypeError, ValueError) as e:
                raise ValueError(f"Invalid CSV values on line {line_no}: {row}") from e
    return elevations


def format_elevation(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")


def update_coordinate_text(text: str, elevations: dict, decimals: int = 7):
    updated, matched, unmatched = [], 0, []
    for token in text.split():
        values = token.split(",")
        if len(values) < 2:
            updated.append(token)
            continue
        try:
            lon, lat = float(values[0]), float(values[1])
        except ValueError:
            updated.append(token)
            continue
        key = (round(lon, decimals), round(lat, decimals))
        if key in elevations:
            updated.append(f"{values[0]},{values[1]},{format_elevation(elevations[key])}")
            matched += 1
        else:
            updated.append(token)
            unmatched.append((lon, lat))
    return "\n              " + "\n              ".join(updated) + "\n            ", matched, unmatched


def update_kml(input_kml: Path, elevation_csv: Path, output_kml: Path, decimals: int = 7):
    elevations = load_elevations(elevation_csv, decimals)
    tree = ET.parse(input_kml)
    root = tree.getroot()
    matched_total, unmatched = 0, []
    for node in root.findall(".//k:coordinates", NS):
        if not node.text:
            continue
        node.text, matched, missing = update_coordinate_text(node.text, elevations, decimals)
        matched_total += matched
        unmatched.extend(missing)
    tree.write(output_kml, encoding="UTF-8", xml_declaration=True)
    unique_unmatched = sorted({(round(x, decimals), round(y, decimals)) for x, y in unmatched})
    return matched_total, unique_unmatched, len(elevations)


def main():
    if len(sys.argv) != 4:
        print(__doc__.strip())
        raise SystemExit(2)
    input_kml = Path(sys.argv[1])
    elevation_csv = Path(sys.argv[2])
    output_kml = Path(sys.argv[3])
    matched, unmatched, csv_points = update_kml(input_kml, elevation_csv, output_kml)
    print(f"CSV elevation points loaded: {csv_points}")
    print(f"KML coordinates updated: {matched}")
    print(f"KML coordinates without a CSV elevation: {len(unmatched)}")
    if unmatched:
        print("First unmatched coordinates:")
        for lon, lat in unmatched[:20]:
            print(f"  {lon},{lat}")
    print(f"Written: {output_kml}")

if __name__ == "__main__":
    main()
