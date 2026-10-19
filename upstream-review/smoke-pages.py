"""Smoke pass: request every GET route of the app with a live project and report 5xx."""
import io
import os
import re
import sys

ROOT = os.path.abspath(sys.argv[1])
DB = sys.argv[2]
os.environ["MONGO_URI"] = f"mongodb://127.0.0.1:27017/{DB}"
os.environ["SECRET_KEY"] = "smoke-key"
sys.path.insert(0, ROOT)

from irrigation import create_app  # noqa: E402
from irrigation.extensions import mongo  # noqa: E402

app = create_app()
app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
client = app.test_client()

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>S 1</name><Polygon><outerBoundaryIs><LinearRing>
    <coordinates>10,36 10.01,36 10.01,36.01 10,36.01 10,36</coordinates>
  </LinearRing></outerBoundaryIs></Polygon></Placemark>
  <Placemark><name>S 2</name><Polygon><outerBoundaryIs><LinearRing>
    <coordinates>10.01,36 10.02,36 10.02,36.01 10.01,36.01 10.01,36</coordinates>
  </LinearRing></outerBoundaryIs></Polygon></Placemark>
</Document></kml>"""

with app.app_context():
    mongo.cx.drop_database(DB)

client.post("/register", data={"username": "smoke", "email": "s@x.io", "password": "pw123456"})
client.post("/login", data={"username": "smoke", "password": "pw123456"})
client.post("/create", data={"name": "Smoke", "boundary_coords": "[[10,36],[10.01,36],[10.01,36.01],[10,36.01],[10,36]]"})
with app.app_context():
    pid = mongo.db.projects.find_one({"name": "Smoke"})["id"]
client.post(f"/{pid}/upload_kml", data={"kml_file": (io.BytesIO(KML.encode()), "land.kml")},
            content_type="multipart/form-data")

with app.app_context():
    doc = mongo.db.projects.find_one({"id": pid})
    for collection, field in (("sectors", "id"), ("zones", "id"), ("tree_rows", "id"),
                              ("trees", "id"), ("network_elements", "id")):
        record = mongo.db[collection].find_one({"project_id": pid})
        print(f"  seeded {collection}: {record.get(field) if record else None}")

rules = sorted({r.rule for r in app.url_map.iter_rules() if "GET" in r.methods})
params = {"project_id": str(pid), "sector_id": "1", "zone_id": "1", "row_id": "1", "tree_id": "1",
          "element_id": "1", "doc_index": "0", "item_id": "1", "resource": "sectors", "record_id": "1"}
bad = []
for rule in rules:
    if rule.startswith("/static"):
        continue
    url = rule
    for name in set(re.findall(r"<(?:[^:>]+:)?([^>]+)>", rule)):
        url = re.sub(r"<(?:[^:>]+:)?" + name + r">", params.get(name, "1"), url, count=1)
    try:
        r = client.get(url, follow_redirects=False)
        code = r.status_code
    except Exception as exc:  # noqa: BLE001
        code = f"RAISED {type(exc).__name__}: {exc}"
    flag = "  " if isinstance(code, int) and code < 500 else "!!"
    if not (isinstance(code, int) and code < 500):
        bad.append((url, code))
    print(f"  {flag} {code}  GET {url}")

with app.app_context():
    mongo.cx.drop_database(DB)
print(f"\n{len(bad)} failing route(s): {bad}" if bad else "\nno route returned a server error")
sys.exit(1 if bad else 0)
