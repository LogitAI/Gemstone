import sys


def run_uvicorn(target, **kwargs):
    import uvicorn
    uvicorn.run(target, **kwargs)


def main(argv):
    if argv[:2] != ["run", "server"]:
        sys.exit("usage: python -m api run server [host] [port] [--reload]\n"
                 "env: GEMSTONE_HOST=host[:port], GEMSTONE_API_KEY, GEMSTONE_ORIGINS, GEMSTONE_DEV=1")
    try:
        from api.src.main import security, settings
        server_object = "api.src.main.server:app"
    except ImportError:
        from src.main import security, settings
        server_object = "src.main.server:app"
    settings.configure_logging()
    config = security.resolve_server_config(argv)
    security.warn_if_open(config.host)
    run_uvicorn(
        server_object, host=config.host, port=config.port, reload=config.reload,
        ws_ping_interval=300, ws_ping_timeout=300, ws_per_message_deflate=False
    )


if __name__ == "__main__":
    main(sys.argv[1:])
