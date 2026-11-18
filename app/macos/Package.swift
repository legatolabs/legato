// swift-tools-version:5.10
// The Legato Mac app: a menu bar shell around the Python engine (`legato app-engine`). Build with ./build.sh.
import PackageDescription

let package = Package(
    name: "Legato",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "Legato",
            path: "Sources/Legato",
            linkerSettings: [.linkedFramework("Carbon"), .linkedFramework("ScreenCaptureKit")]
        ),
    ]
)
