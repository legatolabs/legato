import Foundation
import Sparkle

/// Automatic updates through Sparkle, in release builds only: release.sh writes the update feed (SUFeedURL) and the
/// public key the updates are signed with (SUPublicEDKey) into Info.plist; build.sh leaves both empty, so a build made
/// on this Mac never updates itself.
final class Updater {
    private var controller: SPUStandardUpdaterController?

    var enabled: Bool { controller != nil }

    init() {
        let feed = Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") as? String ?? ""
        let key = Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey") as? String ?? ""
        guard !feed.isEmpty, !key.isEmpty else { return }
        controller = SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: nil, userDriverDelegate: nil)
    }

    func check() { controller?.checkForUpdates(nil) }
}
