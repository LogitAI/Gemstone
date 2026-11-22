import java.net.URLClassLoader
import java.util.zip.ZipFile
import org.jetbrains.kotlin.gradle.targets.js.webpack.KotlinWebpackConfig
import org.jetbrains.compose.desktop.application.dsl.TargetFormat
import org.jetbrains.kotlin.gradle.ExperimentalWasmDsl
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.kotlin.multiplatform)
    alias(libs.plugins.android.application)
    alias(libs.plugins.compose.multiplatform)
    alias(libs.plugins.kotlin.native.desktop)
    alias(libs.plugins.compose.compiler)
    alias(libs.plugins.compose.hotreload)
    kotlin("plugin.serialization").version(libs.versions.kotlin.get())
}

// Gemstone's one version, shared by the client and the API: the `version` of pyproject.toml's
// [project] table, written nowhere else (owner, 2026-11-21; api/tests/test_client_compat.py guards it).
val appVersion: String = Regex("""(?m)^version\s*=\s*"([^"]+)"""")
    .find(providers.fileContents(rootProject.layout.projectDirectory.file("pyproject.toml")).asText.get())
    ?.groupValues?.get(1) ?: error("pyproject.toml has no [project] version")


kotlin {
    androidTarget {
        compilerOptions {
            jvmTarget.set(JvmTarget.JVM_11)
        }
    }

    // No iosX64: Compose Multiplatform 1.11 publishes no iosX64 artifacts (Intel simulators are gone).
    listOf(
        iosArm64(),
        iosSimulatorArm64()
    ).forEach { iosTarget ->
        iosTarget.binaries.framework {
            baseName = "Gemstone"
            isStatic = true
        }
    }

    jvm("desktop")

    @OptIn(ExperimentalWasmDsl::class)
    wasmJs {
        compilerOptions {
            moduleName = "gemstone"
        }
        browser {
            commonWebpackConfig {
                outputFileName = "gemstone.js"
            }
        }
        binaries.executable()
    }

    // cioMain is shared by every target that uses Ktor's CIO engine: Android, desktop (JVM) and iOS.
    // It is declared through the hierarchy template: explicit dependsOn() calls switch the default
    // template off, which left iosMain out of every iOS compilation.
    applyDefaultHierarchyTemplate {
        common {
            group("cio") {
                withAndroidTarget()
                withJvm()
                group("ios")
            }
        }
    }

    sourceSets {
        val desktopMain by getting
        val cioMain by getting {
            dependencies {
                api(libs.ktor.client.cio)
            }
        }
        wasmJsMain.dependencies {
            api(libs.ktor.client.js)
        }

        androidMain.dependencies {
            api(libs.compose.material3)
            api(libs.compose.ui.tooling.preview)
            api(libs.androidx.activity.compose)
        }
        commonMain.dependencies {
            api(libs.compose.runtime)
            api(libs.compose.foundation)
            api(libs.compose.material3)
            // `material-icons-extended` and `material-icons-core` stay at 1.7.3 under `org.jetbrains.compose.material`
            // (not published by compose-multiplatform-core-extended), so exclude their transitive `org.jetbrains.compose.*`
            // dependencies other than `material-icons-core` to avoid pulling a second Compose copy onto the classpath.
            api(libs.compose.material.icons.extended.get().toString()) {
                exclude(group = "org.jetbrains.compose.annotation-internal")
                exclude(group = "org.jetbrains.compose.collection-internal")
                exclude(group = "org.jetbrains.compose.foundation")
                exclude(group = "org.jetbrains.compose.material", module = "material")
                exclude(group = "org.jetbrains.compose.material", module = "material-ripple")
                exclude(group = "org.jetbrains.compose.runtime")
                exclude(group = "org.jetbrains.compose.ui")
            }
            api(libs.compose.ui)
            // `components-resources` and `components-ui-tooling-preview` stay at 1.11.1 under `org.jetbrains.compose.components`;
            // exclude their transitive `org.jetbrains.compose.*` dependencies so they resolve from `org.thisisthepy.compose.*`.
            api(libs.compose.components.resources.get().toString()) {
                exclude(group = "org.jetbrains.compose.annotation-internal")
                exclude(group = "org.jetbrains.compose.collection-internal")
                exclude(group = "org.jetbrains.compose.foundation")
                exclude(group = "org.jetbrains.compose.runtime")
                exclude(group = "org.jetbrains.compose.ui")
            }
            api(libs.compose.components.ui.tooling.preview.get().toString()) {
                exclude(group = "org.jetbrains.compose.annotation-internal")
                exclude(group = "org.jetbrains.compose.collection-internal")
                exclude(group = "org.jetbrains.compose.foundation")
                exclude(group = "org.jetbrains.compose.runtime")
                exclude(group = "org.jetbrains.compose.ui")
            }
            api(libs.androidx.lifecycle.viewmodel)
            api(libs.androidx.lifecycle.runtimeCompose)
            api(libs.navigation.compose)

            // For API calls and JSON serialization
            implementation(libs.ktor.client.core)
            implementation(libs.ktor.client.websockets)
            implementation(libs.ktor.client.content.negotiation)
            implementation(libs.ktor.serialization.kotlinx.json)
            implementation(libs.kotlinx.coroutines.core)
            implementation(libs.kotlinx.serialization.json)
            implementation(libs.kotlinx.datetime)

            // For Compose WebView support
            //api(libs.compose.webview.multiplatform)
        }
        commonTest.dependencies {
            api(libs.kotlin.test)
        }
        // Compose UI tests on the JVM: the chat input's cursor (#41)
        val desktopTest by getting {
            dependencies {
                implementation(libs.compose.ui.test.junit4)
            }
        }
        desktopMain.dependencies {
            api(compose.desktop.currentOs) {
                exclude(group = "org.jetbrains.compose.material3")
                exclude(group = "org.thisisthepy.compose.material3")
            }
            api(libs.kotlinx.coroutines.swing)

            // Jewel is built against upstream `org.jetbrains.compose.*`; exclude those transitives so
            // the desktop classpath resolves a single Compose copy from `org.thisisthepy.compose.*`.
            implementation(libs.jewel.standalone.get().toString()) {
                exclude(group = "org.jetbrains.compose.annotation-internal")
                exclude(group = "org.jetbrains.compose.collection-internal")
                exclude(group = "org.jetbrains.compose.animation")
                exclude(group = "org.jetbrains.compose.desktop")
                exclude(group = "org.jetbrains.compose.foundation")
                exclude(group = "org.jetbrains.compose.material")
                exclude(group = "org.jetbrains.compose.runtime")
                exclude(group = "org.jetbrains.compose.ui")
            }
            implementation(libs.jewel.decorated.window.get().toString()) {
                exclude(group = "org.jetbrains.compose.annotation-internal")
                exclude(group = "org.jetbrains.compose.collection-internal")
                exclude(group = "org.jetbrains.compose.animation")
                exclude(group = "org.jetbrains.compose.desktop")
                exclude(group = "org.jetbrains.compose.foundation")
                exclude(group = "org.jetbrains.compose.material")
                exclude(group = "org.jetbrains.compose.runtime")
                exclude(group = "org.jetbrains.compose.ui")
            }
            implementation(libs.jewel.foundation.get().toString()) {
                exclude(group = "org.jetbrains.compose.annotation-internal")
                exclude(group = "org.jetbrains.compose.collection-internal")
                exclude(group = "org.jetbrains.compose.animation")
                exclude(group = "org.jetbrains.compose.desktop")
                exclude(group = "org.jetbrains.compose.foundation")
                exclude(group = "org.jetbrains.compose.material")
                exclude(group = "org.jetbrains.compose.runtime")
                exclude(group = "org.jetbrains.compose.ui")
            }

            // Jewel's standalone cursor handling loads JNA at class-init, but Jewel does not
            // declare it -- inside the IDE the platform supplies it. Without these the desktop
            // app dies on first composition with NoClassDefFoundError: com/sun/jna/Library.
            // The API key's secure storage also calls it: the macOS Keychain and Windows DPAPI (S2.7).
            implementation(libs.jna)
            implementation(libs.jna.platform)
        }
    }
}

android {
    namespace = "io.github.thisisthepy.gemstone"
    compileSdk = libs.versions.android.compileSdk.get().toInt()

    defaultConfig {
        applicationId = "io.github.thisisthepy.gemstone"
        minSdk = libs.versions.android.minSdk.get().toInt()
        targetSdk = libs.versions.android.targetSdk.get().toInt()
        versionCode = 3  // an integer that grows with every release Android installs over (owner, 2026-11-21)
        versionName = appVersion
    }
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
}

dependencies {
    implementation(libs.androidx.runtime.android)
    debugImplementation(libs.compose.ui.tooling)
}

// ---------------------------------------------------------------------------------------------
// GraalVM native-image for the desktop target (SPEC S3.4, #191).
//
// compose-multiplatform-extended builds it: `packageNativeImage` of the org.thisisthepy.compose
// plugin links AWT, JAWT and Skia statically into one executable (configured in `nativeImage { }`
// below). Gemstone keeps only what is its own: the reachability metadata derived from its classes
// and classpath, its bundled metadata, and the Windows icon and version resource.
// ---------------------------------------------------------------------------------------------

val nativeExecutableName = "gemstone"

val desktopMainCompilation = kotlin.targets.getByName("desktop").compilations.getByName("main")
val desktopRuntimeClasspath = files(
    desktopMainCompilation.output.allOutputs,
    desktopMainCompilation.runtimeDependencyFiles
)

val nativeMetadataDir = layout.projectDirectory.dir("src/desktopMain/resources/META-INF/native-image")
val generatedMetadataDir = layout.buildDirectory.dir("generated/native-image")

/**
 * Reachability metadata for the AWT/Skiko/Compose stack itself, and for third-party libraries.
 * These entries do not depend on this application -- 160 of the 164 JNI entries a tracing agent
 * records here are identical to what a two-button Compose hello world needs -- so they are
 * consumed as a version-pinned bundle rather than rediscovered by driving the UI.
 */
val metadataBundleDirs = listOf(
    layout.projectDirectory.dir("native-metadata/compose-desktop-awt"),
    layout.projectDirectory.dir("native-metadata/libraries")
)

/**
 * Resource metadata derived from the classpath rather than from a tracing agent run.
 *
 * The agent only records resources that were actually loaded, so it finds the icons on whatever
 * screens someone happened to visit and silently misses the rest -- this project has 24 resources
 * and an agent run that opened the app and clicked around recorded 13. The missing ones fail at
 * runtime, on screens nobody exercised.
 *
 * Compose Resources and ServiceLoader files are both fully enumerable at build time, so scanning
 * is complete by construction and needs no UI driving at all.
 */
/**
 * Reflection metadata for kotlinx.serialization, derived from the compiled classes.
 *
 * `serializer(KType)` -- which type-safe Navigation calls for every route -- looks the serializer
 * up reflectively via the `Companion` and `$$serializer` members the compiler plugin generates.
 * In a native image that lookup finds nothing unless the members are registered, and
 * kotlinx.serialization then reports the class as simply not being @Serializable:
 *
 *     SerializationException: Serializer for class 'Main' is not found.
 *
 * The generated members are on disk after compilation, so scanning for them is complete by
 * construction -- no need to keep a hand-written list in step with the source.
 */
val generateNativeSerializerConfig by tasks.registering {
    group = "native"
    description = "Derives reflect-config.json entries for kotlinx.serialization from compiled classes"

    val classDirs = files(desktopMainCompilation.output.classesDirs)
    val runtimeClasspath = desktopRuntimeClasspath
    val outputFile = generatedMetadataDir.map { it.file("reflect-config.json") }

    inputs.files(classDirs).withPathSensitivity(PathSensitivity.RELATIVE)
    outputs.file(outputFile)

    doLast {
        // @Serializable generates different members depending on the declaration: data classes get
        // a `Companion` and a `$$serializer`, but objects get neither -- just a `serializer()`
        // method on the object itself. Matching on name patterns misses the objects, which is
        // exactly what navigation routes are, so inspect the actual members instead.
        val loader = URLClassLoader(
            runtimeClasspath.files.map { it.toURI().toURL() }.toTypedArray(),
            ClassLoader.getPlatformClassLoader()
        )

        fun isSerializable(cls: Class<*>) = runCatching {
            cls.declaredMethods.any { it.name == "serializer" } ||
                cls.declaredFields.any { it.name == "Companion" || it.name == "\$cachedSerializer\$delegate" }
        }.getOrDefault(false)

        val owners = sortedSetOf<String>()
        loader.use {
            classDirs.files.filter { it.isDirectory }.forEach { root ->
                root.walkTopDown().filter { f -> f.isFile && f.extension == "class" }.forEach { classFile ->
                    val binaryName = classFile.relativeTo(root).invariantSeparatorsPath
                        .removeSuffix(".class")
                        .replace('/', '.')
                    if (binaryName.endsWith("\$Companion") || binaryName.endsWith("\$\$serializer")) return@forEach
                    val cls = runCatching { Class.forName(binaryName, false, loader) }.getOrNull()
                    if (cls != null && isSerializable(cls)) owners += binaryName
                }
            }
        }

        val entries = owners.flatMap { owner ->
            listOf(owner, "$owner\$Companion", "$owner\$\$serializer")
        }.map { name ->
            """  { "name":"$name", "allDeclaredFields":true, "allDeclaredMethods":true, """ +
                """"allDeclaredConstructors":true }"""
        }

        outputFile.get().asFile.apply {
            parentFile.mkdirs()
            writeText(entries.joinToString(",\n", "[\n", "\n]\n"))
        }
        logger.lifecycle("[native] registered ${owners.size} serializable types for reflection")
    }
}

val generateNativeResourceConfig by tasks.registering {
    group = "native"
    description = "Derives resource-config.json by scanning the desktop runtime classpath"

    val classpath = desktopRuntimeClasspath
    val outputFile = generatedMetadataDir.map { it.file("resource-config.json") }

    inputs.files(classpath).withPathSensitivity(PathSensitivity.RELATIVE)
    outputs.file(outputFile)

    doLast {
        fun wanted(path: String) =
            path.startsWith("composeResources/") || path.startsWith("META-INF/services/")

        val patterns = sortedSetOf<String>()
        classpath.files.forEach { entry ->
            when {
                entry.isDirectory -> entry.walkTopDown()
                    .filter { it.isFile }
                    .map { it.relativeTo(entry).invariantSeparatorsPath }
                    .filter(::wanted)
                    .forEach { patterns += it }

                entry.isFile && entry.name.endsWith(".jar") ->
                    ZipFile(entry).use { jar ->
                        jar.entries().asSequence()
                            .filter { zipEntry -> !zipEntry.isDirectory && wanted(zipEntry.name) }
                            .forEach { zipEntry -> patterns += zipEntry.name }
                    }
            }
        }

        // \Q..\E makes native-image treat the path literally; the doubled backslashes are what
        // a JSON string needs in order to contain a single one.
        val json = patterns.joinToString(",\n") { """      { "pattern":"\\Q$it\\E" }""" }
        outputFile.get().asFile.apply {
            parentFile.mkdirs()
            writeText("{\n  \"resources\": {\n    \"includes\": [\n$json\n    ]\n  }\n}\n")
        }
        logger.lifecycle("[native] enumerated ${patterns.size} resources from the classpath")
    }
}

val isWindowsHost = System.getProperty("os.name").startsWith("Windows")

// ---------------------------------------------------------------------------------------------
// Executable icon and version metadata.
//
// compose.desktop's nativeDistributions.windows.iconFile only applies to the MSI it packages;
// a native-image binary gets nothing, so Explorer and the taskbar show the default executable
// icon and the file has no version information at all. On Windows both come from a compiled
// resource script linked into the binary.
// ---------------------------------------------------------------------------------------------

val appDisplayName = "Gemstone AI"
val windowsIcon = layout.projectDirectory.file("src/desktopMain/resources/simple_white.ico")

// Resolved eagerly: the configuration cache cannot serialise references back into the build
// script, so tasks may only capture plain values.
val windowsResourceCompiler: File? = run {
    val kits = File(System.getenv("ProgramFiles(x86)") ?: "C:/Program Files (x86)", "Windows Kits/10/bin")
    kits.listFiles()?.sortedByDescending { it.name }
        ?.map { File(it, "x64/rc.exe") }
        ?.firstOrNull { it.isFile }
}

val nativeResourceScript = layout.buildDirectory.file("native/app.rc")
val nativeResourceBinary = layout.buildDirectory.file("native/app.res")
val nativeManifestFile = layout.buildDirectory.file("native/app.manifest")

val compileNativeResources by tasks.registering {
    group = "native"
    description = "Compiles the executable icon and version information into a linkable resource"

    val iconFile = windowsIcon
    val rcFile = nativeResourceScript
    val resFile = nativeResourceBinary
    val compiler = windowsResourceCompiler
    val exeName = nativeExecutableName
    val displayName = appDisplayName
    val version = appVersion
    val manifestFile = nativeManifestFile

    onlyIf { compiler != null }

    inputs.file(iconFile)
    outputs.file(resFile)

    doLast {
        // Without an application manifest Windows treats the binary as DPI-unaware, renders it
        // at 96 DPI and bitmap-stretches the result, so the window looks low-resolution and
        // blurry on any scaled display. The JVM gets this from java.exe's own manifest; a native
        // image has none unless it is linked in here.
        manifestFile.get().asFile.apply {
            parentFile.mkdirs()
            writeText(
                """
                <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                <assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
                  <application xmlns="urn:schemas-microsoft-com:asm.v3">
                    <windowsSettings>
                      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
                      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">permonitorv2,permonitor,system</dpiAwareness>
                    </windowsSettings>
                  </application>
                  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
                    <application>
                      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
                      <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"/>
                    </application>
                  </compatibility>
                </assembly>
                """.trimIndent() + "\n"
            )
        }

        val versionQuad = (version.split(".") + listOf("0", "0", "0", "0")).take(4).joinToString(",")
        rcFile.get().asFile.apply {
            parentFile.mkdirs()
            writeText(
                """
                1 ICON "${iconFile.asFile.absolutePath.replace('\\', '/')}"

                1 VERSIONINFO
                FILEVERSION $versionQuad
                PRODUCTVERSION $versionQuad
                BEGIN
                  BLOCK "StringFileInfo"
                  BEGIN
                    BLOCK "040904b0"
                    BEGIN
                      VALUE "FileDescription", "$displayName"
                      VALUE "FileVersion", "$version"
                      VALUE "InternalName", "$exeName"
                      VALUE "OriginalFilename", "$exeName.exe"
                      VALUE "ProductName", "$displayName"
                      VALUE "ProductVersion", "$version"
                    END
                  END
                  BLOCK "VarFileInfo"
                  BEGIN
                    VALUE "Translation", 0x409, 1200
                  END
                END
                """.trimIndent() + "\n"
            )
        }

        val process = ProcessBuilder(
            compiler!!.absolutePath,
            "/nologo",
            "/fo", resFile.get().asFile.absolutePath,
            rcFile.get().asFile.absolutePath
        ).redirectErrorStream(true).start()
        val output = process.inputStream.bufferedReader().readText()
        check(process.waitFor() == 0) { "rc.exe failed:\n$output" }
        logger.lifecycle("[native] compiled executable icon and version info into ${resFile.get().asFile.name}")
    }
}

compose.desktop {
    application {
        mainClass = "gemstone.Main_desktopKt"

        // One executable through compose-multiplatform-extended (#191): `./gradlew :app:packageNativeImage`
        // writes build/compose/native-image/main/gemstone(.exe). It needs Liberica NIK 25 Full in
        // GRAALVM_HOME and skiko's static archive (core-extended's build-skiko-static-jvm.sh output)
        // in SKIKO_STATIC; docs/build/native-desktop.md has the steps.
        nativeImage {
            imageName.set(nativeExecutableName)
            metadataDirectory.set(nativeMetadataDir)
            providers.environmentVariable("SKIKO_STATIC").orNull?.let { skikoStaticDirectory.set(file(it)) }
            // Gemstone's manifest (DPI awareness, supported Windows versions) instead of the plugin's.
            if (isWindowsHost) windowsManifest.set(nativeManifestFile)
            buildArgs.addAll(
                // Declared rather than observed: a tracing agent picks up whatever locale the machine
                // that ran it happened to use.
                "-H:IncludeLocales=en,ko",
                "-H:ConfigurationFileDirectories=" +
                    (metadataBundleDirs.map { it.asFile } + generatedMetadataDir.get().asFile)
                        .joinToString(",") { it.absolutePath },
            )
            // link.exe takes the .res file as a plain input: the icon and version information end
            // up in the executable itself.
            if (isWindowsHost) buildArgs.add("-H:NativeLinkerOption=${nativeResourceBinary.get().asFile.absolutePath}")
        }

        nativeDistributions {
            targetFormats(TargetFormat.Dmg, TargetFormat.Msi, TargetFormat.Deb)
            packageName = "Gemstone AI"
            packageVersion = appVersion
            macOS {
                iconFile.set(project.file("src/desktopMain/resources/simple_white.icns"))
                installationPath = "/Applications/StoneManager"
                bundleID = "io.github.thisisthepy.gemstone"
            }
            windows {
                iconFile.set(project.file("src/desktopMain/resources/simple_white.ico"))
                dirChooser = true
                installationPath = "C:\\Program Files\\Gemstone"
                perUserInstall = true
            }
            linux {
                iconFile.set(project.file("src/desktopMain/resources/simple_white.png"))
                installationPath = "/usr/local/bin/gemstone"
            }
        }
    }
}

// The tracing agent: `./gradlew :app:runNativeImageAgent` (the plugin's) runs the app on GraalVM's JVM and
// merges what it sees into src/desktopMain/resources/META-INF/native-image (`metadataDirectory` above).

// The plugin's task builds from the metadata and resources Gemstone derives; generate them first.
tasks.matching { it.name == "packageNativeImage" }.configureEach {
    dependsOn(generateNativeSerializerConfig, generateNativeResourceConfig, compileNativeResources)
}

// Desktop tests save chats (a completed turn is saved, SPEC S2.6) under an app-data directory of their
// own, never in the developer's or the runner's profile.
tasks.withType<Test>().configureEach {
    if (name == "desktopTest") {
        val home = layout.buildDirectory.dir("test-home").get().asFile
        systemProperty("user.home", home.absolutePath)
        environment("XDG_DATA_HOME", home.resolve("data").absolutePath)
        environment("APPDATA", home.resolve("AppData").absolutePath)
    }
}
