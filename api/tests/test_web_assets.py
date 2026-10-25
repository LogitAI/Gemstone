"""
Bundled web clients (SPEC S1.9; issue #83). Needs no model: only static routes are requested.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from api.src.main import server

ROOT = Path(__file__).resolve().parents[2]


def test_static_routes_are_served():
    client = TestClient(server.app)
    assert client.get("/").status_code == 200
    assert client.get("/chat").status_code == 200
    assert client.get("/webpack/gemstone.html").status_code == 200
    assert client.get("/static/index.css").status_code == 200
    r = client.get("/composeResources/some/file.png", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/webpack/composeResources/some/file.png"


def test_assets_are_not_under_a_test_directory():
    assert not (ROOT / "api" / "src" / "test").exists()
    assert (ROOT / "api" / "src" / "main" / "webpack" / "gemstone.html").is_file()
    assert (ROOT / "api" / "src" / "main" / "static" / "index.html").is_file()
