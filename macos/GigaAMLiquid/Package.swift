// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "GigaAMLiquid",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "GigaAMLiquid", targets: ["GigaAMLiquid"])],
    targets: [.executableTarget(name: "GigaAMLiquid")]
)
