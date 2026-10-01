# Gemstone — 프로젝트 주요 사항

저장소: `github.com/LogitAI/Gemstone` · 라이선스: MIT · 기준 커밋: `2be0e37` (2026-10-02)

의도는 [`docs/INTENT.md`](docs/INTENT.md), 동작 계약은 [`docs/SPEC.md`](docs/SPEC.md), 에이전트 규정은
[`AGENTS.md`](AGENTS.md) 에 있습니다. 이 문서는 그 셋을 한국어로 빠르게 훑기 위한 요약이며, 어긋나면
INTENT → SPEC 순으로 그쪽이 이깁니다.

---

## 1. 한 줄 요약

모든 플랫폼에서 같은 클라이언트로 쓰는 오픈소스 AI 채팅 시스템. 지금은 Python 서버가 모델을 돌리고,
목표는 Python Multiplatform 으로 모델을 **앱 안에서** 돌리는 것이다.

## 2. 현재 상태 (코드를 읽고 판정 — 테스트로 검증된 항목은 없음)

| 영역 | 상태 | 근거 |
|---|---|---|
| WebSocket 스트리밍 채팅 (`/api/chat/streaming`) | 구현 | `api/src/main/server.py` |
| 도구 호출 (날씨·공휴일·환율·계산·웹검색) | 구현 | `api/src/main/utils/` |
| GGUF / BIN 백엔드, Qwen3 14B · Llama 3.1 8B | 구현 | `api/src/main/backend/`, `models/` |
| GPTQ 백엔드 | 예정 | `gptq.py` 는 import 불가한 스크래치 |
| 비스트리밍 `POST /api/chat`, `GET /api/hello` | 부분 (결함) | `BaseModel.chat` 이 항상 제너레이터 |
| 세션 API | 부분 (결함) | 오류를 `raise` 대신 `return`, 404 대신 500 |
| Android · 데스크톱 · 웹 클라이언트 | 구현 | `app/build.gradle.kts` |
| iOS | 부분 | Xcode 스크립트가 없는 `:composeApp` 모듈을 호출 |
| 모델 선택 | 부분 | 클라이언트에 하드코딩, 서버 목록 미사용 |
| 대화 기록 | 부분 | 메모리에만 |
| 설정 화면, 도메인 계층(Clean Architecture) | 예정 | 빈 파일 |
| 온디바이스 추론 · 오프라인 · 동기화 · OpenAI 호환 API | 예정 | 코드 없음 |
| 네이티브 데스크톱 (GraalVM) | 진행 중 | 메인 체크아웃의 미커밋 작업 |
| 테스트 | **없음** | Kotlin 테스트 1개가 `1 + 2 == 3` 만 확인 |

## 3. 구조

```
app/                Kotlin Compose Multiplatform 클라이언트 (모듈 :app)
  src/commonMain    UI · 뷰모델 · 네트워크 프로토콜 (모든 타깃 공유)
  src/cioMain       Android · iOS · 데스크톱 공유 Ktor CIO 엔진
  src/*Main         플랫폼 진입점 (android, ios, desktop, wasmJs)
api/                Python 3.12 모델 서빙 서버
  src/main/server.py      FastAPI 엔드포인트
  src/main/settings.py    모델 목록, 세션 관리
  src/main/models/        모델 정의 (qwen3, llama3)
  src/main/backend/       추론 런타임 (gguf, bin)
  src/main/utils/         도구 구현과 도구 호출 루프
  src/test/               정적 웹 자산 (빌드된 Wasm 클라이언트, Brython 테스트 페이지) — 테스트 코드 아님
docs/               INTENT, SPEC, locale/, guide/ (GitHub Pages)
```

## 4. 빌드와 실행

```bash
uv sync                          # Python 의존성 (Python >=3.12,<3.13)
python -m api run server         # 0.0.0.0:23100
./gradlew :app:run               # 데스크톱
./gradlew :app:installDebug      # Android
./gradlew :app:wasmJsBrowserDevelopmentRun   # 웹 개발 서버
./gradlew :app:desktopTest       # 공통 테스트 (JVM)
python3 docs/guide/check_guide.py            # 가이드 사이트 검사
```

클라이언트 서버 주소: `GEMSTONE_SERVER_HOST`, `GEMSTONE_SERVER_PORT`(값에 `:` 포함), 데스크톱은
`--server host:port`. 웹 클라이언트는 자신을 서빙한 호스트를 쓰므로 API 서버가 서빙해야 한다.

## 5. 결정 사항

- **개발 방식**: 의도 기반 스펙 주도 개발 + 테스트 주도 개발. INTENT → SPEC → 테스트(적색 확인) → 코드.
- **문서 배치**: `main` 에는 루트의 `README.md` 와 `docs/` 하위 디렉터리만 남는다.
  README 는 develop 전용 파일(AGENTS, PROJECT, INTENT, SPEC)에 링크하지 않는다.
- **`CLAUDE.md`** 는 `@AGENTS.md` 한 줄만 둔다.
- **토치 버전**: `pyproject.toml` 에서 torch 계열에 버전을 고정하지 않는다 (Windows 는 cu128 인덱스).

## 6. 열린 질문

1. **외부 웹 서비스를 쓰는 도구**(SerpApi, Bing, Open-Meteo 등)가 "프라이버시 우선 · 오프라인"
   의도와 맞는가? 범위 안인지, 선택 기능인지, 서버 전용인지 결정이 필요하다.
2. **원격 모델 제공자**(OpenAI, Anthropic, HF Inference) — 옛 README 트리에만 있고 코드·의도에 없다.
3. **동기화(sync)** 가 무엇을 무엇으로 동기화하는지.
4. **온디바이스 엔진** — Python 을 통한 llama.cpp 인가, torchnative 인가.
5. **Python 테스트의 위치** — `api/src/test/` 는 운영 중인 정적 자산이 차지하고 있다.
   테스트 디렉터리를 새로 정할지, 자산을 옮길지.
6. **릴리스 흐름** — 공통 규정은 `release` 브랜치와 `tools/release/sync-release.sh` 를 전제하지만
   이 저장소에는 `release/cnu` 브랜치만 있고 스크립트가 없다.
7. **줄바꿈** — `.gitattributes` 가 없다. 편집기가 CRLF 로 바꾼 파일이 많으니 정책이 필요하다.
8. **GitHub Pages 배포** — "브랜치에서 배포" 는 `/` 또는 `/docs` 만 고를 수 있어 `docs/guide/` 를
   바로 쓸 수 없다. Actions 워크플로를 둘지 결정이 필요하다.
