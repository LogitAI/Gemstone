"""
Origin policy and API key (SPEC S1.1; issue #116). No model: a tiny app carries the middleware,
and the real app is only asked for static assets and `/api/version`.
"""
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.src.main import security, server

FOREIGN = "http://evil.example"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("GEMSTONE_API_KEY", raising=False)
    monkeypatch.delenv("GEMSTONE_ORIGINS", raising=False)


def make_app():
    app = FastAPI()
    security.install(app)

    @app.get("/data")
    def data():
        return {"ok": True}

    @app.post("/change")
    def change():
        return {"changed": True}

    @app.get("/")
    def root():
        return {"root": True}

    @app.get("/chat")
    def chat():
        return {"chat": True}

    @app.get("/static/x")
    def static_x():
        return {"x": True}

    @app.get("/api/version")
    def version():
        return {"version": "1"}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hi")
        await websocket.close()

    return app


@pytest.fixture
def client():
    return TestClient(make_app(), base_url="http://myhost:23100")


def acao(r):
    return r.headers.get("access-control-allow-origin")


# --- CORS -------------------------------------------------------------------------------------

@pytest.mark.parametrize("origin", [
    "http://localhost", "http://localhost:3000", "http://127.0.0.1:8080", "http://[::1]:9",
    "https://localhost:8443", "http://myhost:23100",  # the server's own origin
])
def test_allowed_origins_get_cors_headers(client, origin):
    r = client.get("/data", headers={"Origin": origin})
    assert r.status_code == 200 and acao(r) == origin
    assert "Origin" in r.headers.get("vary", "")


def test_foreign_origin_gets_no_cors_headers_on_get(client):
    r = client.get("/data", headers={"Origin": FOREIGN})
    assert r.status_code == 200 and acao(r) is None


def test_look_alike_hosts_are_foreign(client):
    for origin in ["http://localhost.evil.example", "http://127.0.0.1.evil.example", "http://evil.example:23100"]:
        assert acao(client.get("/data", headers={"Origin": origin})) is None


def test_no_origin_is_always_allowed(client):
    r = client.post("/change")
    assert r.status_code == 200 and acao(r) is None


def test_state_changing_foreign_origin_is_403(client):
    for method in ("post", "put", "patch", "delete"):
        r = getattr(client, method)("/change", headers={"Origin": FOREIGN})
        assert r.status_code == 403, method


def test_state_changing_allowed_origin_passes(client):
    r = client.post("/change", headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 200 and acao(r) == "http://localhost:3000"


def test_preflight_allowed(client):
    r = client.options("/change", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,authorization",
    })
    assert r.status_code in (200, 204)
    assert acao(r) == "http://localhost:3000"
    assert "POST" in r.headers["access-control-allow-methods"]
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


def test_preflight_foreign_has_no_cors_headers(client):
    r = client.options("/change", headers={"Origin": FOREIGN, "Access-Control-Request-Method": "POST"})
    assert r.status_code == 403 and acao(r) is None
    assert "access-control-allow-methods" not in r.headers


def test_origins_env_adds_origins(client, monkeypatch):
    monkeypatch.setenv("GEMSTONE_ORIGINS", "https://app.example, http://other.example:81")
    assert acao(client.get("/data", headers={"Origin": "https://app.example"})) == "https://app.example"
    assert acao(client.get("/data", headers={"Origin": "http://other.example:81"})) == "http://other.example:81"
    assert acao(client.get("/data", headers={"Origin": FOREIGN})) is None


def test_origins_env_star_allows_everything(client, monkeypatch):
    monkeypatch.setenv("GEMSTONE_ORIGINS", "*")
    r = client.post("/change", headers={"Origin": FOREIGN})
    assert r.status_code == 200 and acao(r) in (FOREIGN, "*")


# --- WebSocket Origin -------------------------------------------------------------------------

def test_ws_foreign_origin_is_rejected(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws", headers={"Origin": FOREIGN}):
            pass
    assert e.value.code in (1008, 403)


@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1", "http://myhost:23100"])
def test_ws_allowed_origin_connects(client, origin):
    with client.websocket_connect("ws://myhost:23100/ws", headers={"Origin": origin}) as ws:
        assert ws.receive_text() == "hi"


def test_ws_without_origin_connects(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_text() == "hi"


def test_ws_origins_env(client, monkeypatch):
    monkeypatch.setenv("GEMSTONE_ORIGINS", FOREIGN)
    with client.websocket_connect("/ws", headers={"Origin": FOREIGN}) as ws:
        assert ws.receive_text() == "hi"


# --- API key ----------------------------------------------------------------------------------

def test_without_key_nothing_is_required(client):
    assert client.get("/data").status_code == 200


def test_key_required_on_http(client, monkeypatch):
    monkeypatch.setenv("GEMSTONE_API_KEY", "s3cret")
    r = client.get("/data")
    assert r.status_code == 401 and r.headers["www-authenticate"].lower().startswith("bearer")
    assert client.get("/data", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/data", headers={"Authorization": "s3cret"}).status_code == 401
    assert client.get("/data", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.post("/change", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_key_is_not_accepted_in_http_query(client, monkeypatch):
    monkeypatch.setenv("GEMSTONE_API_KEY", "s3cret")
    assert client.get("/data?api_key=s3cret").status_code == 401


def test_key_exempts_static_and_health(client, monkeypatch):
    monkeypatch.setenv("GEMSTONE_API_KEY", "s3cret")
    for path in ("/", "/chat", "/static/x", "/api/version"):
        assert client.get(path).status_code == 200, path
    assert client.head("/").status_code in (200, 405)  # never 401


def test_exempt_prefix_is_not_a_prefix_match_on_text(client, monkeypatch):
    monkeypatch.setenv("GEMSTONE_API_KEY", "s3cret")
    assert client.get("/staticfoo").status_code == 401
    assert client.get("/chatter").status_code == 401


def test_key_required_on_ws(client, monkeypatch):
    monkeypatch.setenv("GEMSTONE_API_KEY", "s3cret")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass
    with client.websocket_connect("/ws", headers={"Authorization": "Bearer s3cret"}) as ws:
        assert ws.receive_text() == "hi"
    with client.websocket_connect("/ws?api_key=s3cret") as ws:
        assert ws.receive_text() == "hi"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?api_key=nope"):
            pass


def test_preflight_needs_no_key(client, monkeypatch):
    monkeypatch.setenv("GEMSTONE_API_KEY", "s3cret")
    r = client.options("/data", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert r.status_code in (200, 204)


# --- the real app -----------------------------------------------------------------------------

def test_real_app_has_the_policy(monkeypatch):
    c = TestClient(server.app)
    assert acao(c.get("/api/version", headers={"Origin": "http://localhost:3000"})) == "http://localhost:3000"
    assert acao(c.get("/api/version", headers={"Origin": FOREIGN})) is None
    assert c.post("/api/sessions/", headers={"Origin": FOREIGN}).status_code == 403
    monkeypatch.setenv("GEMSTONE_API_KEY", "k")
    assert c.get("/api/models").status_code == 401
    assert c.get("/").status_code == 200
    assert c.get("/api/version").status_code == 200
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/api/chat/streaming"):
            pass
