package gemstone

import java.io.File

/**
 * Startup configuration that only matters when this app is running as a GraalVM native image.
 * On a normal JVM every property below is already set and nothing here does anything.
 *
 * Two things the JVM provides for free are missing from a native image:
 *
 *  - `java.home` is null. AWT resolves its native libraries as `<java.home>/bin/<name>`, so
 *    without it JAWT loading resolves to `(null)/bin/jawt.dll` and the process dies with a hard
 *    crash -- no Java stack trace, and nothing at build time to warn you. native-image emits the
 *    JDK libraries AWT needs right next to the executable, so as long as the executable lives in
 *    a `bin` directory its parent is already a valid java.home.
 *
 *  - Skiko normally unpacks Skia out of its runtime jar into `~/.skiko/<hash>/`. There is no jar
 *    to unpack from here, so it can only find a copy some earlier JVM run left behind: the app
 *    renders on the machine that built it and has no renderer anywhere else. The `nativeDist`
 *    task stages the library and its ICU data alongside the executable instead.
 *
 * Must run before anything touches AWT or Compose.
 */
fun configureNativeImageRuntime() {
    if (System.getProperty("java.home") != null) return

    val executable = ProcessHandle.current().info().command().orElse(null) ?: return
    val binDir = File(executable).parentFile ?: return
    val appRoot = binDir.parentFile ?: return

    if (!binDir.name.equals("bin", ignoreCase = true)) {
        System.err.println(
            "This executable expects to run from a 'bin' directory next to the native libraries " +
                "it needs, but it is in ${binDir.absolutePath}. AWT will fail to load. Run the " +
                "nativeDist Gradle task and launch the binary it stages under build/dist/bin."
        )
    }

    System.setProperty("java.home", appRoot.absolutePath)

    if (System.getProperty("skiko.library.path") == null) {
        System.setProperty("skiko.library.path", binDir.absolutePath)
    }
    if (System.getProperty("skiko.data.path") == null) {
        System.setProperty("skiko.data.path", binDir.absolutePath)
    }
}

/**
 * Traces AWT input events to stderr when GEMSTONE_TRACE_INPUT is set.
 *
 * A native image that renders correctly but ignores every click gives no clue as to where the
 * events are lost. Listening at the AWT level splits the question in two: if events appear here
 * the break is between AWT and Compose, and if they do not it is between Windows and AWT.
 */
fun traceInputEventsIfRequested() {
    if (System.getenv("GEMSTONE_TRACE_INPUT") == null) return

    val mask = java.awt.AWTEvent.MOUSE_EVENT_MASK or
        java.awt.AWTEvent.MOUSE_MOTION_EVENT_MASK or
        java.awt.AWTEvent.KEY_EVENT_MASK or
        java.awt.AWTEvent.MOUSE_WHEEL_EVENT_MASK
    var probed = false
    java.awt.Toolkit.getDefaultToolkit().addAWTEventListener({ event ->
        val where = (event as? java.awt.event.MouseEvent)?.let { " at (${it.x},${it.y})" } ?: ""
        val source = event.source as? java.awt.Component
        System.err.println("[input] ${event.javaClass.name} id=${event.id}$where source=${source?.javaClass?.name}")

        // Attach our own listener to the same component once. If ours fires, AWT really is
        // dispatching to listeners and the break is inside Compose's own handling; if it never
        // fires, dispatch itself is not happening despite the events being posted.
        if (!probed && source != null) {
            probed = true
            System.err.println("[input] existing listeners: " + source.mouseListeners.joinToString { it.javaClass.name })
            System.err.println("[input] component: showing=${source.isShowing} enabled=${source.isEnabled} size=${source.size}")
            source.addMouseListener(object : java.awt.event.MouseAdapter() {
                override fun mousePressed(e: java.awt.event.MouseEvent) {
                    System.err.println("[input] >>> our own listener fired: pressed at (${e.x},${e.y})")
                }
            })
        }
    }, mask)
}
