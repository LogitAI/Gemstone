# Running the engine inside the app

How the client runs Gemstone's serving engine on the device instead of talking to a server (SPEC S3.1,
issue #42; offline operation S3.2, issue #190). Status: `partial`. The Python side and the client's
transport seam are implemented and tested; the bridge that runs the Python side inside the app through
Python Multiplatform is not, because it waits for Python Multiplatform's published artifacts (below).

## The two halves

```
 chat screen ── ChatTransports.active ──┬── ChatWebSocketClient ── HTTP / WebSocket ── server (S1.4)
                                        │
                                        └── InAppChatTransport ── InAppEngine ── api/src/main/inapp.py
                                                                  (Kotlin)        (Python, same engine)
```

- **Kotlin** (`app/src/commonMain/kotlin/gemstone/framework/network/ChatTransport.kt`). The chat
  screen, the model selection and the settings dialog use `ChatTransports.active`. An
  `InAppChatTransport` wraps an `InAppEngine`, which has two calls: `models()` and
  `chat(modelId, history, message)`, a `Flow<String>` of protocol frames. `ChatStreamDecoder` turns
  the frames into the same chat events the server path produces, so the screens do not know which
  transport they are on.
- **Python** (`api/src/main/inapp.py`). The functions an `InAppEngine` calls through Python
  Multiplatform. They use the server's registry, model classes and leases, and never touch the network.

## The Python entry point

```python
from api.src.main import inapp

inapp.configure(
    models_dir="/path/the/app/owns",   # laid out like HF_HOME: hub/ and gemstone/aliases.json
    max_memory="3GB",                  # residency budget (S1.14); 0 or None = no budget
    max_loaded=1,                      # models resident at once
    threads=4,                         # torch's intra-op threads; None keeps torch's default
    device="cpu",                      # cpu, cuda, cuda:<n>, mps
    quantization=None,                 # a weight format such as q8_0 (torchnative, S1.16)
)

for m in inapp.models():               # only models whose weights are in models_dir
    print(m["id"], m["hf_id"])

for frame in inapp.chat("HuggingFaceTB/SmolLM2-135M-Instruct", history=[], message="Hello"):
    ...                                # text, <think>, </think>, <tool_call>...</tool_call>, then <EOS>
```

- `configure` is optional. Until it is called, `models()` and `chat()` use the server's registry and
  environment (`HF_HOME`, `GEMSTONE_DEVICE`, `GEMSTONE_QUANTIZATION`). The app is expected to call it
  once at start with settings it owns. The parameter names and defaults are an agent proposal, not
  confirmed by the owner (SPEC S3.1).
- A malformed value raises `ValueError` naming the setting, and the earlier configuration stays.
  Calling `configure` again replaces the registry and unloads the models the earlier one had loaded.
- `chat` loads with `local_files_only=True`: a model that is not in `models_dir` raises `LookupError`
  and nothing is downloaded. Getting weights onto the device is the app's job (not designed yet).
- The lease on the model is released however the stream ends, including when the app stops reading
  and closes the generator; the model then stays resident for its keep-alive (5 minutes).
- `history` is `[{"role": ..., "content": ...}]`, the same as the WebSocket protocol.

## Tests

| What | Test | Where it runs |
|---|---|---|
| Listing, streaming, lease release, refusing a model that is not on the device (fake engine) | `api/tests/test_inapp.py` | `test.yml` pytest, `test-torchnative.yml` |
| `configure`: the app's directory only, the options a load gets, limits, threads, malformed values, reconfiguring (fake engine) | `api/tests/test_inapp_config.py` | same |
| The real engine through `configure` and `chat` on the test model, downloads off | `test_the_real_engine_chats_from_the_configured_directory` in `api/tests/test_inapp_config.py` (marker `real_model`) | same, with `-m "real_model or not real_model"` |
| The chat screen on the in-app transport opens no network request (fake `InAppEngine`) | `app/src/desktopTest/kotlin/gemstone/framework/network/InAppChatTest.kt` | `test.yml` gradle |

Run them locally with uv:

```bash
uv run --extra torch pytest api/tests/test_inapp.py api/tests/test_inapp_config.py
uv run --extra torch pytest -m real_model api/tests/test_inapp_config.py   # needs the test model in HF_HOME
```

## Not yet

- The `InAppEngine` that calls `inapp.py` through Python Multiplatform, the Python packages bundled in
  the app, and the model directory on each platform. The means is the owner's (2026-11-22, "배포된
  아티팩트로 (추천)"): Python Multiplatform and toolchain's Gradle plugin as published artifacts, desktop
  first. As of 2026-11-23 Maven Central has no Python Multiplatform artifact under `org.thisisthepy`
  (only `compose/` and `kotlin/`), so this part waits.
- On Android and iOS, the free-threaded 3.15t packages without `cp315t` wheels (#178).
