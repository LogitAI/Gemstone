"""
Safe defaults for the server process (SPEC S1.1; issue #116): bind address, reload flag, the
Origin policy for CORS and the WebSocket, and the optional API key.

Everything reads `os.environ` per request, so the settings can change without a restart.
"""
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit
import hmac
import ipaddress
import logging
import os

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 23100

log = logging.getLogger("gemstone.security")


# --- bind address -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    reload: bool


def parse_host_port(value: str):
    """ `host[:port]` (also `[v6]:port`, a bare IPv6 address, an optional scheme) -> (host, port or None). """
    value = value.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/", 1)[0]
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") and rest[1:] else None
    if value.count(":") == 1:
        host, _, port = value.partition(":")
        return host, int(port) if port else None
    return value, None  # no colon, or a bare IPv6 address


def resolve_server_config(argv, env=None) -> ServerConfig:
    """ `argv` is `sys.argv[1:]`-shaped (`run server [host] [port] [--reload]`). Arguments beat `GEMSTONE_HOST`. """
    env = os.environ if env is None else env
    flags = [a for a in argv if a.startswith("--")]
    positional = [a for a in argv[2:] if not a.startswith("--")]
    host, port = DEFAULT_HOST, DEFAULT_PORT
    if env.get("GEMSTONE_HOST", "").strip():
        h, p = parse_host_port(env["GEMSTONE_HOST"])
        host, port = h or host, p or port
    if positional:
        h, p = parse_host_port(positional[0])
        host, port = h or host, p or port
    if len(positional) > 1:
        port = int(positional[1])
    reload = "--reload" in flags or env.get("GEMSTONE_DEV", "") not in ("", "0")
    return ServerConfig(host, port, reload)


def is_loopback_host(host: str) -> bool:
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_warned = False


def _reset_warning_for_tests():
    global _warned
    _warned = False


def warn_if_open(host: str, env=None) -> bool:
    """ One start-up warning when the bind is not loopback and no API key is set; never blocks. """
    global _warned
    env = os.environ if env is None else env
    if _warned or is_loopback_host(host) or env.get("GEMSTONE_API_KEY"):
        return False
    _warned = True
    log.warning(
        "Gemstone is listening on %s without GEMSTONE_API_KEY: anyone who can reach this address can "
        "use the server. Set GEMSTONE_API_KEY (clients send `Authorization: Bearer <key>`) or bind to "
        "127.0.0.1.", host,
    )
    return True


# --- origins and key --------------------------------------------------------------------------

STATIC_PREFIXES = ("/static", "/webpack", "/composeResources")
STATIC_EXACT = ("/", "/chat")
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS"


def _norm_origin(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def origin_allowed(origin: str, host_header: str = "", env=None) -> bool:
    env = os.environ if env is None else env
    extra = [_norm_origin(o) for o in env.get("GEMSTONE_ORIGINS", "").split(",") if o.strip()]
    if "*" in extra or _norm_origin(origin) in extra:
        return True
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if is_loopback_host(parts.hostname):
        return True
    return bool(host_header) and parts.netloc.lower() == host_header.strip().lower()  # the server's own origin


def _is_static(method: str, path: str) -> bool:
    if path in STATIC_EXACT or any(path == p or path.startswith(p + "/") for p in STATIC_PREFIXES):
        return method in ("GET", "HEAD")
    return False


def _is_health(method: str, path: str) -> bool:
    return (method == "HEAD" and path == "/") or (method in ("GET", "HEAD") and path == "/api/version")


def _bearer(headers) -> str:
    value = headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def key_ok(given: str, env=None) -> bool:
    key = (os.environ if env is None else env).get("GEMSTONE_API_KEY", "")
    return not key or (bool(given) and hmac.compare_digest(given.encode(), key.encode()))


class SecurityMiddleware:
    """ Pure ASGI, so one class guards both HTTP and WebSocket. """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            await self.http(scope, receive, send)
        elif scope["type"] == "websocket":
            await self.websocket(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    @staticmethod
    def _headers(scope):
        return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}

    async def http(self, scope, receive, send):
        headers = self._headers(scope)
        method, path = scope["method"], scope["path"]
        origin = headers.get("origin")
        allowed = origin is not None and origin_allowed(origin, headers.get("host", ""))

        if origin is not None and method == "OPTIONS" and "access-control-request-method" in headers:
            if not allowed:
                return await self._reply(send, 403, b"Origin not allowed.")
            requested = headers.get("access-control-request-headers", "")
            extra = [(b"access-control-allow-headers", (requested or "authorization, content-type").encode()),
                     (b"access-control-allow-methods", ALLOW_METHODS.encode()),
                     (b"access-control-max-age", b"600")]
            return await self._reply(send, 204, b"", self._cors(origin) + extra)
        if origin is not None and not allowed and method in UNSAFE_METHODS:
            return await self._reply(send, 403, b"Origin not allowed.")
        if not (_is_static(method, path) or _is_health(method, path)) and not key_ok(_bearer(headers)):
            return await self._reply(
                send, 401, b"A valid API key is required.", [(b"www-authenticate", b"Bearer")] + (self._cors(origin) if allowed else [])
            )

        if not allowed:
            return await self.app(scope, receive, send)

        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                message = dict(message, headers=list(message.get("headers", [])) + self._cors(origin))
            await send(message)

        await self.app(scope, receive, send_with_cors)

    async def websocket(self, scope, receive, send):
        headers = self._headers(scope)
        origin = headers.get("origin")
        if origin is not None and not origin_allowed(origin, headers.get("host", "")):
            return await self._refuse(receive, send)
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        given = _bearer(headers) or (query.get("api_key") or [""])[0]
        if not key_ok(given):
            return await self._refuse(receive, send)
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(receive, send):
        """ Close before accept: the handshake answers 403 (the close code is 1008 for the app's own closes). """
        await receive()  # websocket.connect
        await send({"type": "websocket.close", "code": 1008, "reason": "Forbidden"})

    @staticmethod
    def _cors(origin):
        return [(b"access-control-allow-origin", origin.encode("latin-1")), (b"vary", b"Origin")]

    @staticmethod
    async def _reply(send, status, body, headers=()):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"content-length", str(len(body)).encode()), *headers]})
        await send({"type": "http.response.body", "body": body})


def install(app):
    app.add_middleware(SecurityMiddleware)
    return app
