"""Shared pytest fixtures.

Database-backed tests run against a dedicated ``fis_freebuff_test`` database that
is dropped before and after each test, so the tests can never touch the data the
running application uses.  If MongoDB is unreachable the database tests are
skipped with a clear reason instead of failing the whole suite.
"""

from __future__ import annotations

import io
import shutil
import tempfile

import pytest
from pymongo import MongoClient

from irrigation import create_app
from irrigation.extensions import mongo

TEST_DB = "fis_freebuff_test"
MONGO_URI = "mongodb://127.0.0.1:27017"

PROJECT_BOUNDARY = (
    "8.13010,36.72130 8.13220,36.72130 8.13220,36.71890 8.13010,36.71890"
)


def kml_document(boundary: str = PROJECT_BOUNDARY, *, name: str = "parcel") -> bytes:
    """A minimal KML whose polygon carries ``boundary`` as lon,lat pairs.

    A project cannot be created without a map, so every test that wants a
    project uploads one of these.
    """
    pairs = boundary.split()
    ring = " ".join(f"{pair},0" for pair in pairs) + f" {pairs[0]},0"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>'
        f"<name>{name}</name><Polygon><outerBoundaryIs><LinearRing><coordinates>"
        f"{ring}</coordinates></LinearRing></outerBoundaryIs></Polygon>"
        "</Placemark></Document></kml>"
    ).encode("utf-8")


def kml_upload(boundary: str = PROJECT_BOUNDARY, filename: str = "land.kml"):
    """A ``(file, name)`` pair for the ``map_file`` field of a multipart post."""
    return (io.BytesIO(kml_document(boundary)), filename)


@pytest.fixture(scope="session")
def mongo_server():
    """Session-wide check that a MongoDB server is actually listening."""
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
    try:
        client.admin.command("ping")
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"MongoDB not reachable at {MONGO_URI}: {exc}")
    finally:
        client.close()
    return True


@pytest.fixture()
def app(mongo_server):
    application = create_app("testing")
    # Uploaded maps are written to disk, so the tests get their own folder; the
    # application's uploads/ directory stays untouched by a test run.
    uploads = tempfile.mkdtemp(prefix="fis-uploads-")
    application.config.update(MONGO_DBNAME=TEST_DB, SECRET_KEY="test-secret",
                              UPLOAD_FOLDER=uploads)

    with application.app_context():
        mongo.db.client.drop_database(TEST_DB)
    yield application
    with application.app_context():
        mongo.db.client.drop_database(TEST_DB)
    shutil.rmtree(uploads, ignore_errors=True)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    with app.app_context():
        yield mongo.db


@pytest.fixture()
def registered(client):
    """Register the first account (which becomes Full Admin) and sign in."""
    response = client.post(
        "/auth/register",
        data={
            "username": "tester",
            "password": "secret-pass",
            "confirm_password": "secret-pass",
            "email": "tester@example.com",
            "full_name": "Test User",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    return client


@pytest.fixture()
def project_id(registered, db):
    """Create a project from a land map and return its id."""
    registered.post(
        "/projects/new",
        data={"name": "Test Block", "description": "fixture",
              "map_file": kml_upload()},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    doc = db["projects"].find_one({"name": "Test Block"})
    assert doc is not None, "project fixture failed to create a project"
    return str(doc["_id"])
