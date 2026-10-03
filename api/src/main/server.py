from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
import anyio
import uvicorn

import traceback
import threading
import json
import os

from .settings import STATIC_DIR, WEBPACK_DIR, Session
from . import registry as registry_module
from .leases import acquire
from .registry import DEFAULT_KEEP_ALIVE, ModelBusy, ModelUnavailable, in_catalogue
from .models.config import ChatHistory
from .openai_api import router as openai_router
from .ollama_api import router as ollama_router
from . import security


app = FastAPI()
security.install(app)  # Origin policy and the optional API key (SPEC S1.1)
app.include_router(ollama_router)  # Ollama-compatible API (SPEC S1.15), including POST /api/chat
app.mount("/static", StaticFiles(directory=STATIC_DIR, html=True), name="static")
app.mount("/webpack", StaticFiles(directory=WEBPACK_DIR, html=True), name="webpack")
app.include_router(openai_router)  # /v1/*: OpenAI-compatible API (SPEC S1.10)


def _reason(text: str) -> str:
    """ A close-frame reason: at most 123 bytes of UTF-8 (the frame's limit), cut on a character. """
    return text.encode()[:123].decode(errors="ignore")


@app.get("/")
def root():
    """ Redirect to the chat page """
    #return RedirectResponse(url="/chat")
    return FileResponse(os.path.join(WEBPACK_DIR, "gemstone.html"))


@app.get("/composeResources/{path:path}")
def resource(path: str):
    """ Redirect to the chat page """
    return RedirectResponse(
        url=f"/webpack/composeResources/{path}",
        status_code=301
    )


@app.get("/chat")
def index():
    """ Serve the main HTML page """
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/models")
def models():
    """ List the models the registry knows (SPEC S1.2): {id: {model_name, model_description}} """
    return {
        m["id"]: dict(model_name=m["model_name"], model_description=m["model_description"])
        for m in registry_module.registry.models()
    }


@app.post("/api/models/{model_id:path}/sessions/")  # `path`: a Hugging Face id contains a "/"
@app.post("/api/sessions/")
def create_session(model_id: str = "default"):
    """ Create a new session for the specified model; 404 if the model is unknown (SPEC S1.3) """
    try:
        registry_module.registry.check_known(model_id)  # resolves the name; loads and fetches nothing
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e.args[0]) if e.args else "Model not found.")
    session = Session(model_id=model_id)

    return dict(model_id=model_id, session_id=session.session_id, message="A session is created successfully.")


@app.delete("/api/sessions/{session_id}")
@app.post("/api/sessions/{session_id}")
def delete_session(session_id: str):
    """ Delete a session by its ID; 404 if there is no such session """
    try:
        Session.close(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="The session is not found.")

    return dict(message="Session deleted successfully")


@app.websocket("/api/chat/streaming")
async def chat_with_streaming(websocket: WebSocket):
    """ Chat via Websocket endpoint """
    await websocket.accept()

    try:
        session_id = json.loads(await websocket.receive_text()).get("session_id")
        session = Session(session_id=session_id)
        hf_id = registry_module.registry.resolve(session.model_id)  # derived models too
    except Exception:
        traceback.print_exc()
        await websocket.close(code=1008, reason="Invalid session ID or model not found.")
        return

    chat_history = ChatHistory()
    chat_history.extend(json.loads(await websocket.receive_text()))
    user_prompt = await websocket.receive_text()

    # The registry owns the model (SPEC S1.14): the lease keeps it resident until this generation
    # ends, then the default keep_alive applies. A catalogue model missing from the cache is
    # fetched on first use, as the app always did.
    registry = registry_module.registry
    try:
        lease = await acquire(registry, hf_id, DEFAULT_KEEP_ALIVE, in_catalogue(session.model_id))
    except ModelBusy as e:  # every resident model stayed busy (SPEC S1.14): 1013 "try again later"
        await websocket.close(code=1013, reason=str(e)[:120])
        return
    except ModelUnavailable as e:  # offline or a failed download (#114): 1011, not the "busy" 1013
        await websocket.close(code=1011, reason=_reason(e.reason))
        return
    except Exception:
        traceback.print_exc()
        await websocket.close(code=1008, reason="Model not found or failed to load.")
        return

    # Generation runs on worker threads so the event loop stays free; a client that goes away
    # sets `cancel`, which stops generation and frees the model for the next request. Everything
    # after the lease is granted runs inside the `try`, so the lease is released however it ends.
    cancel = threading.Event()
    tokens = None
    done = object()
    try:
        tokens = lease.model.chat(  # a generator: nothing runs until the first `next`
            chat_history, user_prompt, print_output=True, cancel=cancel, tool_call_caches=session.tool_call_caches
        )
        while (token := await run_in_threadpool(next, tokens, done)) is not done:
            await websocket.send_text(token)
        await websocket.send_text("<EOS>")  # EOS token to signal the end of the conversation
        await websocket.close()
    except WebSocketDisconnect:
        pass  # the client went away mid-stream
    finally:
        cancel.set()
        try:
            if tokens is not None:
                with anyio.CancelScope(shield=True):
                    await run_in_threadpool(tokens.close)
        finally:
            registry.release(lease)


if __name__ == '__main__':
    import sys
    config = security.resolve_server_config(["run", "server", *sys.argv[1:]])
    security.warn_if_open(config.host)
    uvicorn.run(
        "server:app", host=config.host, port=config.port, reload=config.reload,
        ws_ping_interval=300, ws_ping_timeout=300, ws_per_message_deflate=False
    )
