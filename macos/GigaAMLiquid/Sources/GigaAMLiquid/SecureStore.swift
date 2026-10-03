import Foundation
import Security

enum SecureStore {
    private static let service = "ru.dubr1k.gigaam-liquid"

    struct Failure: LocalizedError {
        let status: OSStatus
        var errorDescription: String? {
            SecCopyErrorMessageString(status, nil) as String? ?? "Keychain error \(status)"
        }
    }

    /// The stored value, or nil when nothing is stored. A real Keychain failure
    /// (locked or denied keychain, an unreadable item) throws: reading it as "no
    /// token" let a job run without the token and blame the model licence.
    static func string(for account: String) throws -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        return try value(status: status, item: item)
    }

    /// The lookup's outcome: not found is "no value", anything else but success is an error.
    static func value(status: OSStatus, item: CFTypeRef?) throws -> String? {
        switch status {
        case errSecItemNotFound:
            return nil
        case errSecSuccess:
            guard let data = item as? Data, let text = String(data: data, encoding: .utf8) else {
                throw Failure(status: errSecDecode)
            }
            return text
        default:
            throw Failure(status: status)
        }
    }

    static func set(_ value: String, for account: String) throws {
        let key: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        if value.isEmpty {
            let status = SecItemDelete(key as CFDictionary)
            if status != errSecSuccess && status != errSecItemNotFound { throw Failure(status: status) }
            return
        }
        let attributes: [String: Any] = [kSecValueData as String: Data(value.utf8)]
        let update = SecItemUpdate(key as CFDictionary, attributes as CFDictionary)
        if update == errSecSuccess { return }
        guard update == errSecItemNotFound else { throw Failure(status: update) }
        var create = key
        create.merge(attributes) { _, new in new }
        let add = SecItemAdd(create as CFDictionary, nil)
        if add != errSecSuccess { throw Failure(status: add) }
    }
}
