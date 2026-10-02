from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
import uvicorn

import traceback
import threading
import json
import os

from .settings import STATIC_DIR, WEBPACK_DIR, MODEL_LIST, Session
from .models.config import ChatHistory
from .openai_api import router as openai_router
from .ollama_api import router as ollama_router


app = FastAPI()
app.include_router(ollama_router)  # Ollama-compatible API (SPEC S1.15), including POST /api/chat
app.mount("/static", StaticFiles(directory=STATIC_DIR, html=True), name="static")
app.mount("/webpack", StaticFiles(directory=WEBPACK_DIR, html=True), name="webpack")
app.include_router(openai_router)  # /v1/*: OpenAI-compatible API (SPEC S1.10)


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
    """ List available models """
    return MODEL_LIST


@app.post("/api/models/{model_id}/sessions/")
@app.post("/api/sessions/")
def create_session(model_id: str = "default"):
    """ Create a new session for the specified model """
    try:
        session = Session(model_id=model_id)
    except ValueError as e:
        return HTTPException(status_code=404, detail=e)

    return dict(model_id=model_id, session_id=session.session_id, message="A session is created successfully.")


@app.delete("/api/sessions/{session_id}")
@app.post("/api/sessions/{session_id}")
def delete_session(session_id: str):
    """ Delete a session by its ID """
    try:
        Session.close(session_id)
    except KeyError:
        return HTTPException(status_code=404, detail="The session is not found.")

    return dict(message="Session deleted successfully")


@app.websocket("/api/chat/streaming")
async def chat_with_streaming(websocket: WebSocket):
    """ Chat via Websocket endpoint """
    await websocket.accept()

    try:
        session_id = json.loads(await websocket.receive_text()).get("session_id")
        session = Session(session_id=session_id)
        model = session.model
    except Exception:
        traceback.print_exc()
        await websocket.close(code=1008, reason="Invalid session ID or model not found.")
        return

    chat_history = ChatHistory()
    chat_history.extend(json.loads(await websocket.receive_text()))
    user_prompt = await websocket.receive_text()

    # Generation runs on worker threads so the event loop stays free; a client that goes away
    # sets `cancel`, which stops generation and frees the model for the next request.
    cancel = threading.Event()
    tokens = model.chat(
        chat_history, user_prompt, print_output=True, cancel=cancel, tool_call_caches=session.tool_call_caches
    )
    done = object()
    try:
        while (token := await run_in_threadpool(next, tokens, done)) is not done:
            await websocket.send_text(token)
        await websocket.send_text("<EOS>")  # EOS token to signal the end of the conversation
        await websocket.close()
    except WebSocketDisconnect:
        pass  # the client went away mid-stream
    finally:
        cancel.set()
        await run_in_threadpool(tokens.close)
        del model


if __name__ == '__main__':
    uvicorn.run(
        "server:app", host="127.0.0.1", port=23100, reload=True,
        ws_ping_interval=300, ws_ping_timeout=300, ws_per_message_deflate=False
    )
