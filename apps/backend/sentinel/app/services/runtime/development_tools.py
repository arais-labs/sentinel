"""Curated workspace tools. Desktop installation recipes are checked against this catalog."""

import platform

from app.services.runtime.environment import detect_runtime_environment, normalize_remote_os
from app.services.runtime.storage import machine_transport

TOOLS = [
    {
        "id": "kind",
        "name": "kind",
        "detail": "Upstream Kubernetes · private single-node cluster",
        "category": "Containers",
    },
    {
        "id": "k3s",
        "name": "K3s",
        "detail": "Lightweight Kubernetes via k3d · private single-node cluster",
        "category": "Containers",
    },
    {
        "id": "kubectl",
        "name": "kubectl",
        "detail": "Kubernetes command line tools",
        "category": "Containers",
    },
    {
        "id": "helm",
        "name": "Helm",
        "detail": "Kubernetes package manager",
        "category": "Containers",
    },
    {
        "id": "docker-builder",
        "name": "Docker Builder",
        "detail": "BuildKit builder, persistent cache and local image registry",
        "category": "Containers",
    },
    {
        "id": "desktop",
        "name": "Desktop",
        "detail": "Graphical desktop, terminal and Chromium · starts on demand",
        "category": "Desktop",
    },
    {"id": "git", "name": "Git", "detail": "Version control", "category": "Essentials"},
    {
        "id": "node",
        "name": "Node.js",
        "detail": "JavaScript runtime · includes npm",
        "category": "JavaScript",
    },
    {
        "id": "bun",
        "name": "Bun",
        "detail": "JavaScript runtime, bundler and package manager",
        "category": "JavaScript",
    },
    {
        "id": "deno",
        "name": "Deno",
        "detail": "JavaScript and TypeScript runtime",
        "category": "JavaScript",
    },
    {
        "id": "pnpm",
        "name": "pnpm",
        "detail": "Fast package manager · includes Node.js",
        "category": "JavaScript",
    },
    {
        "id": "yarn",
        "name": "Yarn",
        "detail": "Package manager · includes Node.js",
        "category": "JavaScript",
    },
    {
        "id": "typescript",
        "name": "TypeScript",
        "detail": "Type checker and compiler · includes Node.js",
        "category": "JavaScript",
    },
    {
        "id": "python",
        "name": "Python",
        "detail": "Python runtime · includes pip and virtual environments",
        "category": "Python",
    },
    {
        "id": "uv",
        "name": "uv",
        "detail": "Python package and environment manager",
        "category": "Python",
    },
    {
        "id": "poetry",
        "name": "Poetry",
        "detail": "Python dependency management and packaging",
        "category": "Python",
    },
    {
        "id": "go",
        "name": "Go",
        "detail": "Compiler and Go tools",
        "category": "Languages",
    },
    {
        "id": "rust",
        "name": "Rust",
        "detail": "Compiler and Cargo",
        "category": "Languages",
    },
    {
        "id": "php",
        "name": "PHP",
        "detail": "PHP command line runtime",
        "category": "Languages",
    },
    {
        "id": "composer",
        "name": "Composer",
        "detail": "PHP dependency manager · includes PHP",
        "category": "Languages",
    },
    {
        "id": "ruby",
        "name": "Ruby",
        "detail": "Ruby runtime · includes Bundler and build tools",
        "category": "Languages",
    },
    {
        "id": "java",
        "name": "Java",
        "detail": "OpenJDK 21 development kit",
        "category": "Languages",
    },
    {
        "id": "maven",
        "name": "Maven",
        "detail": "Java build and dependency management",
        "category": "Build tools",
    },
    {
        "id": "gradle",
        "name": "Gradle",
        "detail": "Java and JVM build automation",
        "category": "Build tools",
    },
    {
        "id": "dotnet",
        "name": ".NET",
        "detail": "SDK for C#, F# and ASP.NET",
        "category": "Languages",
    },
    {
        "id": "cpp",
        "name": "C / C++",
        "detail": "GCC, G++ and Make",
        "category": "Build tools",
    },
    {
        "id": "clang",
        "name": "Clang",
        "detail": "LLVM C and C++ compiler",
        "category": "Build tools",
    },
    {
        "id": "cmake",
        "name": "CMake",
        "detail": "Cross-platform build configuration",
        "category": "Build tools",
    },
    {
        "id": "ninja",
        "name": "Ninja",
        "detail": "Build runner",
        "category": "Build tools",
    },
    {
        "id": "chromium",
        "name": "Chromium",
        "detail": "Headless browser for automation and testing",
        "category": "Browser",
    },
    {
        "id": "postgres",
        "name": "PostgreSQL",
        "detail": "Private database · localhost:5432",
        "category": "Services",
    },
    {
        "id": "mysql",
        "name": "MySQL",
        "detail": "Private database · localhost:3306",
        "category": "Services",
    },
    {
        "id": "redis",
        "name": "Redis",
        "detail": "Private persistent cache · localhost:6379",
        "category": "Services",
    },
]
TOOL_IDS = frozenset(tool["id"] for tool in TOOLS)
STACKS = [
    {
        "id": "kind",
        "name": "Kubernetes · kind",
        "description": "Upstream Kubernetes for development & testing",
        "tools": ["kind", "kubectl", "helm", "docker-builder"],
    },
    {
        "id": "k3s",
        "name": "Kubernetes · K3s",
        "description": "Lightweight Kubernetes, managed with k3d",
        "tools": ["k3s", "kubectl", "helm", "docker-builder"],
    },
    {
        "id": "docker-builder",
        "name": "Docker Builder",
        "description": "Build, cache & publish container images",
        "tools": ["docker-builder"],
    },
    {
        "id": "desktop",
        "name": "Desktop",
        "description": "Graphical apps & interactive browsing",
        "tools": ["desktop"],
    },
    {
        "id": "node",
        "name": "Node.js",
        "description": "Web apps & JavaScript",
        "tools": ["git", "node", "pnpm", "typescript"],
    },
    {
        "id": "bun",
        "name": "Bun",
        "description": "Fast JavaScript development",
        "tools": ["git", "bun"],
    },
    {
        "id": "python",
        "name": "Python",
        "description": "Scripts, APIs & data",
        "tools": ["git", "python", "uv"],
    },
    {
        "id": "go",
        "name": "Go",
        "description": "Services & command-line tools",
        "tools": ["git", "go"],
    },
    {
        "id": "rust",
        "name": "Rust",
        "description": "Systems & native applications",
        "tools": ["git", "rust"],
    },
    {
        "id": "php",
        "name": "PHP",
        "description": "Web apps & APIs",
        "tools": ["git", "php", "composer"],
    },
    {
        "id": "ruby",
        "name": "Ruby",
        "description": "Web apps & scripting",
        "tools": ["git", "ruby"],
    },
    {
        "id": "java",
        "name": "Java",
        "description": "JVM applications & services",
        "tools": ["git", "java", "maven"],
    },
    {
        "id": "dotnet",
        "name": ".NET",
        "description": "C#, F# & web services",
        "tools": ["git", "dotnet"],
    },
    {
        "id": "cpp",
        "name": "C / C++",
        "description": "Native applications & libraries",
        "tools": ["git", "cpp", "cmake", "ninja"],
    },
]


async def machine_os(machine) -> str:
    if machine.provider == "local":

        return normalize_remote_os(platform.system())

    transport = machine_transport(machine)
    try:
        return (await detect_runtime_environment(transport)).os
    finally:
        await transport.close()
