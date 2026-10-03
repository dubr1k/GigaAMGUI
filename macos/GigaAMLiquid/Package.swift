// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "GigaAMLiquid",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "GigaAMLiquid", targets: ["GigaAMLiquid"])],
    targets: [
        // UI-free logic lives here so `swift test` can cover it without AppKit.
        .target(name: "GigaAMLiquidCore"),
        .executableTarget(name: "GigaAMLiquid", dependencies: ["GigaAMLiquidCore"]),
        .testTarget(name: "GigaAMLiquidCoreTests", dependencies: ["GigaAMLiquidCore"]),
        // Worker jobs end to end against a scripted /bin/sh stand-in for the Python worker.
        .testTarget(name: "GigaAMLiquidTests", dependencies: ["GigaAMLiquid"])
    ]
)
