import Foundation
import Security
import Testing
@testable import GigaAMLiquid

/// Every Keychain error used to read as "no token": a locked keychain made a
/// diarization job run without the token and fail on the model licence instead.
@Suite struct SecureStoreTests {
    @Test func onlyAMissingItemMeansNoValue() throws {
        #expect(try SecureStore.value(status: errSecItemNotFound, item: nil) == nil)
        #expect(try SecureStore.value(status: errSecSuccess, item: Data("hf_123".utf8) as CFData) == "hf_123")
    }

    @Test func realKeychainFailuresAreErrors() {
        for status in [errSecInteractionNotAllowed, errSecAuthFailed, errSecNotAvailable] {
            #expect(throws: SecureStore.Failure.self) { try SecureStore.value(status: status, item: nil) }
        }
        #expect(throws: SecureStore.Failure.self) { try SecureStore.value(status: errSecSuccess, item: nil) }
    }
}
