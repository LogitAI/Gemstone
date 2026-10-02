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
| 단일 서빙 엔진 (transformers, PyTorch 위) | 구현, 테스트 있음 | `api/src/main/engine.py`, `api/tests/`. torchnative 위 검증은 TN-M1(10-24) 이후 |
| GGUF · BIN · GPTQ 백엔드, Llama 3.1 모델 | **제거됨** (#84) | Qwen3-0.6B 로 교체 |
| torchnative 단일 서빙 시스템(Ollama 대체) | 예정 | 연속 배칭·페이지드 어텐션 포함. 엔진 세부는 제안 단계(transformers 5.x 연속 배칭 + 페이지드 KV 캐시, 커널은 torchnative), 확정 대기. [`docs/serving/engine.md`](docs/serving/engine.md) |
| 모델 관리 · Ollama 호환 API · 4비트 가중치 | 예정 | Ollama 대체에서 따라 나오는 항목. 범위 확정 대기 (SPEC S1.14–S1.16) |
| 비스트리밍 `POST /api/chat`, `GET /api/hello` | 부분 (결함) | `BaseModel.chat` 이 항상 제너레이터 |
| 세션 API | 부분 (결함) | 오류를 `raise` 대신 `return`, 404 대신 500 |
| Android · 데스크톱 · 웹 클라이언트 | 구현 | `app/build.gradle.kts` |
| iOS | 부분 | Xcode 스크립트가 없는 `:composeApp` 모듈을 호출 |
| 모델 선택 | 부분 | 클라이언트에 하드코딩, 서버 목록 미사용 |
| 대화 기록 | 부분 | 메모리에만 |
| 설정 화면, 도메인 계층(Clean Architecture) | 예정 | 빈 파일 |
| 온디바이스 추론 · 오프라인 · 동기화 · OpenAI 호환 API | 예정 | 코드 없음 |
| 네이티브 데스크톱 (GraalVM) | 진행 중 | 메인 체크아웃의 미커밋 작업. 장기적으로 compose-multiplatform-extended 가 관리 |
| 테스트 | **없음** | Kotlin 테스트 1개가 `1 + 2 == 3` 만 확인 |

## 3. 구조

```
app/                Kotlin Compose Multiplatform 클라이언트 (모듈 :app)
  src/commonMain    UI · 뷰모델 · 네트워크 프로토콜 (모든 타깃 공유)
  src/cioMain       Android · iOS · 데스크톱 공유 Ktor CIO 엔진
  src/*Main         플랫폼 진입점 (android, ios, desktop, wasmJs)
api/                Python 3.13 모델 서빙 서버
  src/main/server.py      FastAPI 엔드포인트
  src/main/settings.py    모델 목록, 세션 관리
  src/main/models/        모델 정의 (qwen3, llama3)
  src/main/engine.py      서빙 엔진 (transformers, PyTorch 또는 torchnative 위)
  tests/                  pytest (실제 모델 SmolLM2-135M 사용)
  src/main/utils/         도구 구현과 도구 호출 루프
  src/test/               정적 웹 자산 (빌드된 Wasm 클라이언트, Brython 테스트 페이지) — 테스트 코드 아님
docs/               INTENT, SPEC, locale/, guide/ (GitHub Pages), serving/ (서빙 엔진 결정 기록), build/
```

## 4. 빌드와 실행

```bash
uv sync --extra torch            # Python 의존성 (Python >=3.13). torchnative 는 --extra torchnative
uv run --extra torch pytest      # Python 테스트 (api/tests)
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
- **서빙 구조 (2026-10-02)**: 근거와 위험은 [`docs/serving/engine.md`](docs/serving/engine.md) 에 있다.
  - 의미 없는 다중 백엔드를 걷어내고 **단일 서빙 시스템**으로 간다. GGUF·BIN·GPTQ 런타임과
    `llama-cpp-python`·`bitsandbytes` 의존성을 제거한다.
  - 서빙 시스템은 **torchnative 에 의존**한다. 그래서 Python 요구 버전이 `>=3.13` 으로 오른다.
  - Gemstone 은 **Ollama 대체제**가 된다.
  - **연속 배칭(continuous batching)과 페이지드 어텐션(paged attention)을 도입**한다.
  - vLLM 은 의존성으로 쓰지 않는다. 별도의 사용자 결정이 아니라 위 결정(torchnative 기반 단일 서빙)에서
    따라 나오는 것이다. 방향이 바뀌면 함께 다시 본다. torchnative 위에서 돌 수 없고(libtorch ABI·`torch.compile`
    전제), 서버와 기기에 스택이 둘이 되기 때문이다.
- **네이티브 데스크톱 (2026-10-02)**: GraalVM native-image 는 `compose-multiplatform-extended`
  (Compose Gradle 플러그인 포크)가 종합 관리한다. 플러그인에 아직 코드가 없어서, 그때까지는 Gemstone 의
  현재 경로를 유지한다. JNA 의존성은 GraalVM 과 무관하므로 Gemstone 에 남긴다. 입력 디버그 프로브
  (`GEMSTONE_INPUT_PROBE`)는 커밋하지 않는다.

## 6. 열린 질문

1. **외부 웹 서비스를 쓰는 도구**(SerpApi, Bing, Open-Meteo 등)가 "프라이버시 우선 · 오프라인"
   의도와 맞는가? 범위 안인지, 선택 기능인지, 서버 전용인지 결정이 필요하다.
2. **원격 모델 제공자**(OpenAI, Anthropic, HF Inference) — 옛 README 트리에만 있고 코드·의도에 없다.
3. **동기화(sync)** 가 무엇을 무엇으로 동기화하는지.
4. **서빙 엔진 세부** — 방향은 torchnative 단일 시스템으로 확정(다중 백엔드·vLLM 사용 안 함). 엔진 구성은 미확정: 현재 제안은 transformers 5.x 연속 배칭 + 페이지드 KV 캐시를 엔진으로, 커널은 torchnative.
   확정 전에 할 일: 작은 모델로 torchnative 위에서 `generate_batch`(`sdpa_paged`/`eager_paged`)를
   돌려 정확도와 처리량을 잰다 ([`docs/serving/engine.md`](docs/serving/engine.md) § 7).
5. **Python 테스트의 위치** — 해결됨(2026-10-03): `api/tests/` 에 pytest 로 둔다. 정적 자산을
   `api/src/test/` 밖으로 옮기는 일은 #83(M4).
6. **릴리스 흐름** — 스크립트(`tools/release/sync-release.sh`)와 워크플로(`release-sync.yml`)는 들어왔다.
   - 해결됨: 원격의 `release/cnu` 를 같은 커밋(`307bb22`)의 `release-cnu` 로 바꿔 보존했다. 이제 CI 가
     `release` 브랜치를 만들 수 있다.
   - 남은 것: 저장소 Actions 설정상 GITHUB_TOKEN 으로는 PR 을 만들 수 없다. 설정을 바꾸거나
     `RELEASE_PR_TOKEN` 시크릿(PAT)을 둬야 한다.
7. **줄바꿈** — 해결됨: LF 로 고정(`.gitattributes`, `.bat`/`.cmd`/`.ps1` 만 CRLF). `docs/build/` 는 추적한다.
8. **GitHub Pages 배포** — "브랜치에서 배포" 는 `/` 또는 `/docs` 만 고를 수 있어 `docs/guide/` 를
   바로 쓸 수 없다. `pages.yml` 워크플로가 들어왔으니, 저장소 설정에서 Pages 소스를 "GitHub Actions" 로
   바꾸면 된다(`main` 푸시 때 배포).
9. **Ollama 대체의 범위** — 모델 pull/list/rm/ps, keep-alive, 여러 모델 동시 상주, Ollama 호환 API 중
   어디까지 할지. 4비트 품질과 GGUF 읽기는 torchnative 쪽 작업에 달려 있다.

## 7. 마일스톤 (2026-10-03 재조정: 11월 말 실사용)

서빙 전환(#57) 기준이다. GitHub 마일스톤과 같다. 사용자 지시에 따라 **실제로 쓸 수 있는 수준을
2026-11-30 까지** 낸다. 실사용 수준이란 torchnative 단일 엔진으로 로컬 모델을 받아 스트리밍 채팅과
도구 호출을 하고, 동시 요청을 처리하고, OpenAI·Ollama 호환 API 로 붙는 상태다. 날짜를 당기려고
품질 기준을 낮추지는 않았다. 대신 범위를 줄였다.

| 마일스톤 | 목표일 | 범위 | 완료 기준 | 이슈 |
|---|---|---|---|---|
| **M1 단일 torchnative 엔진** | 2026-11-16 | transformers `generate` 기반 엔진 하나(공개 torch API 만 사용). GGUF·BIN·GPTQ 제거. Python 3.13. q8_0. WebSocket 스트리밍 채팅과 서버 측 도구 유지. 요청 직렬화. 연결이 끊기면 생성 중단. 모델은 Qwen3-0.6B(기본)와 SmolLM2. 자동 테스트는 torchnative #23(Qwen3 상류 일치)이 착지할 때까지 SmolLM2 | torchnative(cpu, mps)에서 스트리밍 채팅 테스트 통과. `llama-cpp`·`bitsandbytes` import 0건 | #36 #35 |
| **M2 동시 요청** | 2026-11-23 | transformers `generate_batch` 기반 연속 배칭과 페이지드 KV 캐시. attention 은 `sdpa_paged`/`eager_paged`(순수 torch op) | 동시 두 요청의 출력이 순차 생성과 같다. 같은 시드의 샘플링은 배칭 여부와 상관없이 같은 출력을 낸다. 처리량 측정 기록 | #63 #64 |
| **M3 실사용 Ollama 대체** | 2026-11-30 | OpenAI 호환 `/v1/chat/completions`(스트리밍, tool call pass-through)·`/v1/models`. Ollama 핵심 엔드포인트(`/api/chat` `/api/generate` `/api/tags` `/api/show` `/api/pull` `/api/delete` `/api/ps`)와 `keep_alive`. 상주 모델 1개. Hugging Face 의 q8_0 | OpenAI·Ollama 클라이언트로 tool call 왕복 테스트 통과. Ollama 클라이언트로 작은 모델 pull·list·채팅·삭제 | #65 #66 #57 |
| **M4 성능과 범위 확장** | 2027-02-26 | 아래 "11월에서 뺀 것" | 항목별 이슈에 적음 | #74 #75 #67 #37 |

**11월에서 뺀 것과 이유**

| 뺀 것 | 이유 | 이슈 |
|---|---|---|
| 빠른 paged attention 커널 | 완전히 새로 만들어야 하는 torchnative 커널이다(TN-M2). 11월에는 순수 torch op 경로로 정확성만 맞춘다 | #74 |
| 4비트(Q4) 가중치, GGUF 가져오기 | torchnative Q4_0 의 생성 품질이 떨어진다(logit RMS 29.5%). GGUF 리더도 없다(TN-M3). 11월에는 q8_0 만 낸다 | #67 |
| 여러 모델 동시 상주와 축출, 나머지 Ollama 명령(create/copy/push, Modelfile, embeddings) | 실사용에 필요한 최소 범위 밖이다. 상주 모델 1개와 핵심 엔드포인트로 시작한다 | #75 |
| 세션 프리픽스 캐시 | 체감 속도는 좋아지지만 정확성 기능은 아니다 | #37 |
| Qwen3 4B 이상과 8B 이상 모델 | Qwen3 은 0.6B 부터 검증한다(torchnative #23). 4비트가 없으면 큰 모델은 메모리 부담이 크다 | — |
| CUDA 서버 경로 | torchnative CUDA 는 연결만 돼 있고 실행해 본 적이 없다 | — |

**위험**

- torchnative TN-M1(스트리밍, q8_0)이 11-13 에 나온다. Gemstone M1(11-16)까지 사흘뿐이다. 그래서 엔진은
  upstream torch 로 먼저 개발하고 11-13 이후 torchnative 로 옮긴다. 공개 API 만 쓰므로 코드는 같다.
- 연속 배칭 경로가 torchnative 에서 한 번도 실행된 적이 없다. torchnative 가 11-16 까지 cpu·mps 에서
  돌려야 M2(11-23)를 맞춘다. Native 런타임 설계자에게 요청했다(막히는 op 목록은 11-06 쯤 받기로 요청).
- 커널 없이 순수 op 로 돌리면 긴 문맥이 느리다. 11월 실사용 범위는 1B–3B 모델이다.
- 날짜가 밀리면 이 표와 GitHub 마일스톤을 함께 고친다.
