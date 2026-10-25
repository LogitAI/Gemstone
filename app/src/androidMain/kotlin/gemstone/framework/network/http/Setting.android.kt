package gemstone.framework.network.http


internal actual fun setting(name: String): String? = System.getProperty(name) ?: System.getenv(name)
