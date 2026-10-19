"""End-to-end tests through the HTTP interface.

These drive the app the way a browser does, so a passing run demonstrates that the
templates render, the forms persist and the access checks hold.
"""

from __future__ import annotations

import io
from pathlib import Path

from bson import ObjectId
from conftest import kml_document, kml_upload

MAIN_TABS = ["Project", "Water Resource", "Geometry", "Hydrology1", "Trees",
             "Hydrology2", "Estimation", "Report"]


# ---------------------------------------------------------------- auth & nav --


def test_anonymous_user_is_sent_to_the_login_page(client):
    response = client.get("/projects")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_root_redirects_to_login_when_anonymous(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_first_account_becomes_full_admin(registered, db):
    user = db["users"].find_one({"username": "tester"})
    assert user is not None
    assert user["role"] == "full_admin"
    assert user["password_hash"] != "secret-pass"


def test_second_account_is_a_customer(client, registered, app):
    registered.post("/auth/logout")
    response = client.post(
        "/auth/register",
        data={"username": "second", "password": "secret-pass",
              "confirm_password": "secret-pass"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        from irrigation.extensions import mongo
        user = mongo.db["users"].find_one({"username": "second"})
    assert user["role"] == "customer"


def test_all_main_tabs_render_with_the_required_order(registered):
    html = registered.get("/projects").get_data(as_text=True)
    positions = [html.index(label) for label in MAIN_TABS]
    assert positions == sorted(positions), "main tabs are not in the required order"


def test_wrong_password_is_rejected(client, registered):
    registered.post("/auth/logout")
    response = client.post("/auth/login",
                           data={"username": "tester", "password": "wrong"})
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data


def test_logout_clears_the_session(registered):
    registered.post("/auth/logout")
    assert registered.get("/projects").status_code == 302


def test_language_switch_to_arabic_sets_rtl(registered):
    registered.post("/auth/language", data={"lang": "ar", "next": "/projects"})
    html = registered.get("/projects").get_data(as_text=True)
    assert 'dir="rtl"' in html


def test_chrome_is_bilingual_and_has_no_language_picker(registered):
    """Both languages are always printed, so the picker is gone from the chrome."""
    html = registered.get("/projects").get_data(as_text=True)
    assert 'name="lang"' not in html
    assert "/auth/language" not in html
    # The same screen carries a label in English and in Arabic.
    assert "Projects" in html and "المشاريع" in html


def test_arabic_stacks_above_english_in_buttons_tabs_and_headers(project_id, registered):
    """Buttons, tabs and table headers print Arabic first, English below."""
    tabs = registered.get("/projects").get_data(as_text=True)
    assert tabs.index("bilingual-button-ar") < tabs.index("bilingual-button-en"), \
        "tab/button text must put Arabic above English"

    report = registered.get(f"/report/{project_id}/technical").get_data(as_text=True)
    assert report.index("bilingual-stack-ar") < report.index("bilingual-stack-en"), \
        "table headers must put Arabic above English"


def test_headings_put_english_left_and_arabic_right(client):
    """Headings and labels keep English on the left and Arabic on the right."""
    html = client.get("/auth/login").get_data(as_text=True)
    assert html.index("bilingual-heading-en") < html.index("bilingual-heading-ar")
    assert html.index("bilingual-label-en") < html.index("bilingual-label-ar")


# ------------------------------------------------------------------- projects --


def test_project_is_created_with_land_metrics(project_id, db):
    project = db["projects"].find_one({"_id": ObjectId(project_id)})
    metrics = project["land"]["metrics"]
    # ~187 m x ~266 m -> about 5 ha
    assert 4.5 < metrics["area_ha"] < 5.5
    assert metrics["vertices"] == 4


def test_creating_a_project_requires_a_map(registered):
    """The land map is mandatory: it is what links a project to its land."""
    response = registered.post("/projects/new", data={"name": "No Map"},
                               follow_redirects=True)
    assert b"A map file is required" in response.data


def test_unsupported_map_format_is_rejected(registered):
    response = registered.post(
        "/projects/new",
        data={"name": "Bad Map", "map_file": (io.BytesIO(b"hello"), "land.txt")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"Map rejected" in response.data


def test_map_without_an_outline_is_rejected(registered):
    response = registered.post(
        "/projects/new",
        data={"name": "Empty KML",
              "map_file": (io.BytesIO(b"<kml/>"), "land.kml")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"Map rejected" in response.data


def test_malformed_map_is_rejected(registered):
    response = registered.post(
        "/projects/new",
        data={"name": "Broken KML",
              "map_file": (io.BytesIO(b"<kml><unclosed>"), "land.kml")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"Map rejected" in response.data


def test_map_is_linked_and_the_boundary_comes_from_it(project_id, db, app):
    project = db["projects"].find_one({"_id": ObjectId(project_id)})
    linked = project["map"]
    assert linked["kind"] == "kml"
    assert linked["original_name"] == "land.kml"
    assert project["land"]["geographic"] is True
    assert project["land"]["metrics"]["vertices"] == 4

    # The metadata is mirrored in its own collection, so a project teardown
    # removes it through the ordinary cascade.
    record = db["kml_files"].find_one({"project_id": ObjectId(project_id)})
    assert record and record["stored_name"] == linked["stored_name"]

    # The uploaded file itself is kept under the project's upload folder.
    stored = Path(app.config["UPLOAD_FOLDER"]) / project_id / linked["stored_name"]
    assert stored.is_file()
    assert b"<kml" in stored.read_bytes()


def test_linked_map_can_be_downloaded(project_id, registered):
    response = registered.get(f"/projects/{project_id}/map")
    assert response.status_code == 200
    assert b"<kml" in response.data
    assert "attachment" in response.headers["Content-Disposition"]
    assert "land.kml" in response.headers["Content-Disposition"]


def test_project_cards_offer_edit_and_remove(project_id, registered):
    html = registered.get("/projects").get_data(as_text=True)
    assert f"/projects/{project_id}/edit" in html
    assert f"/projects/{project_id}/delete" in html


def test_project_can_be_edited(project_id, registered, db):
    response = registered.post(
        f"/projects/{project_id}/edit",
        data={"name": "Renamed Block", "description": "updated"},
        content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    doc = db["projects"].find_one({"_id": ObjectId(project_id)})
    assert doc["name"] == "Renamed Block"
    assert doc["description"] == "updated"
    # Editing the text fields leaves the linked map and the boundary alone.
    assert doc["map"]["original_name"] == "land.kml"
    assert 4.5 < doc["land"]["metrics"]["area_ha"] < 5.5


def test_editing_can_replace_the_map_and_redraw_the_boundary(project_id, registered, db):
    bigger = "8.1300,36.7220 8.1330,36.7220 8.1330,36.7180 8.1300,36.7180"
    registered.post(
        f"/projects/{project_id}/edit",
        data={"name": "Test Block", "map_file": kml_upload(bigger, "bigger.kml")},
        content_type="multipart/form-data", follow_redirects=True)
    doc = db["projects"].find_one({"_id": ObjectId(project_id)})
    assert doc["map"]["original_name"] == "bigger.kml"
    assert doc["land"]["metrics"]["area_ha"] > 5.5
    # Replacing keeps exactly one linked-map record.
    assert db["kml_files"].count_documents({"project_id": ObjectId(project_id)}) == 1


def test_editing_can_override_the_boundary_by_hand(project_id, registered, db):
    registered.post(
        f"/projects/{project_id}/edit",
        data={"name": "Test Block",
              "boundary": "200000,3200000 200100,3200000 200100,3200050 200000,3200050"},
        content_type="multipart/form-data", follow_redirects=True)
    doc = db["projects"].find_one({"_id": ObjectId(project_id)})
    # Values far outside +/-180 / +/-90 are metres, so the 100 x 50 m ring is
    # half a hectare rather than degrees.
    assert doc["land"]["geographic"] is False
    assert doc["land"]["metrics"]["area_ha"] == 0.5


def test_removing_a_project_deletes_it_and_its_records(project_id, registered, db):
    registered.post(f"/geometry/{project_id}/sectors/generate",
                    data={"count": "2"}, follow_redirects=True)
    assert db["sectors"].count_documents({"project_id": ObjectId(project_id)}) == 2

    response = registered.post(f"/projects/{project_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    # The project document itself must be gone, not only its children: removing
    # it used to leave the project in the list.
    assert db["projects"].find_one({"_id": ObjectId(project_id)}) is None
    assert db["sectors"].count_documents({"project_id": ObjectId(project_id)}) == 0
    assert db["kml_files"].count_documents({"project_id": ObjectId(project_id)}) == 0
    assert "Test Block" not in response.get_data(as_text=True)


def test_a_customer_owner_can_edit_and_remove_their_own_project(client, registered, db):
    """A non-admin owner keeps control of the project they created.

    Regression: the remove route compared the project's ``owner_id`` (an
    ObjectId) with the session user's id (a string) and so refused every owner
    who was not a full admin, even though the card offered them the button.
    """
    registered.post("/auth/logout")
    client.post("/auth/register",
                data={"username": "customer-one", "password": "secret-pass",
                      "confirm_password": "secret-pass"},
                follow_redirects=True)
    client.post("/projects/new",
                data={"name": "Own Block", "map_file": kml_upload()},
                content_type="multipart/form-data", follow_redirects=True)
    doc = db["projects"].find_one({"name": "Own Block"})
    assert doc is not None, "the customer could not create a project"
    pid = str(doc["_id"])

    html = client.get("/projects").get_data(as_text=True)
    assert f"/projects/{pid}/delete" in html, "the owner was not offered Remove"

    client.post(f"/projects/{pid}/edit", data={"name": "Own Block Renamed"},
                content_type="multipart/form-data", follow_redirects=True)
    assert db["projects"].find_one({"_id": doc["_id"]})["name"] == "Own Block Renamed"

    client.post(f"/projects/{pid}/delete", follow_redirects=True)
    assert db["projects"].find_one({"_id": doc["_id"]}) is None


# ------------------------------------------------------------------- geometry --


def test_generate_sectors_partitions_the_land(project_id, registered, db):
    response = registered.post(f"/geometry/{project_id}/sectors/generate",
                               data={"count": "3"}, follow_redirects=True)
    assert response.status_code == 200
    sectors = list(db["sectors"].find({"project_id": ObjectId(project_id)}))
    assert len(sectors) == 3
    total_ha = sum(s["metrics"]["area_ha"] for s in sectors)
    assert 4.5 < total_ha < 5.5
    for sector in sectors:
        assert sector["metrics"]["area_m2"] > 0
        assert len(sector["boundary"]) >= 3


def test_generate_zones_subdivides_each_sector(project_id, registered, db):
    registered.post(f"/geometry/{project_id}/sectors/generate",
                    data={"count": "2"}, follow_redirects=True)
    registered.post(f"/geometry/{project_id}/zones/generate",
                    data={"count": "2"}, follow_redirects=True)
    assert db["zones"].count_documents({"project_id": ObjectId(project_id)}) == 4


def test_split_sector_creates_an_extra_row(project_id, registered, db):
    registered.post(f"/geometry/{project_id}/sectors/generate",
                    data={"count": "1"}, follow_redirects=True)
    sector = db["sectors"].find_one({"project_id": ObjectId(project_id)})
    before = sector["metrics"]["area_ha"]

    registered.post(f"/geometry/{project_id}/sectors/{sector['_id']}/split",
                    data={"mode": "x", "ratio": "0.5"}, follow_redirects=True)

    sectors = list(db["sectors"].find({"project_id": ObjectId(project_id)}))
    assert len(sectors) == 2
    assert sum(s["metrics"]["area_ha"] for s in sectors) > before * 0.95


def test_merge_and_swap_sectors(project_id, registered, db):
    registered.post(f"/geometry/{project_id}/sectors/generate",
                    data={"count": "4"}, follow_redirects=True)
    ids = [str(s["_id"]) for s in db["sectors"].find({"project_id": ObjectId(project_id)})]

    registered.post(f"/geometry/{project_id}/sectors/merge",
                    data={"item_ids": [ids[0], ids[1]], "keep": ids[0]},
                    follow_redirects=True)
    assert db["sectors"].count_documents({"project_id": ObjectId(project_id)}) == 3

    remaining = [str(s["_id"]) for s in
                 db["sectors"].find({"project_id": ObjectId(project_id)})]
    first = db["sectors"].find_one({"_id": ObjectId(remaining[0])})
    second = db["sectors"].find_one({"_id": ObjectId(remaining[1])})

    registered.post(f"/geometry/{project_id}/sectors/swap",
                    data={"item_ids": remaining[:2]}, follow_redirects=True)
    after_first = db["sectors"].find_one({"_id": ObjectId(remaining[0])})
    after_second = db["sectors"].find_one({"_id": ObjectId(remaining[1])})

    assert after_first["name"] == first["name"]          # names stay put
    assert after_first["boundary"] == second["boundary"]  # boundaries move
    assert after_second["boundary"] == first["boundary"]


def test_duplicate_sector_name_is_refused(project_id, registered, db):
    registered.post(f"/geometry/{project_id}/sectors/add", data={"name": "Alpha"},
                    follow_redirects=True)
    response = registered.post(f"/geometry/{project_id}/sectors/add",
                               data={"name": "Alpha"}, follow_redirects=True)
    assert b"already exists" in response.data
    assert db["sectors"].count_documents({"project_id": ObjectId(project_id)}) == 1


# ------------------------------------------------------- water / hydrology / trees --


def test_water_resource_kind_pages(project_id, registered, db):
    registered.post(f"/water-resource/{project_id}/add",
                    data={"kind": "well", "name": "North well",
                          "flow_m3h": "25", "depth_m": "80"},
                    follow_redirects=True)
    response = registered.get(f"/water-resource/{project_id}/well")
    assert response.status_code == 200
    assert b"North well" in response.data

    doc = db["water_resources"].find_one({"project_id": ObjectId(project_id)})
    assert doc["kind"] == "well"
    assert doc["flow_m3h"] == 25.0


def test_pipe_pages_report_velocity_and_head_loss(project_id, registered):
    registered.post(f"/hydrology/{project_id}/add",
                    data={"level": "MP", "name": "Main 1",
                          "diameter_mm": "90", "length_m": "200",
                          "material": "pvc"},
                    follow_redirects=True)
    html = registered.get(f"/hydrology/{project_id}/main-pipe").get_data(as_text=True)
    assert "Main 1" in html
    assert "Velocity" in html


def test_pipe_documents_carry_their_own_level(project_id, registered):
    """The level must be stored as a field, not only as the ``kind`` index."""
    for level, diameter, length in (("MP", "90", "180"), ("SP", "63", "120"),
                                    ("ZP", "32", "60")):
        registered.post(f"/hydrology/{project_id}/add",
                        data={"level": level, "name": f"{level} 1",
                              "diameter_mm": diameter, "length_m": length},
                        follow_redirects=True)
    items = registered.get(
        f"/api/project/{project_id}/collection/pipes").get_json()["items"]
    assert sorted(item["level"] for item in items) == ["MP", "SP", "ZP"]
    assert sorted(item["kind"] for item in items) == ["MP", "SP", "ZP"]


def test_bilan_head_is_physically_sane(project_id, registered):
    """Regression: the report once summed every pipe at the full block flow in
    series and reported about 3 970 m of total dynamic head for a 5 ha block."""
    import re

    registered.post(f"/geometry/{project_id}/sectors/generate",
                    data={"count": "4"}, follow_redirects=True)
    registered.post(f"/geometry/{project_id}/zones/generate",
                    data={"count": "3"}, follow_redirects=True)
    for level, diameter, length in (("MP", "90", "180"), ("SP", "63", "120"),
                                    ("ZP", "32", "60")):
        registered.post(f"/hydrology/{project_id}/add",
                        data={"level": level, "name": f"{level} 1",
                              "diameter_mm": diameter, "length_m": length},
                        follow_redirects=True)

    html = registered.get(f"/report/{project_id}/bilan-hydrique").get_data(as_text=True)

    # The row labels are bilingual, so markup (the stacked English/Arabic spans)
    # sits between the label and the value cell; match across it.
    head = re.search(r"Total dynamic head.*?</th>\s*<td[^>]*>([0-9.]+) m", html, re.S)
    friction = re.search(r"Pipe friction head.*?</th>\s*<td[^>]*>([0-9.]+) m", html, re.S)
    assert head and friction, "bilan did not render the pump duty figures"
    assert float(friction.group(1)) < 50, friction.group(1)
    assert 20 < float(head.group(1)) < 120, head.group(1)

    # The zone pipe carries one zone's share of the block flow, not all of it, so
    # its velocity must stay inside the design band.
    row = re.search(r"<tr>\s*<td>ZP</td>(.*?)</tr>", html, re.S)
    assert row, "critical path table did not render the ZP row"
    numbers = re.findall(r"[0-9]+\.[0-9]+", row.group(1))
    # The row reads: flow, diameter, velocity, head loss.
    assert len(numbers) >= 4, numbers
    assert float(numbers[2]) < 2.0, numbers


def test_rows_and_trees_can_be_generated(project_id, registered, db):
    registered.post(f"/geometry/{project_id}/sectors/generate",
                    data={"count": "1"}, follow_redirects=True)
    registered.post(f"/trees/{project_id}/rows/generate",
                    data={"spacing_m": "50"}, follow_redirects=True)
    rows = list(db["rows"].find({"project_id": ObjectId(project_id)}))
    assert len(rows) >= 2

    registered.post(f"/trees/{project_id}/trees/generate",
                    data={"spacing_m": "20"}, follow_redirects=True)
    trees = list(db["trees"].find({"project_id": ObjectId(project_id)}))
    assert trees, "no trees were generated"
    for tree in trees:
        assert tree["row_id"] is not None
        assert tree["lat"] is not None and tree["lon"] is not None


# ------------------------------------------------------- estimation and report --


def test_estimation_totals_chain(project_id, registered):
    response = registered.post(
        f"/estimation/{project_id}/configuration",
        data={
            "item_name": ["Dripline", "Valves"],
            "item_unit": ["m", "unit"],
            "item_quantity": ["1000", "10"],
            "item_price": ["0.5", "20"],
            "discount_percent": "10",
            "tax_percent": "19",
            "notes": "quotation",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    html = registered.get(f"/estimation/{project_id}/result").get_data(as_text=True)
    # subtotal 500 + 200 = 700 ; discount 70 ; taxable 630 ; tax 119.7 ; total 749.7
    # The whole chain must be visible so the figures can be audited.
    assert "700" in html          # subtotal
    assert "-70" in html           # discount
    assert "630" in html           # taxable amount
    assert "119.7" in html         # tax
    assert "749.7" in html         # total


def test_report_pages_render(project_id, registered):
    for path in ("charts", "bilan-hydrique", "technical"):
        response = registered.get(f"/report/{project_id}/{path}")
        assert response.status_code == 200, path


def test_bilan_hydrique_uses_the_drawn_area(project_id, registered):
    registered.post(f"/geometry/{project_id}/sectors/generate",
                    data={"count": "2"}, follow_redirects=True)
    html = registered.get(f"/report/{project_id}/bilan-hydrique").get_data(as_text=True)
    assert "Net irrigation requirement" in html
    assert "Peak design flow" in html


# ------------------------------------------------------------------------ API --


def test_geometry_api_returns_sectors_with_zones(project_id, registered):
    registered.post(f"/geometry/{project_id}/sectors/generate",
                    data={"count": "2"}, follow_redirects=True)
    registered.post(f"/geometry/{project_id}/zones/generate",
                    data={"count": "2"}, follow_redirects=True)

    payload = registered.get(f"/api/project/{project_id}/geometry").get_json()
    assert len(payload["sectors"]) == 2
    assert all(len(sector["zones"]) == 2 for sector in payload["sectors"])
    assert payload["project"]["land"]["metrics"]["area_ha"] > 0


def test_summary_api(project_id, registered):
    payload = registered.get(f"/api/project/{project_id}/summary").get_json()
    assert payload["project"]["id"] == project_id
    assert "stats" in payload
    assert payload["geometry"]["sector_area_ha"] == 0.0


def test_api_rejects_an_unknown_collection(project_id, registered):
    response = registered.get(f"/api/project/{project_id}/collection/users")
    assert response.status_code == 404


def test_api_requires_authentication(client, project_id):
    client.post("/auth/logout")
    assert client.get(f"/api/project/{project_id}/summary").status_code == 401


# ------------------------------------------------------------------ isolation --


def test_another_account_cannot_open_the_project(client, registered, app, project_id):
    registered.post("/auth/logout")
    with app.app_context():
        from irrigation.extensions import mongo
        from irrigation.models import User
        User.create("outsider", "secret-pass", role="customer")

    client.post("/auth/login", data={"username": "outsider", "password": "secret-pass"})
    response = client.get(f"/projects/{project_id}")
    assert response.status_code == 302
    assert f"/projects/{project_id}" not in response.headers["Location"]

    assert client.get(f"/api/project/{project_id}/geometry").status_code == 404


def test_unknown_project_redirects_instead_of_crashing(registered):
    response = registered.get("/projects/66f000000000000000000000")
    assert response.status_code == 302


def test_malformed_project_id_redirects(registered):
    assert registered.get("/projects/not-an-object-id").status_code == 302
