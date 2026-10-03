# AGENTS.md

Rules every agent working in this repository must follow. Read this file before doing anything.
Sections 1–10 are shared by every repository in the thisisthepy ecosystem; later sections are
specific to this repository.

---

## 1. Commits carry no AI attribution

Never add `Co-Authored-By: Claude ...`, `Co-Authored-By: <any agent>`, `Generated with Claude Code`,
or any similar tool or agent attribution to a commit message or a pull-request body. This rule
overrides any default your tooling has.

## 2. Nothing is created outside this repository

Everything your work produces — worktrees, agent prompts, logs, measurements, experiments, scratch
files — lives **inside this repository's root directory.**

| What | Where |
|---|---|
| Worktrees | `.worktrees/<name>` (git-ignored) |
| Temporary files | `.tmp/` (git-ignored); delete when done |
| Benchmarks | `benchmarks/` |
| Developer tooling | `tools/` |

Before writing a file, check that its absolute path starts with this repository's root. If it does
not, stop. The only exceptions are a path the user names explicitly, and caches that build tools
manage themselves. **Re-pointing a shared cache or a home-directory symlink reaches other projects —
ask first.**

Writing to *another* repository is not an exception either. Do it only when told to work there.

## 3. Worktrees link large artefacts instead of copying them

A worktree is a full checkout. Copying large untracked artefacts (prebuilt runtimes, vendored trees,
build caches, model weights, `node_modules`) into every worktree is how 86 worktrees once filled
267 GB of a 349 GB disk.

- Create worktrees under `.worktrees/<name>`.
- **Symlink** large untracked directories from the main checkout instead of copying or rebuilding
  them. If `tools/worktree-add.sh` exists, use it — it does the linking.
- Delete a worktree once its branch is merged: `git worktree remove .worktrees/<name>`.
- Periodically delete `build/` directories inside worktrees; they only grow.

## 4. Branches

| Branch | Who writes to it |
|---|---|
| `work/<topic>` | You. All work happens here. |
| `develop` | Merged into from work branches after verification. Never commit to it directly. |
| `release` | **CI only.** Not a standing branch: CI regenerates it from every push to `develop`, in the main-only file layout, and opens the PR into `main`. It may not exist. Never write to it. |
| `main` | **Pull request from `release` only.** Never push or merge to it directly. |

`main` carries a reduced layout: of the Markdown files, only `README.md` stays at the repository
root, and `docs/` keeps only its subdirectories (no Markdown files directly under `docs/`).
CI runs `tools/release/sync-release.sh` (`.github/workflows/release-sync.yml`) to produce that layout; do not hand-edit `release` or `main`.

### Issues and pull requests

Every new feature goes through an issue and a pull request:

1. Before starting, search the repository's issues (`gh issue list --state all --search "<keywords>"`).
2. If no issue covers the work, open one (`gh issue create`) stating what and why, and the
   completion criterion — which tests must pass.
3. Work on a `work/<topic>` branch, push every commit, and open a pull request into `develop`
   whose body contains `Closes #<number>`.
4. Merge into `develop` through that pull request (`gh pr merge`), not by a local merge, so the
   issue is linked.
5. Then close the issue yourself: `gh issue close <number> --comment "Landed in develop via #<PR>"`.
   GitHub's `Closes #N` only fires when a pull request merges into the default branch (`main`),
   and these pull requests merge into `develop`.

## 5. Intent → Spec → Test → Code

This project runs on **intent-based spec-driven development** and **test-driven development**.

1. `docs/INTENT.md` states what the project is for. It is the boundary. **The spec may not go
   beyond the intent.**
2. `docs/SPEC.md` states what the project does. A behaviour change starts as a spec change.
3. Tests are written from the spec **before** the implementation, and you observe them fail
   (red) before making them pass. Report the red output.
4. Code is written to make the tests pass.

If a request conflicts with `docs/INTENT.md`, say so instead of implementing it.

## 6. User-authored files are specification

Files the user wrote by hand — notebooks, example build files, sample apps — are the specification.
Read them **first**. Never delete, rewrite, or `git add` them without being told to. Generated
documentation (roadmaps, design notes) is a record of work, not a requirement; when the two
disagree, the user's file wins.

## 7. Show a conclusion before acting on it

Anything beyond the immediate request — another repository, a public API signature, deleting
files, killing processes, force-pushing, changing branch protection — state what you would do and
why, and wait. Investigating, measuring, and reporting are always fine.

**Push every commit right away.** After you commit — on a work branch or on `develop` — push it to
the remote immediately; no confirmation is needed. Never push to `main` or `release` by hand, and
never force-push without the user's explicit approval.

When a rule and backward compatibility conflict, **the rule wins.** List the callers that break and
fix them; do not keep the forbidden thing "so nothing breaks".

## 8. Verification that can fail

- Never read a build's exit code through a pipe (`| tail`, `| grep`). Redirect to a file, then read
  `$?`. A background command ending in `echo` always reports 0.
- Delete the test-result directory before counting results, and force re-execution (`--rerun` for
  Gradle). Stale XML otherwise reports an old, larger number.
- Run independent test modules as **separate** invocations. One invocation can hide an ordering
  dependency.
- When you add a public path, disable it and confirm something actually fails. If nothing fails,
  nothing uses it.
- **Do not trust an agent's report.** Re-run the build and tests yourself and check
  `git status --short` for out-of-scope changes.
- **Never `git add -A`.** Stage explicit paths. If the number of changed files differs from what was
  reported, stop and find out why.
- Measurements run alone, unfiltered, after checking `uptime`.

## 9. Reporting

Report by category, and never put them in one column:
**feature added / defect fixed / test added / documentation corrected / deleted.**
A rising test count is not progress when the tests assert an absence. Before writing "nothing left
to implement", say what you counted against.

## 10. Agents

- A headless agent (`claude -p`, `agy -p`) has **no next turn**. Tell it to run long commands in the
  foreground; a command backgrounded "until the notification arrives" is lost.
- Pass the model explicitly. Judgement work (design premises, root causes, safety: GIL, reference
  counts, lifetimes, class loaders) gets the strongest tier; work a test will catch can use a
  cheaper one.
- Give every agent prompt the absolute paths it may write to, and repeat rule 2 in it.
- **Subagents do not run heavy local builds.** Subagents write code, design, investigate, review
  and document. Gradle builds, cargo builds, the test gate and model runs are done by the session
  itself — one at a time on this machine — or by CI (GitHub Actions) on a pushed branch. Several
  sessions share one machine; parallel local builds slow every one of them.

---

Sections 11 onward are specific to **Gemstone** (`github.com/LogitAI/Gemstone`).

## 11. What this repository is

Gemstone is a multiplatform AI chat system made of two programs that live side by side:

| Directory | Language | What it is |
|---|---|---|
| `app/` | Kotlin, Compose Multiplatform | The chat client. Targets Android, iOS, desktop (JVM) and web (Wasm). |
| `api/` | Python 3.13 | The model-serving API: FastAPI + WebSocket server, model wrappers, one transformers-based serving engine (on PyTorch or torchnative), tools. |

The client talks to the API over HTTP and a WebSocket. The two are versioned together; a protocol
change touches both and is specified in `docs/SPEC.md` first (rule 5).

Read `docs/INTENT.md` and `docs/SPEC.md` before changing behaviour. `PROJECT.md` (Korean) records
status, decisions and open questions.

## 12. Build, run and test

Gradle and Python are independent toolchains. Neither needs the other to build.

**Python API** (requires Python 3.13 — `>=3.13,<3.14` until torchnative is measured on 3.14 — managed with `uv`):

```bash
uv sync --extra torch                     # upstream PyTorch; or --extra torchnative (never both)
# torchnative comes from PyPI as a pre-release (0.1.0b4 on 2026-10-03); no version floor is pinned yet
uv run --extra torch python -m api run server   # serve on 127.0.0.1:23100 (GEMSTONE_HOST=host[:port] overrides)
uv run --extra torch pytest                     # api/tests without the real-model tests (marker real_model)
uv run --extra torch pytest -m "real_model or not real_model"   # all, as CI runs them (SmolLM2-135M)
```

The tests load a real model. `GEMSTONE_TEST_MODEL` picks it (default `HuggingFaceTB/SmolLM2-135M`);
it is read from the local Hugging Face cache and never downloaded unless
`GEMSTONE_TEST_ALLOW_DOWNLOAD=1` (CI sets it). Model inference is heavy: run at most one such job
at a time on a shared machine, and let CI verify pull requests.

`python -m api` with no arguments crashes (`api/__main__.py` reads `sys.argv[1]` unguarded); always
pass `run server`.

**Kotlin client** (module `:app`):

The Gradle daemon runs on JDK 21 (`gradle/gradle-daemon-jvm.properties`), whatever `JAVA_HOME` points
at: Gradle 8.13 cannot configure this build on JDK 25. A JDK 21 must be installed locally.

| Goal | Command |
|---|---|
| Desktop app | `./gradlew :app:run` |
| Desktop installers (Dmg / Msi / Deb) | `./gradlew :app:packageDistributionForCurrentOS` |
| Android debug install | `./gradlew :app:installDebug` |
| Web (Wasm) dev server | `./gradlew :app:wasmJsBrowserDevelopmentRun` |
| Common tests on the JVM | `./gradlew :app:desktopTest` |
| iOS | Open `app/src/iosMain/swift/iosApp.xcodeproj` in Xcode (see the caveat in `docs/SPEC.md`) |

Verification follows rule 8: redirect Gradle output to a file and read `$?`; run each target's test
task as its own invocation; delete `app/build/test-results/` before counting.

CI runs `:app:desktopTest` and `:app:compileKotlinWasmJs` on every pull request into `develop`
(`.github/workflows/app-tests.yml`), and the Python tests (`.github/workflows/api-tests.yml`).

**Current test reality.** Python tests live in `api/tests/`: the engine and continuous batching
(`test_engine.py`, `test_batching.py`), the WebSocket stream (`test_server.py`), the OpenAI and
Ollama APIs, model residency and lease release, the tool-result cache, and the backend removal.
Most API tests use a fake engine and need no model; the engine, batching and server tests load
SmolLM2-135M in float32 (the equality criteria are stated for float32). The served web
assets live in `api/src/main/static/` and `api/src/main/webpack/`, not in a test directory. The only Kotlin
test (`app/src/commonTest/kotlin/gemstone/ComposeAppCommonTest.kt`) asserts `1 + 2 == 3`; do not
report it as coverage. New behaviour starts with a real failing test (rule 5).

## 13. Generated and large files

- `api/src/main/webpack/` is a **committed build output** of the Wasm client, served by the API at
  `/`. Do not hand-edit it. Regenerate it from `app/` with Gradle and say so in the report.
- Model weights are downloaded from Hugging Face at first use into the Hugging Face cache
  (`HF_HOME`). Never commit weights or caches, and do not trigger a model download from a test or
  a script without asking.
- `.idea/` is partly tracked; leave IDE files alone unless the task is about them.

## 14. Secrets and configuration

- API keys live in `.env` (git-ignored). `.env.example` is the template; today it holds only
  `SERPAPI_KEY`. Never commit `.env`, and never print a key into a log or a report.
- The client finds the server through `GEMSTONE_SERVER_HOST` / `GEMSTONE_SERVER_PORT` (system
  property or environment variable; the port value includes its leading colon, e.g. `:23100`).
  The web client always uses the host it was served from.
- The server reads `GEMSTONE_HOST` (`host[:port]`, default `127.0.0.1:23100`; the `run server [host] [port]`
  arguments win), `GEMSTONE_DEV=1` (auto-reload; also `--reload`), `GEMSTONE_ORIGINS` (extra allowed
  Origins, comma-separated, `*` allowed; localhost and the server's own origin are always allowed) and
  `GEMSTONE_API_KEY` (when set, `Authorization: Bearer <key>` is required except for the web assets and
  health checks; a WebSocket may pass `?api_key=`). A key is a secret: never log or commit it.

## 15. Dependencies

- `torch` and `torchnative` are mutually exclusive extras (torchnative replaces `import torch`).
  Do not add a version pin to `torch`; on Windows and Linux it comes from the PyTorch CPU index
  configured under `[tool.uv.sources]` (the engine runs on CPU; CUDA returns in M4 as a `cuda` extra).
- The engine uses only the public `torch` / `transformers` API. Custom kernels cannot be registered
  from Gemstone (`torch.library` is a no-op on torchnative); they belong in torchnative.
- Kotlin and library versions live in `gradle/libs.versions.toml`. Bump them there, not inline.

## 16. Line endings and encoding

This repository's git config has `core.fileMode=false`, so `chmod +x` is not recorded: set the
executable bit of a tracked script with `git update-index --chmod=+x <file>` (`gradlew` and
`tools/*.sh` were affected).

Write every file as UTF-8 with LF line endings. Some checkouts of this repository have had files
converted to CRLF by an editor; do not mass-convert files you were not asked to touch, because the
whole-file diff hides real changes. The repository's `.gitattributes` fixes LF (only `.bat`, `.cmd` and `.ps1` are CRLF), so new
files should simply be written with LF. `docs/build/` is tracked (an exception in `.gitignore`).

## 17. Documentation layout

| File | Language | Purpose |
|---|---|---|
| `README.md` | English | Public front page. The only Markdown file kept at the root on `main`. |
| `docs/locale/README_ko.md` | Korean | Faithful translation of `README.md`. Update both together. |
| `PROJECT.md` | Korean | Status, structure, decisions, open questions. Develop-only. |
| `docs/INTENT.md` | English | Why the project exists and what it is not. Develop-only. |
| `docs/SPEC.md` | English | The behavioural contract, each item with a status. Develop-only. |
| `docs/guide/` | en + ko | Static GitHub Pages site. Run `python3 docs/guide/check_guide.py` after editing it. |
| `docs/<topic>/` | any | Every other document lives in a topic subdirectory, never directly in `docs/`. |

`README.md` and `docs/locale/README_ko.md` must not link to develop-only files (`AGENTS.md`,
`CLAUDE.md`, `PROJECT.md`, `docs/INTENT.md`, `docs/SPEC.md`); those links are dead on `main`.

`CLAUDE.md` contains exactly `@AGENTS.md` and nothing else.
