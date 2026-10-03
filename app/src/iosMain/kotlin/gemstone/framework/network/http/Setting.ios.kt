package gemstone.framework.network.http

import platform.Foundation.NSProcessInfo


internal actual fun setting(name: String): String? =
    NSProcessInfo.processInfo.environment[name] as? String
