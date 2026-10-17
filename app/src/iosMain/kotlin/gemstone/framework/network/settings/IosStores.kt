package gemstone.framework.network.settings

import gemstone.framework.network.SecretStore
import gemstone.framework.network.ServerSettings.Companion.KEY_ACCOUNT
import gemstone.framework.network.ServerSettings.Companion.KEY_SERVICE
import gemstone.framework.network.SettingsStore
import kotlinx.cinterop.BetaInteropApi
import kotlinx.cinterop.CPointer
import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.alloc
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.ptr
import kotlinx.cinterop.value
import platform.CoreFoundation.CFDictionaryAddValue
import platform.CoreFoundation.CFDictionaryCreateMutable
import platform.CoreFoundation.CFDictionaryRef
import platform.CoreFoundation.CFRelease
import platform.CoreFoundation.CFTypeRefVar
import platform.CoreFoundation.kCFBooleanTrue
import platform.CoreFoundation.kCFTypeDictionaryKeyCallBacks
import platform.CoreFoundation.kCFTypeDictionaryValueCallBacks
import platform.Foundation.CFBridgingRelease
import platform.Foundation.CFBridgingRetain
import platform.Foundation.NSData
import platform.Foundation.NSString
import platform.Foundation.NSUTF8StringEncoding
import platform.Foundation.NSUserDefaults
import platform.Foundation.create
import platform.Foundation.dataUsingEncoding
import platform.Security.SecItemAdd
import platform.Security.SecItemCopyMatching
import platform.Security.SecItemDelete
import platform.Security.errSecSuccess
import platform.Security.kSecAttrAccessible
import platform.Security.kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
import platform.Security.kSecAttrAccount
import platform.Security.kSecAttrService
import platform.Security.kSecClass
import platform.Security.kSecClassGenericPassword
import platform.Security.kSecMatchLimit
import platform.Security.kSecMatchLimitOne
import platform.Security.kSecReturnData
import platform.Security.kSecValueData


/** The server address in `NSUserDefaults`. */
class UserDefaultsStore : SettingsStore {
    private val defaults = NSUserDefaults.standardUserDefaults

    override fun get(key: String): String? = defaults.stringForKey(key)

    override fun set(key: String, value: String?) {
        if (value == null) defaults.removeObjectForKey(key) else defaults.setObject(value, key)
    }
}


/** The API key in the Keychain: a generic password, readable after first unlock, on this device only. */
@OptIn(ExperimentalForeignApi::class, BetaInteropApi::class)
class KeychainSecretStore : SecretStore {
    override val persistent: Boolean = true

    /** Runs [block] with a query for the key's item plus [extra] attributes. */
    private fun <T> withQuery(vararg extra: Pair<CPointer<*>?, CPointer<*>?>, block: (CFDictionaryRef?) -> T): T {
        val service = CFBridgingRetain(KEY_SERVICE)
        val account = CFBridgingRetain(KEY_ACCOUNT)
        val query = CFDictionaryCreateMutable(null, 0, kCFTypeDictionaryKeyCallBacks.ptr, kCFTypeDictionaryValueCallBacks.ptr)
        try {
            CFDictionaryAddValue(query, kSecClass, kSecClassGenericPassword)
            CFDictionaryAddValue(query, kSecAttrService, service)
            CFDictionaryAddValue(query, kSecAttrAccount, account)
            for ((key, value) in extra) CFDictionaryAddValue(query, key, value)
            return block(query)
        } finally {
            CFRelease(query)
            CFRelease(service)
            CFRelease(account)
        }
    }

    override fun get(): String? = withQuery(kSecReturnData to kCFBooleanTrue, kSecMatchLimit to kSecMatchLimitOne) { query ->
        memScoped {
            val result = alloc<CFTypeRefVar>()
            if (SecItemCopyMatching(query, result.ptr) != errSecSuccess) return@memScoped null
            val data = CFBridgingRelease(result.value) as? NSData ?: return@memScoped null
            NSString.create(data = data, encoding = NSUTF8StringEncoding)?.toString()
        }
    }

    override fun set(value: String?) {
        withQuery { SecItemDelete(it) }  // nothing to delete is fine
        if (value == null) return
        @Suppress("CAST_NEVER_SUCCEEDS")
        val data = CFBridgingRetain((value as NSString).dataUsingEncoding(NSUTF8StringEncoding))
        try {
            withQuery(kSecValueData to data, kSecAttrAccessible to kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly) {
                val status = SecItemAdd(it, null)
                check(status == errSecSuccess) { "Keychain error $status" }
            }
        } finally {
            CFRelease(data)
        }
    }
}
