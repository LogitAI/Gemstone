# Native desktop build (GraalVM native-image)

Compiles the desktop target to a standalone native executable — no JVM, no JDK on the target
machine, no installer.

```powershell
$env:JAVA_HOME = "<a GraalVM 22 installation>"
$env:GRAALVM_HOME = $env:JAVA_HOME

./gradlew :app:nativeDist
./app/build/dist/bin/gemstone.exe
```

Output is `app/build/dist`, about 109MB: an 84MB executable plus the native libraries it needs.

## Tasks

| Task | What it does |
|---|---|
| `generateNativeResourceConfig` | Enumerates Compose Resources and `META-INF/services` from the classpath into `resource-config.json` |
| `nativeCompile` | Builds the executable into `build/native/nativeCompile/bin` |
| `nativeDist` | Stages the executable and every native library it needs into `build/dist` |
| `metadataCopy` | Copies tracing agent output into `src/desktopMain/resources/META-INF/native-image` |

## Why the layout looks like a JDK

`build/dist` has a `bin` and a `lib` directory because AWT expects to find things relative to
`java.home`, and **`java.home` is null in a native image**:

- AWT loads its native libraries from `<java.home>/bin`. Unset, the binary dies opening
  `(null)\bin\jawt.dll` — a hard `0xC0000409` crash with no Java stack trace and no build-time
  warning.
- AWT reads its font configuration from `<java.home>/lib`. Without it `Font.createFont` fails
  with `IOException: Problem reading font data`, which is what Jewel hits loading its Inter font.

native-image emits the JDK libraries next to the executable anyway, so putting the executable
*inside* `bin` makes its parent a valid `java.home`.
[`NativeRuntime.kt`](../../app/src/desktopMain/kotlin/gemstone/NativeRuntime.kt) derives it at
startup from the executable's own path. Move the executable out of `bin` and it will tell you
so on stderr before it dies.

Skiko needs the same treatment for a different reason: it normally unpacks Skia out of its
runtime jar into `~/.skiko`, and a native image has no jar to unpack from. `nativeDist` stages
`skiko-windows-x64.dll` and `icudtl.dat` into `bin`, and `NativeRuntime.kt` points
`skiko.library.path` / `skiko.data.path` at them. Skip this and the app renders only on the
machine that built it.

## Reachability metadata

Resources are **enumerated from the classpath**, not discovered by running the app. This matters:
the tracing agent only records what was actually loaded, and an agent run that opened the app and
clicked around recorded 13 of this project's 24 Compose resources. The other 11 are icons on
screens that were not visited, and they would have failed at runtime. `generateNativeResourceConfig`
finds all 24 in a second, with no display and no interaction.

The JNI and reflection metadata under `src/desktopMain/resources/META-INF/native-image` still
comes from a tracing agent run:

```powershell
$env:JAVA_HOME = "<a GraalVM 22 installation>"
Start-Job { ./gradlew :app:run -Pagent }
./script/drive-desktop.ps1     # exercises the UI, then closes the window cleanly
./gradlew :app:metadataCopy
```

The agent only writes its configuration on a clean JVM shutdown, which is why the script closes
the window rather than killing the process.

Most of that metadata is not actually specific to this app — 160 of its 164 JNI entries are
identical to what a two-button Compose hello world needs, because they describe the
AWT/Skiko/Compose stack rather than Gemstone. The intent is for that part to become a
version-pinned bundle applications consume instead of regenerating; see
[compose-graal-hello/docs/metadata-strategy.md](https://github.com/thisisthepy/compose-graal-hello)
for the measurements behind that.

## Known limitations

- Windows x64 only so far.
- Startup prints `java.lang.Error: no ComponentUI class for: javax.swing.JRootPane`. The app
  recovers and renders correctly; the cause is unresolved and tracked upstream as
  [oracle/graal#9284](https://github.com/oracle/graal/issues/9284).
- The agent run did not exercise networking, so Ktor and serialization paths may still be
  missing metadata. Sending an actual chat message in the native build is unverified.
- Jewel needs JNA at runtime but does not declare it — inside the IDE the platform supplies it.
  It is added as an explicit `runtimeOnly` dependency; without it the desktop app fails on first
  composition with `NoClassDefFoundError: com/sun/jna/Library`, on the JVM as well as natively.
