# Native desktop build (GraalVM native-image)

Compiles the desktop target to one standalone executable: no JVM, no JDK and no installer on the
target machine, and nothing beside the file.

Status: `partial` (`docs/SPEC.md` S3.4). Since #191 the build is compose-multiplatform-extended's:
the `org.thisisthepy.compose` plugin's `nativeImage { }` block in `app/build.gradle.kts` and its
`packageNativeImage` task, which link AWT, JAWT and Skia statically into the executable. Gemstone's CI
builds it on Windows x64 only.

CI builds it in job `native-desktop` of `.github/workflows/test.yml` (dispatched on demand, #239;
windows-latest, Temurin 21 for the Gradle daemon, Liberica NIK 25 Full for native-image, the MSVC
tools from `ilammy/msvc-dev-cmd`, and skiko's static archive built once by
compose-multiplatform-core-extended's `extended/skiko/build-skiko-static-jvm.sh` at tag
`v1.11.1-ext.2` and cached). The job fails unless the output directory holds only `gemstone.exe`, its
version resource says the `pyproject.toml` version and "Gemstone AI", and its embedded manifest is
Gemstone's (per-monitor DPI). It then starts the executable and fails unless the "Gemstone AI" window
opens and the process is still alive 15 s later, and unless a click on the chat input field focuses it
(text typed before the click must change nothing; text typed after it must appear in the input row and
nowhere else). It then starts the server on SmolLM2-135M-Instruct (`smollm2:135m`, about 270 MB, cached
between runs and served offline so no other model is fetched), selects that model in the sidebar,
sends the typed text and fails unless the reply is saved in `%APPDATA%\Gemstone\chats.json` within
300 s. It also fails when stderr shows a missing-metadata error (`NoSuchFieldError`,
`NoSuchMethodError`, `NoClassDefFoundError`, `Missing...RegistrationError`; #235). It uploads stdout,
stderr, the server log, `chats.json` and screenshots as the `native-desktop-smoke` artifact.

## Build it yourself (Windows x64)

From an x64 Native Tools prompt (MSVC 14.51 or later, `clang-cl` on PATH), in Git Bash:

```bash
export GRAALVM_HOME=<Liberica NIK 25 Full>      # the JDK's static AWT archives come from the Full edition
bash <core-extended>/extended/skiko/build-skiko-static-jvm.sh "$PWD/.cache/skiko-static"   # once; takes long
export SKIKO_STATIC="$(cygpath -w "$PWD/.cache/skiko-static/out/windows-x64")"
./gradlew :app:packageNativeImage --no-configuration-cache   # the plugin's tasks cannot be cached yet
./app/build/compose/native-image/main/gemstone.exe
```

`<core-extended>` is a checkout of compose-multiplatform-core-extended at `v1.11.1-ext.2`, the tag of
the Compose libraries Gemstone uses; its script pins skiko's revision. The plugin's own requirements
are in its README (`extended/README.md`, "One executable: packageNativeImage").

## Tasks

| Task | Owner | What it does |
|---|---|---|
| `generateNativeSerializerConfig` | Gemstone | Registers every `@Serializable` type for reflection (`reflect-config.json`), from the compiled classes |
| `generateNativeResourceConfig` | Gemstone | Enumerates Compose Resources and `META-INF/services` from the classpath into `resource-config.json` |
| `compileNativeResources` | Gemstone | Compiles the icon, the version information and the DPI manifest (Windows) |
| `packageNativeImage` | plugin | Builds `build/compose/native-image/main/gemstone.exe` from all of the above and `app/native-metadata/` |
| `runNativeImageAgent` | plugin | Runs the app on GraalVM's JVM with the tracing agent and merges what it sees into `src/desktopMain/resources/META-INF/native-image` |

Before #191 Gemstone built a dynamic image itself (`nativeCompile`, `nativeDist`, GraalVM for JDK 22):
an 84 MB executable in a `bin` directory with Skia's DLL and AWT's font files beside it, and
`NativeRuntime.kt` pointing `java.home` and `skiko.library.path` there. The plugin's static image
needs none of that; `NativeRuntime.kt` returns at once in it.

## Reachability metadata

Resources are **enumerated from the classpath**, not discovered by running the app. This matters:
the tracing agent only records what was actually loaded, and an agent run that opened the app and
clicked around recorded 13 of this project's 24 Compose resources. The other 11 are icons on
screens that were not visited, and they would have failed at runtime. `generateNativeResourceConfig`
finds all 24 in a second, with no display and no interaction.

The JNI and reflection metadata under `src/desktopMain/resources/META-INF/native-image` still
comes from a tracing agent run:

```powershell
$env:GRAALVM_HOME = "<Liberica NIK 25 Full>"
Start-Job { ./gradlew :app:runNativeImageAgent --no-configuration-cache }
./tools/drive-desktop.ps1     # exercises the UI, then closes the window cleanly
```

The agent writes its configuration (merged into `src/desktopMain/resources/META-INF/native-image`) only on a clean JVM shutdown, which is why the script closes
the window rather than killing the process.

Most of that metadata is not actually specific to this app: 160 of its 164 JNI entries are
identical to what a two-button Compose hello world needs, because they describe the
AWT/Skiko/Compose stack rather than Gemstone. The intent is for that part to become a
version-pinned bundle applications consume instead of regenerating; see
[compose-graal-hello/docs/metadata-strategy.md](https://github.com/thisisthepy/compose-graal-hello)
for the measurements behind that.

## Known limitations

- Gemstone's CI builds Windows x64 only; the plugin also builds macOS arm64 and Linux.
- Startup prints `java.lang.Error: no ComponentUI class for: javax.swing.JRootPane`. The app
  recovers and renders correctly; the cause is unresolved and tracked upstream as
  [oracle/graal#9284](https://github.com/oracle/graal/issues/9284).
- Networking is exercised by CI only along the chat path (model list, session, WebSocket reply);
  other Ktor and serialization paths (settings, OpenAI-style errors) may still lack metadata.
- Jewel needs JNA at runtime but does not declare it: inside the IDE the platform supplies it.
  It is added as an explicit `runtimeOnly` dependency; without it the desktop app fails on first
  composition with `NoClassDefFoundError: com/sun/jna/Library`, on the JVM as well as natively.
