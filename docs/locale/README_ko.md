[English](../../README.md) | 한국어

<div align="center">

<img src="../../app/src/desktopMain/resources/simple_white.png" alt="Gemstone 로고" width="120" height="120" />

# Gemstone

**모든 플랫폼에 하나의 AI 채팅 클라이언트 — 모델을 내 기기에서 돌리기 위해 만들었습니다.**

[![License: Apache-2.0](https://img.shields.io/github/license/LogitAI/Gemstone?color=c2185b)](../../LICENSE.md)
[![Kotlin](https://img.shields.io/badge/Kotlin-2.3-7F52FF?logo=kotlin&logoColor=white)](../../gradle/libs.versions.toml)
[![Compose Multiplatform](https://img.shields.io/badge/Compose_Multiplatform-1.9-4285F4?logo=jetpackcompose&logoColor=white)](../../gradle/libs.versions.toml)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](../../pyproject.toml)
[![Platforms](https://img.shields.io/badge/platforms-Android%20%7C%20iOS%20%7C%20Desktop%20%7C%20Web-5b6270)](#-현황)

[가이드](https://logitai.github.io/Gemstone/?lang=ko) ·
[빠른 시작](#-빠른-시작) ·
[아키텍처](#-아키텍처) ·
[현황](#-현황)

</div>

---

## 💎 왜 Gemstone 인가?

대부분의 AI 채팅 앱은 남의 서버를 들여다보는 얇은 창입니다. Gemstone 은 반대편에서 출발합니다:
**공개 가중치 모델을, 내가 있는 곳에서, 모든 기기에 들고 다닐 수 있는 하나의 인터페이스로.**

지금은 Android, iOS, 데스크톱, 웹을 위한 Kotlin
[Compose Multiplatform](https://www.jetbrains.com/compose-multiplatform/) 클라이언트가, 내 머신에서
직접 띄운 Python 서버(엔진은 아직 CPU 에서 돕니다. GPU 지원은 예정)와 대화하는 형태입니다. 다음 단계에서는 같은 Python 모델 코드가
[Python Multiplatform](https://github.com/thisisthepy/python-multiplatform) 을 통해 앱 *안으로*
들어가, 별도 서버도 네트워크도 없이 채팅할 수 있게 됩니다.

## ✨ 기능

- 🧩 **하나의 코드베이스, 네 개의 타깃** — Android, iOS, 데스크톱(Windows, macOS, Linux), 웹(Kotlin/Wasm) 이 UI, 뷰모델, 프로토콜을 공유합니다.
- 🚀 **스트리밍 채팅** — 토큰이 생성되는 즉시 WebSocket 으로 도착하고, 그 자리에서 Markdown 으로 렌더링됩니다.
- 🧠 **보이는 추론 과정** — 모델의 `<think>` 블록이 경과 시간과 함께 접을 수 있는 패널로 표시됩니다.
- 🔌 **도구 호출** — 날씨, 공휴일, 환율, 계산기, 웹 검색을 서버에서 병렬로 실행하고 결과를 모델에 돌려줍니다.
- 📦 **엔진 하나로 도는 공개 가중치 모델** — Qwen 3 0.6B 를 transformers 기반 단일 엔진이 서빙합니다. 이 엔진은 PyTorch 위에서도, [torchnative](https://github.com/thisisthepy/torchnative) 위에서도 돕니다. Gemstone 은 로컬 Ollama 대체제가 되어 가는 중입니다. 동시 요청 처리(페이지드 KV 캐시를 쓰는 연속 배칭)와 OpenAI·Ollama 호환 API 는 이미 동작하고, 4비트 가중치와 GGUF 는 예정입니다.
- 🖥️ **네이티브 데스크톱 경험** — JetBrains Jewel 데코레이티드 윈도우와 Dmg / Msi / Deb 설치 파일.

## 🚀 빠른 시작

Git, Python 3.13, [uv](https://docs.astral.sh/uv/), JDK 21 이 필요합니다. 기본 모델(Qwen 3 0.6B)은
CPU 에서 돕니다.

**1. 저장소를 받고 서버 의존성 설치**

```bash
git clone https://github.com/LogitAI/Gemstone.git
cd Gemstone
uv sync --extra torch          # 또는 --extra torchnative (둘은 함께 설치할 수 없습니다)
```

**2. 모델 서버 실행** (포트 `23100`, 모델은 처음 쓸 때 내려받습니다)

```bash
uv run --extra torch python -m api run server
```

<http://127.0.0.1:23100/> 를 열면 서버에 함께 들어 있는 웹 클라이언트가 뜹니다.

서버는 `127.0.0.1` 에서만 듣습니다. 다른 기기에 열려면 `OLLAMA_HOST` 처럼 `GEMSTONE_HOST` 와 API 키를 설정하세요:
`GEMSTONE_HOST=0.0.0.0:23100 GEMSTONE_API_KEY=<key> uv run --extra torch python -m api run server`.
클라이언트는 `Authorization: Bearer <key>` 를 보냅니다. `GEMSTONE_ORIGINS` 는 브라우저 origin 을 더 허용하고,
`--reload` (또는 `GEMSTONE_DEV=1`) 는 개발용 자동 재시작을 켭니다.

**기존 클라이언트를 Gemstone 에 연결하기** (포트는 Ollama 의 `11434` 가 아니라 `23100` 입니다):

```bash
OLLAMA_HOST=http://127.0.0.1:23100 ollama run qwen3          # ollama CLI 와 Ollama 클라이언트
OPENAI_BASE_URL=http://127.0.0.1:23100/v1 OPENAI_API_KEY=x python my_script.py   # OpenAI SDK
```

`qwen3`, `qwen3:latest`, `qwen3:0.6b` 는 기본 제공 모델이며 처음 쓸 때 내려받습니다. Hugging Face 모델은 id 로
받습니다(`ollama pull Org/Model`). 그 밖의 Ollama 라이브러리 이름(`llama3.2`)은 쓸 수 없습니다. 서버에
`GEMSTONE_API_KEY` 를 설정했다면 그 값을 키로 보내세요. OpenAI SDK 는 `OPENAI_API_KEY=<key>`, 다른 클라이언트는
`Authorization: Bearer <key>` 헤더입니다(헤더를 보낼 수 없는 Ollama 클라이언트는 키 없는 loopback 서버에서 쓰세요).
`think: false`(Ollama), `reasoning_effort: "none"`(OpenAI)은 Qwen3 의 추론을 끕니다.

**3. 네이티브 클라이언트 실행**

```bash
./gradlew :app:run           # 데스크톱
./gradlew :app:installDebug  # Android
```

서버는 `127.0.0.1` 에서만 받습니다. Android 기기나 에뮬레이터에서는 서버를 같은 컴퓨터에서 실행하고
`adb reverse tcp:23100 tcp:23100` 을 쓰세요. 같은 LAN 의 다른 기기에 서비스하려면 서버를
`GEMSTONE_HOST=0.0.0.0` 으로 시작하고 양쪽에 `GEMSTONE_API_KEY=<비밀값>` 을 설정합니다. 데스크톱
클라이언트는 `--server http://<호스트>:23100` (또는 `GEMSTONE_SERVER_HOST`)을 받습니다.
자세한 내용은 `docs/guide/` 의 클라이언트 가이드를 보세요.

**내 코드에서 서버와 대화하기** — 프레임 세 개를 보내면 토큰이 흘러나옵니다:

```python
import asyncio, json, urllib.request
import websockets  # Gemstone 의존성에 이미 포함

async def ask(prompt: str, host: str = "127.0.0.1:23100") -> None:
    req = urllib.request.Request(f"http://{host}/api/models/qwen3/sessions/", method="POST")
    session_id = json.load(urllib.request.urlopen(req))["session_id"]

    async with websockets.connect(f"ws://{host}/api/chat/streaming") as ws:
        await ws.send(json.dumps({"session_id": session_id}))  # 1. 세션
        await ws.send(json.dumps([]))                          # 2. 대화 기록
        await ws.send(prompt)                                  # 3. 프롬프트
        async for token in ws:
            if token == "<EOS>":
                break
            print(token, end="", flush=True)

asyncio.run(ask("오늘 대전 날씨가 어때?"))
```

브라우저 클라이언트 [`api/src/main/static/index.py`](../../api/src/main/static/index.py) 와 같은
프로토콜입니다.

## 🧩 아키텍처

```mermaid
flowchart LR
    subgraph Client["app/ — Compose Multiplatform"]
        UI["채팅 UI<br/>commonMain"] --> VM["ChatViewModel"] --> WS["ChatWebSocketClient<br/>(Ktor)"]
    end
    subgraph Server["api/ — Python 3.13"]
        EP["FastAPI<br/>/api/chat/streaming"] --> M["모델<br/>Qwen 3"]
        M --> B["엔진<br/>PyTorch / torchnative 위의 transformers"]
        M <--> T["도구<br/>날씨 · 검색 · …"]
    end
    WS -- "WebSocket :23100" --> EP
    Client -. "예정: Python Multiplatform,<br/>앱 안에서 모델 실행" .-> M
```

| 디렉터리 | 들어 있는 것 |
|---|---|
| `app/src/commonMain` | UI, 뷰모델, 네트워크 프로토콜 — 모든 타깃이 공유 |
| `app/src/{android,ios,desktop,wasmJs}Main` | 플랫폼별 진입점 하나씩 |
| `app/src/cioMain` | Android, iOS, 데스크톱이 공유하는 Ktor CIO 엔진 |
| `api/src/main/models` | 모델 정의: 프롬프트, 샘플링 기본값 |
| `api/src/main/engine.py` | 서빙 엔진: 모든 모델을 맡는 transformers 기반 엔진 하나. PyTorch 또는 torchnative 위에서 돈다 |
| `api/src/main/utils` | 도구 구현과 도구 호출 루프 |

## 📍 현황

Gemstone 은 초기 단계의, 동작하는 프로토타입입니다. 각 부분의 솔직한 상태:

| 영역 | 상태 |
|---|---|
| 스트리밍 채팅, 추론 표시, 도구 호출 | ✅ 동작 |
| transformers 단일 엔진, 연속 배칭, 페이지드 KV 캐시 (기본은 CPU) | ✅ PyTorch 에서 동작, 🟡 torchnative 위 검증은 아직 |
| 여러 모델 상주, 축출, `keep_alive` | ✅ 동작 |
| OpenAI 호환 API (`/v1`, 도구 호출은 클라이언트로 전달) | 🟡 부분 — `n` > 1, logprobs, embeddings 없음 |
| Ollama 호환 API (chat, generate, tags, show, pull, delete, ps, copy, create, embed) | 🟡 부분 — `push` 는 501, Modelfile · template · blobs 없음 |
| 보안 기본값 (루프백 바인드, 오리진 검사, API 키) | ✅ 동작 |
| Android, 데스크톱, 웹 클라이언트 | ✅ 동작 |
| 클라이언트의 모델 선택과 서버 주소 | ✅ 모델은 서버에서 읽음. Android 는 아직 주소 설정이 없음 |
| iOS 클라이언트 | 🟡 프레임워크 타깃은 구성됨, Xcode 빌드 스크립트 수정 필요 |
| 대화 기록 | 🟡 메모리에만 보관 |
| 자동화된 테스트 | 🟡 Python 테스트(실제 모델 포함)와 네트워크 계층의 Kotlin 테스트가 CI 에서 돎. UI 는 테스트 없음 |
| 네이티브 데스크톱 실행 파일 (GraalVM, JVM 없음) | ⏳ 예정 — Windows x64 빌드 경로는 있으나 아직 실행해 보지 않음 |
| GPU(CUDA) 추론 | ⏳ 예정 |
| 4비트 가중치, GGUF | ⏳ 예정 |
| 설정 화면 | ⏳ 예정 |
| Python Multiplatform 을 통한 온디바이스 추론 | ⏳ 예정 |
| 오프라인 모드, 기기 간 동기화 | ⏳ 예정 |

## 📖 문서

- **[Gemstone 가이드](https://logitai.github.io/Gemstone/?lang=ko)** — 시작하기, 개념, 작업별 가이드, FAQ (한국어 · English). 소스는 [`docs/guide/`](../guide/).
- **[English README](../../README.md)**

## 🌐 생태계

Gemstone 은 아직 아래 프로젝트에 의존하지 않습니다. 이것은 [thisisthepy](https://github.com/thisisthepy)
의 "모든 플랫폼 위의 Python" 스택이 실어 나르려는 애플리케이션입니다:

| 프로젝트 | 역할 |
|---|---|
| [python-multiplatform](https://github.com/thisisthepy/python-multiplatform) | Kotlin Multiplatform 에 임베딩한 CPython — 온디바이스 추론으로 가는 길 |
| [pythonx-compose](https://github.com/thisisthepy/pythonx-compose) | Compose Multiplatform 의 Python 래퍼 |
| [toolchain](https://github.com/thisisthepy/toolchain) | Python Multiplatform 용 Gradle 빌드 플러그인과 도구 |
| [torchnative](https://github.com/thisisthepy/torchnative) | 기기 위에서 그대로 도는 실제 PyTorch 생태계 |

## 🤝 기여하기

이슈와 풀 리퀘스트는 [LogitAI/Gemstone](https://github.com/LogitAI/Gemstone/issues) 에서 환영합니다.
Gemstone 은 의도 우선, 테스트 우선으로 개발합니다 — 풀 리퀘스트를 열기 전에
[가이드의 기여 안내](https://logitai.github.io/Gemstone/faq.html?lang=ko#contributing) 를 읽어 주세요.

## 🙏 감사의 말

Kotlin, Compose Multiplatform, Jewel 을 만든 [JetBrains](https://www.jetbrains.com/) ·
[PyTorch](https://pytorch.org/) ·
[Hugging Face](https://huggingface.co/) Transformers ·
[Qwen](https://github.com/QwenLM) 모델 팀 ·
무료 공개 API 를 제공하는 [Open-Meteo](https://open-meteo.com/) 와 [Nager.Date](https://date.nager.at/).

## 📄 라이선스

[Apache-2.0](../../LICENSE.md) © 2025 thisisthepy
