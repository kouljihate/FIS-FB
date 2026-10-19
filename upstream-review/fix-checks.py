"""Regression checks for the four fixes. Usage: python fis_fix_checks.py <repo-dir> <db>
Exits non-zero when any expectation fails."""
import io
import os
import sys
import zipfile

ROOT = os.path.abspath(sys.argv[1])
DB = sys.argv[2]
os.environ["MONGO_URI"] = f"mongodb://127.0.0.1:27017/{DB}"
os.environ["SECRET_KEY"] = "check-key"
sys.path.insert(0, ROOT)

from irrigation import create_app  # noqa: E402
from irrigation.extensions import mongo  # noqa: E402
from irrigation.utils.kml import export_project_to_kml  # noqa: E402

app = create_app()
app.config.update(TESTING=True)
client = app.test_client()

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>S 1</name><Polygon><outerBoundaryIs><LinearRing>
    <coordinates>10,36 10.01,36 10.01,36.01 10,36.01 10,36</coordinates>
  </LinearRing></outerBoundaryIs></Polygon></Placemark>
  <Placemark><name>S 2</name><Polygon><outerBoundaryIs><LinearRing>
    <coordinates>10.01,36 10.02,36 10.02,36.01 10.01,36.01 10.01,36</coordinates>
  </LineargeRing></outerBoundaryIs></Polygon></Placemark>
</Document></kml>""".replace("LineargeRing", "LinearRing")

failures = []


def check(label, ok, detail=""):
    print(f"    {'PASS' if ok else 'FAIL'}  {label}{('  -> ' + str(detail)) if detail else ''}")
    if not ok:
        failures.append(label)


def flashes():
    with client.session_transaction() as s:
        msgs = list(s.get("_flashes", []))
        s["_flashes"] = []
    return [m[1] for m in msgs]


def reg(u, e, p="pw123456"):
    client.post("/register", data={"username": u, "email": e, "password": p})


def login(u, p="pw123456"):
    client.post("/login", data={"username": u, "password": p})


print(f"### checks against {ROOT}")
with app.app_context():
    mongo.cx.drop_database(DB)

print("\n1. blank-email registration")
reg("alice", "")
reg("bob", "")
with app.app_context():
    users = sorted(u["username"] for u in mongo.db.users.find())
check("two accounts can register without an email", users == ["alice", "bob"], users)
reg("carol", "carol@x.io")
reg("dave", "carol@x.io")
reg("alice", "eve@x.io")
with app.app_context():
    users = sorted(u["username"] for u in mongo.db.users.find())
check("a duplicate email is still refused", users == ["alice", "bob", "carol"], users)
check("a duplicate username is still refused", "eve" not in users, users)

print("\n2. KML export")
login("alice")
client.post("/create", data={"name": "Alpha", "boundary_coords": "[[10,36],[10.01,36],[10.01,36.01],[10,36.01],[10,36]]"})
with app.app_context():
    pid = mongo.db.projects.find_one({"name": "Alpha"})["id"]
client.post(f"/{pid}/upload_kml", data={"kml_file": (io.BytesIO(KML.encode()), "land.kml")},
            content_type="multipart/form-data")
flashes()
with app.app_context():
    direct = export_project_to_kml(pid)
check("export_project_to_kml returns a document", isinstance(direct, str) and "<kml" in direct)
r = client.get(f"/{pid}/export_kml")
check("GET export_kml downloads a KML", r.status_code == 200 and r.mimetype == "application/vnd.google-earth.kml+xml",
      f"{r.status_code} {r.mimetype}")
check("the download carries the land boundary", b"Land Boundary" in r.data)

print("\n3. cross-tenant delete")
client.get("/logout")
login("bob")
flashes()
r = client.post(f"/{pid}/delete")
msgs = flashes()
with app.app_context():
    still_there = mongo.db.projects.count_documents({"id": pid}) == 1
    kids = mongo.db.sectors.count_documents({"project_id": pid})
check("a foreign delete is refused", any("not found" in m for m in msgs), msgs)
check("the project row survives", still_there)
check("its children survive", kids >= 1, f"{kids} sectors")

print("\n4. owner delete still cascades")
client.get("/logout")
login("alice")
client.post(f"/{pid}/delete")
flashes()
with app.app_context():
    gone = mongo.db.projects.count_documents({"id": pid}) == 0
    kids = mongo.db.sectors.count_documents({"project_id": pid})
check("the owner can still delete the project", gone)
check("the cascade removes the children", kids == 0, f"{kids} left")

print("\n5. malformed uploads")
client.post("/create", data={"name": "Beta", "boundary_coords": "[[10,36],[10.01,36],[10.01,36.01],[10,36.01],[10,36]]"})
with app.app_context():
    pid2 = mongo.db.projects.find_one({"name": "Beta"})["id"]
flashes()
try:
    r = client.post(f"/{pid2}/upload_kml", data={"kml_file": (io.BytesIO(b"not xml"), "broken.kml")},
                    content_type="multipart/form-data")
    check("a .kml that is not XML is handled", r.status_code == 302 and any("Could not parse" in m for m in flashes()), r.status_code)
except Exception as exc:  # noqa: BLE001
    check("a .kml that is not XML is handled", False, f"raised {type(exc).__name__}")
try:
    r = client.post(f"/{pid2}/upload_kml", data={"kml_file": (io.BytesIO(b"not a zip"), "fake.kmz")},
                    content_type="multipart/form-data")
    check("a .kmz that is not a zip is handled", r.status_code == 302 and any("Could not parse" in m for m in flashes()), r.status_code)
except Exception as exc:  # noqa: BLE001
    check("a .kmz that is not a zip is handled", False, f"raised {type(exc).__name__}")

r = client.post(f"/{pid2}/upload_kml", data={"kml_file": (io.BytesIO(b"<b>x</b>"), "payload.html")},
                content_type="multipart/form-data")
msgs = flashes()
check("a non-KML extension is refused", any("Only .kml" in m for m in msgs), msgs)

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("doc.kml", "<kml xmlns='http://www.opengis.net/kml/2.2'><Document>"
                           + ("<Placemark><name>x</name><Point><coordinates>1,1</coordinates></Point></Placemark>" * 200000)
                           + "</Document></kml>")
blob = buf.getvalue()
print(f"    (expanding KMZ upload size: {len(blob)/1024:.0f} KB)")
try:
    r = client.post(f"/{pid2}/upload_kml", data={"kml_file": (io.BytesIO(blob), "bomb.kmz")},
                    content_type="multipart/form-data")
    msgs = flashes()
    check("a zip-bomb KMZ is refused without a server error",
          r.status_code == 302 and any("Could not parse" in m or "too many features" in m for m in msgs), msgs)
except Exception as exc:  # noqa: BLE001
    check("a zip-bomb KMZ is refused without a server error", False, f"raised {type(exc).__name__}")

print("\n6. a valid upload still imports")
r = client.post(f"/{pid2}/upload_kml", data={"kml_file": (io.BytesIO(KML.encode()), "land.kml")},
                content_type="multipart/form-data")
msgs = flashes()
with app.app_context():
    n = mongo.db.sectors.count_documents({"project_id": pid2})
check("a good KML is still imported", n >= 1 and any("Loaded" in m for m in msgs), f"{n} sectors, {msgs}")

with app.app_context():
    mongo.cx.drop_database(DB)

print(f"\n{len(failures)} failing check(s): {failures}" if failures else "\nall checks passed")
sys.exit(1 if failures else 0)
