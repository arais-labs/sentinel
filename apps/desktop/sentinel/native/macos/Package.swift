// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "SentinelWorkspaceRuntime",
    platforms: [.macOS("26.0")],
    products: [.executable(name: "sentinel-workspace-runtime", targets: ["WorkspaceRuntime"])],
    dependencies: [.package(url: "https://github.com/apple/containerization.git", exact: "0.45.0")],
    targets: [.executableTarget(name: "WorkspaceRuntime", dependencies: [
        .product(name: "Containerization", package: "containerization"),
    ]), .testTarget(name: "WorkspaceRuntimeTests", dependencies: ["WorkspaceRuntime"])]
)
