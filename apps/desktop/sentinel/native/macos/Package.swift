// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "SentinelWorkspaceRuntime",
    platforms: [.macOS("26.0")],
    products: [
        .executable(name: "sentinel-workspace-runtime", targets: ["WorkspaceRuntime"]),
        .executable(name: "workspace-boot-probe", targets: ["WorkspaceBootProbe"]),
    ],
    dependencies: [.package(url: "https://github.com/apple/containerization.git", exact: "0.45.0")],
    targets: [.executableTarget(name: "WorkspaceRuntime", dependencies: [
        .product(name: "Containerization", package: "containerization"),
        .product(name: "ContainerizationOS", package: "containerization"),
    ]), .executableTarget(name: "WorkspaceBootProbe", dependencies: [
        .product(name: "Containerization", package: "containerization"),
    ], path: "Tests/Fixtures/WorkspaceBootProbe"),
    .testTarget(name: "WorkspaceRuntimeTests", dependencies: ["WorkspaceRuntime"])]
)
