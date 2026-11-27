import Foundation
import Security
import XCTest
@testable import Legato

final class SettingsTests: XCTestCase {
    func testPartialLegacyReadCannotReplaceOtherKeys() throws {
        let service = "legato.test." + UUID().uuidString
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service]
        defer { SecItemDelete(query as CFDictionary) }

        func item(_ provider: String) -> [String: Any] {
            var q = query
            q[kSecAttrAccount as String] = provider
            return q
        }
        for (provider, bytes) in [("openai", Data("first".utf8)), ("gemini", Data([0xff]))] {
            var q = item(provider)
            q[kSecValueData as String] = bytes
            XCTAssertEqual(SecItemAdd(q as CFDictionary, nil), errSecSuccess)
        }

        let keys = Keychain(service: service)
        XCTAssertEqual(keys.read(account: "openai"), "first")
        XCTAssertFalse(keys.write("replacement", account: "openai"))
        XCTAssertFalse(keys.delete(account: "openai"))
        XCTAssertEqual(SecItemCopyMatching(item("keys") as CFDictionary, nil), errSecItemNotFound)

        let repaired = [kSecValueData as String: Data("second".utf8)]
        XCTAssertEqual(SecItemUpdate(item("gemini") as CFDictionary, repaired as CFDictionary), errSecSuccess)
        let reopened = Keychain(service: service)
        XCTAssertEqual(reopened.read(account: "openai"), "first")
        XCTAssertEqual(reopened.read(account: "gemini"), "second")
        XCTAssertTrue(reopened.write("updated", account: "openai"))
        XCTAssertEqual(Keychain(service: service).read(account: "gemini"), "second")
    }
}
