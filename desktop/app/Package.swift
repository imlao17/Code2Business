// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "C2BDesktop",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "C2BDesktop",
            path: "Sources/C2BDesktop"
        ),
        .testTarget(
            name: "C2BDesktopTests",
            dependencies: ["C2BDesktop"],
            path: "Tests/C2BDesktopTests"
        ),
    ]
)
