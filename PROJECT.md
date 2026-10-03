# Gemstone: 프로젝트 주요 사항

저장소: `github.com/LogitAI/Gemstone` · 라이선스: Apache-2.0 · 기준 커밋: `2be0e37` (2026-10-02)

의도는 [`docs/INTENT.md`](docs/INTENT.md), 동작 계약은 [`docs/SPEC.md`](docs/SPEC.md), 에이전트 규정은
[`AGENTS.md`](AGENTS.md) 에 있습니다. 이 문서는 그 셋을 한국어로 빠르게 훑기 위한 요약이며, 어긋나면
INTENT → SPEC 순으로 그쪽이 이깁니다.

---

## 1. 한 줄 요약

모든 플랫폼에서 같은 클라이언트로 쓰는 오픈소스 AI 채팅 시스템. 지금은 Python 서버가 모델을 돌리고,
목표는 Python Multiplatform 으로 모델을 **앱 안에서** 돌리는 것이다.

## 2. 현재 상태 (2026-10-03, develop 기준)

`api/tests/` 의 Python 테스트는 CI(GitHub Actions)에서 실제 모델(SmolLM2-135M, float32)로 돈다. 표에서
"테스트 있음"은 그 검증을 뜻하고, 나머지는 코드를 읽고 판정했다.

| 영역 | 상태 | 근거 |
|---|---|---|
| 단일 서빙 엔진 (transformers) | 구현, 테스트 있음 | `engine.py` (#85). torchnative 위 검증은 TN-M1(10-24) 이후 (#84) |
| 연속 배칭 · 페이지드 KV 캐시 | 구현, 테스트 있음 | `engine.py` (#91). 동일성 기준은 float32. 처리량 측정과 torchnative 검증은 남음 |
| 모델 상주 (앱 · OpenAI · Ollama 공유, keep_alive, 여러 모델과 LRU 축출) | 구현, 테스트 있음 | `registry.py`, `leases.py` (#92, #94, #75) |
| OpenAI 호환 API (`/v1`, tool call pass-through) | 부분, 테스트 있음 | `openai_api.py` (#88). n>1, logprobs, embeddings 없음 |
| Ollama 호환 API (핵심 8개 + copy/create/embed, 여러 상주 모델) | 부분, 테스트 있음 | `ollama_api.py` (#90, #75). push 는 501, Modelfile·template·blobs·format 없음 |
| WebSocket 스트리밍 채팅 (`/api/chat/streaming`) | 구현, 테스트 있음 | `server.py`. 연결이 끊기면 생성 중단 |
| 도구 호출과 세션별 결과 캐시 | 구현, 캐시는 테스트 있음 | `utils/` (#87, PR #53 기능 이식) |
| 4비트 가중치 · GGUF | 예정 (M4) | torchnative TN-M3 에 달림 (#67) |
| GGUF · BIN · GPTQ 백엔드, Llama 3.1, 옛 `POST /api/chat`·`/api/hello` | **제거됨** | #85, #90 |
| 세션 API | 부분 (결함) | 오류를 `raise` 대신 `return`, 404 대신 500, 생성 시 모델 이름 미검증 |
| Android · 데스크톱 · 웹 클라이언트 | 구현 | `app/build.gradle.kts` |
| iOS | 부분 | Xcode 스크립트가 없는 `:composeApp` 모듈을 호출 |
| 모델 선택, 서버 주소 | 구현, 테스트 있음 | 서버의 `GET /api/models` 를 읽음 (S2.5). 서버 주소 파싱과 API 키 포함 (S2.2). Android 는 주소 설정 화면이 아직 없음 |
| 대화 기록 | 부분 | 메모리에만 |
| 설정 화면, 도메인 계층(Clean Architecture) | 예정 | 빈 파일 |
| 온디바이스 추론 · 오프라인 · 동기화 | 예정 | 코드 없음 |
| 네이티브 데스크톱 (GraalVM) | 예정 (SPEC S3.4) | Windows x64 빌드 경로는 develop 에 있으나(#71) 네이티브 빌드는 실행해 본 적이 없다. 장기적으로 compose-multiplatform-extended 가 관리 |
| Kotlin 테스트 | 부분 | 서버 주소, 모델 목록 파싱, 종료 코드 매핑은 테스트 있음(CI). 화면과 뷰모델은 테스트 없음 |
| GPU(CUDA) 추론 | 예정 (M4) | 엔진 기본값은 `device="cpu"`. torchnative CUDA 는 실행해 본 적이 없다 |

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
  src/main/static/        Brython 테스트 페이지 (`/chat`)
  src/main/webpack/       빌드된 Wasm 클라이언트 (`/`)
docs/               INTENT, SPEC, locale/, guide/ (GitHub Pages), serving/ (서빙 엔진 결정 기록), build/
```

## 4. 빌드와 실행

```bash
uv sync --extra torch            # Python 의존성 (Python 3.13). torchnative 는 --extra torchnative
                                 # torchnative 는 PyPI 프리릴리스(2026-10-03 기준 0.1.0b4). 버전 하한은 결정 대기
uv run --extra torch pytest      # Python 테스트 (api/tests)
uv run --extra torch python -m api run server   # 127.0.0.1:23100 (GEMSTONE_HOST 로 변경)
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
- **`CLAUDE.md` 는 두지 않는다**(2026-10-03). Claude Code 가 `AGENTS.md` 를 직접 읽는다.
- **브랜치**: 작업 브랜치는 `feat/<topic>`. `main`·`develop`·`release`(와 보존용 `release-*`)만 상시 브랜치로 두고, 머지된 브랜치는 머지 때 `--delete-branch` 로, 그 밖에는 주기적으로 지운다.
- **토치 버전**: `pyproject.toml` 에서 torch 계열에 버전을 고정하지 않는다 (Windows 는 cu128 인덱스).
- **서빙 구조 (2026-10-02)**: 근거와 위험은 [`docs/serving/engine.md`](docs/serving/engine.md) 에 있다.
  - 의미 없는 다중 백엔드를 걷어내고 **단일 서빙 시스템**으로 간다. GGUF·BIN·GPTQ 런타임과
    `llama-cpp-python`·`bitsandbytes` 의존성을 제거한다.
  - 서빙 시스템은 **torchnative 에 의존**한다. 그래서 Python 요구 버전이 3.13 으로 오른다(torchnative 가 3.14 에서 측정될 때까지 `<3.14`).
  - Gemstone 은 **Ollama 대체제**가 된다.
  - **연속 배칭(continuous batching)과 페이지드 어텐션(paged attention)을 도입**한다.
  - vLLM 은 의존성으로 쓰지 않는다. 별도의 사용자 결정이 아니라 위 결정(torchnative 기반 단일 서빙)에서
    따라 나오는 것이다. 방향이 바뀌면 함께 다시 본다. torchnative 위에서 돌 수 없고(libtorch ABI·`torch.compile`
    전제), 서버와 기기에 스택이 둘이 되기 때문이다.
- **네이티브 데스크톱 (2026-10-02)**: GraalVM native-image 는 `compose-multiplatform-extended`
  (Compose Gradle 플러그인 포크)가 종합 관리한다. 플러그인에 아직 코드가 없어서, 그때까지는 Gemstone 의
  현재 경로를 유지한다. JNA 의존성은 GraalVM 과 무관하므로 Gemstone 에 남긴다. 입력 디버그 프로브
  (`GEMSTONE_INPUT_PROBE`)는 커밋하지 않는다.
- **우선순위 (2026-10-04)**: thisisthepy 저장소는 우선순위대로 하나씩 진행하고, Gemstone 은 마지막
  순위다(torchnative → python-multiplatform → pypackpack/toolchain-lite → pythonx-compose → 기타 pythonx,
  Gemstone). 사용자는 당분간 darkpyonix 에 집중한다. 새 무거운 작업(로컬 빌드, 서브 에이전트)은 리더의
  신호를 받고 시작한다.

## 6. 열린 질문

1. **외부 웹 서비스를 쓰는 도구**(SerpApi, Bing, Open-Meteo 등)가 "프라이버시 우선 · 오프라인"
   의도와 맞는가? 범위 안인지, 선택 기능인지, 서버 전용인지 결정이 필요하다.
2. **원격 모델 제공자**(OpenAI, Anthropic, HF Inference): 옛 README 트리에만 있고 코드·의도에 없다.
3. **동기화(sync)** 가 무엇을 무엇으로 동기화하는지.
4. **서빙 엔진 세부**: 구현됐다(#85, #91). transformers 5.x 연속 배칭과 페이지드 KV 캐시를 쓰고,
   커널은 torchnative 에 둔다. torchnative 위 검증 현황(#84):
   - torchnative 0.1.0b4, Linux CPU(CI): 411 통과, 연속 배칭 8개만 `pin_memory` 미지원으로 skip.
   - 같은 버전, 이 맥의 mps: `generate` 가 transformers 의 `isin` 에서 멈춘다(torchnative #29).
   - 배칭을 막던 torchnative 결함(#30, cb-path)은 torchnative develop 에 들어갔다. 그 휠을 CI 가 받을
     URL(GitHub pre-release)은 사용자 승인을 기다린다. `test-torchnative.yml` 은 `wheel_url` 입력을 받는다.
   - 처리량 측정(`benchmarks/batching_throughput.py`)은 아직 돌리지 않았다.
5. **Python 테스트의 위치**: 해결됨(2026-10-03): `api/tests/` 에 pytest 로 둔다. 정적 자산도
   `api/src/test/` 에서 `api/src/main/{static,webpack}/` 로 옮겼다(#83).
6. **릴리스 흐름**: 해결됨. develop push 마다 CI 가 `release` 를 만들고 release → main PR 을 연다.
   main 의 보호는 사용자가 저장소 설정에서 관리하고 잠가 둔다. release PR 은 사용자가 확인하고 머지한다.
   에이전트는 보호 설정을 스크립트로 만들거나 바꾸지 않는다. 출발 브랜치를 검사하던
   `main-source-guard` 워크플로는 없앴다. `release-cnu` 는 보존 브랜치다.
7. **줄바꿈**: 해결됨: LF 로 고정(`.gitattributes`, `.bat`/`.cmd`/`.ps1` 만 CRLF). `docs/build/` 는 추적한다.
8. **GitHub Pages 배포**: 해결됨(2026-10-03). Pages 소스는 GitHub Actions, main 에서만 배포한다
   (`pages.yml`). 주소는 https://logitai.github.io/Gemstone/ 이고, 첫 배포는 release → main PR 이
   머지되면 일어난다.
9. **Ollama 대체의 범위**: 핵심 엔드포인트, 여러 모델 상주와 축출, copy/create/embed 는 구현했다(#90, #107).
   남은 것은 4비트 품질과 GGUF 읽기(#67)로, torchnative 작업에 달려 있다.
10. **torchnative 버전 하한**: PyPI 최신은 `0.1.0b4` 이고 배칭 수정이 없다. 수정이 든 `0.1.0b5` 를
    언제 올릴지, Gemstone 이 어떤 하한(`>=0.1.0b5` 등)을 걸지 사용자 결정을 기다린다.
11. **`script/` 의 자리**: 해결됨(2026-10-04). `tools/drive-desktop.ps1` 로 옮겼다. `.idea/` 도 추적을 끊었다.

## 7. 마일스톤 (2026-10-03 재조정: 11월 말 실사용)

> 2026-10-04: Gemstone 이 thisisthepy 의 마지막 순위가 되어(§5) 아래 날짜는 다시 정해야 한다.
> 코드는 M1~M3 범위가 develop 에 있고, 남은 것은 torchnative 위 검증과 출시 결정이다.

서빙 전환(#57) 기준이다. GitHub 마일스톤과 같다. 사용자 지시에 따라 **실제로 쓸 수 있는 수준을
2026-11-30 까지** 낸다. 실사용 수준이란 torchnative 단일 엔진으로 로컬 모델을 받아 스트리밍 채팅과
도구 호출을 하고, 동시 요청을 처리하고, OpenAI·Ollama 호환 API 로 붙는 상태다. 날짜를 당기려고
품질 기준을 낮추지는 않았다. 대신 범위를 줄였다.

| 마일스톤 | 목표일 | 범위 | 완료 기준 | 이슈 |
|---|---|---|---|---|
| **M1 단일 torchnative 엔진** | 2026-11-16 | transformers `generate` 기반 엔진 하나(공개 torch API 만 사용). GGUF·BIN·GPTQ 제거. Python 3.13. q8_0. WebSocket 스트리밍 채팅과 서버 측 도구 유지. 요청 직렬화. 연결이 끊기면 생성 중단. 모델은 Qwen3-0.6B(기본)와 SmolLM2. 자동 테스트는 torchnative #23(Qwen3 상류 일치)이 착지할 때까지 SmolLM2 | torchnative(cpu, mps)에서 스트리밍 채팅 테스트 통과. `llama-cpp`·`bitsandbytes` import 0건 | #36 #35 |
| **M2 동시 요청** | 2026-11-23 | transformers `generate_batch` 기반 연속 배칭과 페이지드 KV 캐시. attention 은 `sdpa_paged`/`eager_paged`(순수 torch op) | 동시 두 요청의 출력이 순차 생성과 같다. 같은 시드의 샘플링은 배칭 여부와 상관없이 같은 출력을 낸다. 처리량 측정 기록 | #63 #64 |
| **M3 실사용 Ollama 대체** | 2026-11-30 | OpenAI 호환 `/v1/chat/completions`(스트리밍, tool call pass-through)·`/v1/models`. Ollama 핵심 엔드포인트(`/api/chat` `/api/generate` `/api/tags` `/api/show` `/api/pull` `/api/delete` `/api/ps`)와 `keep_alive`. 상주 모델 1개(이후 #75 에서 여러 개로 확장). Hugging Face 의 q8_0 | OpenAI·Ollama 클라이언트로 tool call 왕복 테스트 통과. Ollama 클라이언트로 작은 모델 pull·list·채팅·삭제 | #65 #66 #57 |
| **M4 성능과 범위 확장** | 2027-02-26 | 아래 "11월에서 뺀 것" | 항목별 이슈에 적음 | #74 #75 #67 #37 |

**11월에서 뺀 것과 이유**

| 뺀 것 | 이유 | 이슈 |
|---|---|---|
| 빠른 paged attention 커널 | 완전히 새로 만들어야 하는 torchnative 커널이다(TN-M2). 11월에는 순수 torch op 경로로 정확성만 맞춘다 | #74 |
| 4비트(Q4) 가중치, GGUF 가져오기 | torchnative Q4_0 의 생성 품질이 떨어진다(logit RMS 29.5%). GGUF 리더도 없다(TN-M3). 11월에는 q8_0 만 낸다 | #67 |
| ~~여러 모델 동시 상주와 축출, 나머지 Ollama 명령(create/copy/push, Modelfile, embeddings)~~ | **구현함**(develop 반영 대기). 상주 모델 수 3개(`GEMSTONE_MAX_LOADED_MODELS`)와 메모리 한도, 사용 중이 아닌 모델의 LRU 축출, copy·create(system·parameters)·embed. push 는 501, Modelfile·template 은 지원하지 않는다 | #75 |
| 세션 프리픽스 캐시 | 체감 속도는 좋아지지만 정확성 기능은 아니다 | #37 |
| Qwen3 4B 이상과 8B 이상 모델 | Qwen3 은 0.6B 부터 검증한다(torchnative #23). 4비트가 없으면 큰 모델은 메모리 부담이 크다 | - |
| CUDA 서버 경로 | torchnative CUDA 는 연결만 돼 있고 실행해 본 적이 없다 | - |

**위험**

- torchnative TN-M1(스트리밍, q8_0)이 11-13 에 나온다. Gemstone M1(11-16)까지 사흘뿐이다. 그래서 엔진은
  upstream torch 로 먼저 개발하고 11-13 이후 torchnative 로 옮긴다. 공개 API 만 쓰므로 코드는 같다.
- 연속 배칭 경로가 torchnative 에서 한 번도 실행된 적이 없다. torchnative 가 11-16 까지 cpu·mps 에서
  돌려야 M2(11-23)를 맞춘다. Native 런타임 설계자에게 요청했다(막히는 op 목록은 11-06 쯤 받기로 요청).
- 커널 없이 순수 op 로 돌리면 긴 문맥이 느리다. 11월 실사용 범위는 1B–3B 모델이다.
- 날짜가 밀리면 이 표와 GitHub 마일스톤을 함께 고친다.
