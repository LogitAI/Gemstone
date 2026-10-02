import java.net.URLClassLoader
import java.util.zip.ZipFile
import org.gradle.process.CommandLineArgumentProvider
import org.jetbrains.kotlin.gradle.targets.js.webpack.KotlinWebpackConfig
import org.jetbrains.compose.desktop.application.dsl.TargetFormat
import org.jetbrains.kotlin.gradle.ExperimentalWasmDsl
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.kotlin.multiplatform)
    alias(libs.plugins.android.application)
    alias(libs.plugins.compose.multiplatform)
    alias(libs.plugins.compose.compiler)
    alias(libs.plugins.compose.hotreload)
    kotlin("plugin.serialization").version(libs.versions.kotlin.get())
}


kotlin {
    androidTarget {
        compilerOptions {
            jvmTarget.set(JvmTarget.JVM_11)
        }
    }

    listOf(
        iosX64(),
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

    sourceSets {
        val commonMain by getting
        val androidMain by getting
        val desktopMain by getting
        val cioMain by creating {
            dependencies {
                api(libs.ktor.client.cio)
            }
            androidMain.dependsOn(this)
            desktopMain.dependsOn(this)
            iosMain {
                dependsOn(this)
            }
            dependsOn(commonMain)
        }
        wasmJsMain.dependencies {
            api(libs.ktor.client.js)
        }

        androidMain.dependencies {
            api(compose.material3)
            api(compose.preview)
            api(libs.androidx.activity.compose)
        }
        commonMain.dependencies {
            api(compose.runtime)
            api(compose.foundation)
            api(compose.material3)
            api(compose.materialIconsExtended)
            api(compose.ui)
            api(compose.components.resources)
            api(compose.components.uiToolingPreview)
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
        desktopMain.dependencies {
            api(compose.desktop.currentOs) {
                exclude(group = "org.jetbrains.compose.material3")
            }
            api(libs.kotlinx.coroutines.swing)

            implementation(libs.jewel.standalone)
            implementation(libs.jewel.decorated.window)
            implementation(libs.jewel.foundation)

            // Jewel's standalone cursor handling loads JNA at class-init, but Jewel does not
            // declare it -- inside the IDE the platform supplies it. Without these the desktop
            // app dies on first composition with NoClassDefFoundError: com/sun/jna/Library.
            runtimeOnly(libs.jna)
            runtimeOnly(libs.jna.platform)
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
        versionCode = 2
        versionName = "1.0.1"
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
    debugImplementation(compose.uiTooling)
}

// ---------------------------------------------------------------------------------------------
// GraalVM native-image for the desktop target.
//
// native-build-tools binds itself to the `main` source set of a plain JVM project, which a
// Kotlin Multiplatform project does not have, so the binary is pointed at the desktop
// compilation's own output and runtime classpath instead.
// ---------------------------------------------------------------------------------------------

val nativeExecutableName = "gemstone"
val nativeImageJdk = "22"
val nativeMainClass = "gemstone.Main_desktopKt"

val desktopMainCompilation = kotlin.targets.getByName("desktop").compilations.getByName("main")
val desktopRuntimeClasspath = files(
    desktopMainCompilation.output.allOutputs,
    desktopMainCompilation.runtimeDependencyFiles
)

// AWT resolves its native libraries as <java.home>/bin/<name> and java.home is null in a native
// image, so the executable is built straight into a `bin` directory: that makes its parent a
// valid java.home, which NativeRuntime.kt derives at startup. Building anywhere else produces
// an executable that hard-crashes on launch with no Java stack trace.
val nativeImageRoot = layout.buildDirectory.dir("native/nativeCompile")
val nativeBinDir = layout.buildDirectory.dir("native/nativeCompile/bin")
val nativeMetadataDir = layout.projectDirectory.dir("src/desktopMain/resources/META-INF/native-image")
val agentOutputDir = layout.buildDirectory.dir("native/agent-output")
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

val graalHome = javaToolchains.launcherFor {
    languageVersion.set(JavaLanguageVersion.of(nativeImageJdk))
}.map { it.metadata.installationPath.asFile.absolutePath }

val isWindowsHost = System.getProperty("os.name").startsWith("Windows")
val nativeImageTool = if (isWindowsHost) "bin/native-image.cmd" else "bin/native-image"

// ---------------------------------------------------------------------------------------------
// Executable icon and version metadata.
//
// compose.desktop's nativeDistributions.windows.iconFile only applies to the MSI it packages;
// a native-image binary gets nothing, so Explorer and the taskbar show the default executable
// icon and the file has no version information at all. On Windows both come from a compiled
// resource script linked into the binary.
// ---------------------------------------------------------------------------------------------

val appDisplayName = "Gemstone AI"
val appVersion = "1.0.1"
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

                1 24 "${manifestFile.get().asFile.absolutePath.replace('\\', '/')}"

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

val nativeCompile by tasks.registering(Exec::class) {
    group = "native"
    description = "Builds a native executable of the desktop app with GraalVM native-image"

    dependsOn(
        desktopMainCompilation.compileTaskProvider,
        "desktopProcessResources",
        generateNativeResourceConfig,
        generateNativeSerializerConfig,
        compileNativeResources
    )
    inputs.files(desktopRuntimeClasspath).withPathSensitivity(PathSensitivity.RELATIVE)
    // The bundle has to be an input too: editing it and getting an UP-TO-DATE build silently
    // produces a binary that ignores the change, which is very hard to tell apart from the fix
    // simply not working.
    inputs.files(metadataBundleDirs.map { it.asFile }).withPathSensitivity(PathSensitivity.RELATIVE)
    inputs.dir(nativeMetadataDir).optional()
    inputs.dir(generatedMetadataDir)
    outputs.dir(nativeImageRoot)

    executable = "${graalHome.get()}/$nativeImageTool"

    val binDir = nativeBinDir
    val metadataDir = nativeMetadataDir
    val classpath = desktopRuntimeClasspath
    val imageName = nativeExecutableName
    val mainClass = nativeMainClass
    val argFile = layout.buildDirectory.file("native/native-image.args")
    val generatedDir = generatedMetadataDir
    val resourceBinary = nativeResourceBinary
    val bundleDirs = metadataBundleDirs

    // A real application's classpath blows straight past the 8191 character Windows command
    // line limit, so the arguments go in a file. native-image reads backslashes in an argument
    // file as escapes, hence the forward slashes -- Windows accepts them in paths.
    doFirst {
        binDir.get().asFile.mkdirs()
        val args = buildList {
            add("-cp"); add(classpath.asPath)
            // native-image defaults java.awt.headless to true.
            add("-Djava.awt.headless=false")
            // Without this, native-image quietly emits a fallback image -- a launcher that
            // shells out to a JVM -- instead of failing on missing metadata.
            add("--no-fallback")
            // Declared rather than observed: a tracing agent picks up whatever locale the machine
            // that ran it happened to use, so the same source tree would otherwise produce
            // different images for different developers.
            add("-H:IncludeLocales=en,ko")
            val configDirs = (bundleDirs.map { it.asFile } + metadataDir.asFile + generatedDir.get().asFile)
                .filter { it.isDirectory }
            if (configDirs.isNotEmpty()) {
                add("-H:ConfigurationFileDirectories=${configDirs.joinToString(",") { it.absolutePath }}")
            }
            // link.exe takes a .res file as a plain input, so the icon and version information
            // end up embedded in the executable itself.
            resourceBinary.get().asFile.takeIf { it.isFile }?.let {
                add("-H:NativeLinkerOption=${it.absolutePath}")
            }
            add("-o"); add(binDir.get().asFile.resolve(imageName).absolutePath)
            add(mainClass)
        }
        argFile.get().asFile.apply {
            parentFile.mkdirs()
            writeText(args.joinToString("\n") { "\"${it.replace('\\', '/')}\"" })
        }
    }

    argumentProviders.add(CommandLineArgumentProvider {
        listOf("@${argFile.get().asFile.absolutePath}")
    })

    // A native-image output directory missing Skia and the AWT font configuration is an
    // executable that exists and crashes on launch. Stage them here and not only in nativeDist,
    // because the compiler's own output directory is the first thing anyone runs.
    val graalLibDir = graalHome.map { "$it/lib" }
    doLast {
        val bin = binDir.get().asFile
        val lib = bin.parentFile.resolve("lib").apply { mkdirs() }

        val skikoJar = classpath.files.single { it.name.startsWith("skiko-awt-runtime-") }
        ZipFile(skikoJar).use { jar ->
            jar.entries().asSequence()
                .filter { e ->
                    !e.isDirectory && (e.name == "icudtl.dat" ||
                        e.name.endsWith(".dll") || e.name.endsWith(".so") || e.name.endsWith(".dylib"))
                }
                .forEach { e ->
                    jar.getInputStream(e).use { input ->
                        bin.resolve(File(e.name).name).outputStream().use { input.copyTo(it) }
                    }
                }
        }

        listOf("fontconfig.bfc", "fontconfig.properties.src", "psfont.properties.ja", "psfontj2d.properties")
            .map { File(graalLibDir.get(), it) }
            .filter { it.isFile }
            .forEach { it.copyTo(lib.resolve(it.name), overwrite = true) }
    }
}

/**
 * A clean copy of the runnable layout, without the compiler's build reports. nativeCompile
 * already produces a complete `bin` + `lib` tree, so this only strips what is not needed to run.
 */
val nativeDist by tasks.registering(Sync::class) {
    group = "native"
    description = "Copies the runnable native image layout into build/dist"
    dependsOn(nativeCompile)

    from(nativeImageRoot) {
        exclude("*.txt", "*.args", "reports/**")
    }
    into(layout.buildDirectory.dir("dist"))
}

val metadataCopy by tasks.registering(Copy::class) {
    group = "native"
    description = "Copies tracing agent output into src/desktopMain/resources/META-INF/native-image"
    from(agentOutputDir)
    into(nativeMetadataDir)
}

compose.desktop {
    application {
        mainClass = "gemstone.Main_desktopKt"

        nativeDistributions {
            targetFormats(TargetFormat.Dmg, TargetFormat.Msi, TargetFormat.Deb)
            packageName = "Gemstone AI"
            packageVersion = "1.0.1"
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

// `-Pagent` attaches the GraalVM tracing agent to `run`, so that exercising the app records the
// reflection, JNI and resource access a native image cannot infer statically. This goes through
// Compose's own jvmArgs because the plugin configures the run task after any `withType<JavaExec>`
// hook and would overwrite arguments added there.
if (project.hasProperty("agent")) {
    val agentDir = agentOutputDir.get().asFile
    agentDir.mkdirs()
    compose.desktop.application.jvmArgs += "-agentlib:native-image-agent=config-output-dir=${agentDir.absolutePath}"
}
