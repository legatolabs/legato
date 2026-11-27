// swift-tools-version:5.10
// The Legato Mac app: a menu bar shell around the Python engine (`legato app-engine`). Build with ./build.sh (this Mac)
// or ./release.sh (a signed, notarized .dmg for other Macs). Sparkle checks for and installs updates.
import PackageDescription

let package = Package(
    name: "Legato",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.1"),
    ],
    targets: [
        .executableTarget(
            name: "Legato",
            dependencies: [.product(name: "Sparkle", package: "Sparkle")],
            path: "Sources/Legato",
            linkerSettings: [
                .linkedFramework("Carbon"), .linkedFramework("ScreenCaptureKit"),
                // Sparkle.framework is copied into Legato.app/Contents/Frameworks by build.sh and release.sh
                .unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"]),
            ]
        ),
        .testTarget(name: "LegatoTests", dependencies: ["Legato"]),
    ]
)
