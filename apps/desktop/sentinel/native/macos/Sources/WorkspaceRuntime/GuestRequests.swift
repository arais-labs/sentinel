import Containerization
import Foundation

extension WorkspaceRuntime {
    // Match guest graphics_environment.py. Exec/PTY bypass PAM and shell profiles.
    static let guestGPUEnvironment = ["VK_LOADER_DRIVERS_SELECT=*virtio*"]
    /// Creation, exec and initial PTY setup share one startup budget. Cleanup
    /// stays with the operation so a late reply cannot publish an orphan process.
    @MainActor private static func startGuestProcess(_ id: String, request: Request,
        create: @escaping @MainActor () async throws -> LinuxProcess) async throws -> LinuxProcess {
        let generation = WorkspaceHealth.requestGeneration ?? health.generation(id)
        return try await health.processStartup(id) {
            let process = try await create()
            do {
                try Task.checkCancellation()
                guard generation == health.generation(id) else { throw CancellationError() }
                try await process.start()
                try Task.checkCancellation()
                if request.action == "process_start", request.terminal == true {
                    try await process.resize(to: .init(width: request.cols ?? 80, height: request.rows ?? 24))
                }
                try Task.checkCancellation()
                guard generation == health.generation(id) else { throw CancellationError() }
                return process
            } catch {
                try? await process.kill(.kill)
                try? await process.delete()
                throw error
            }
        }
    }

    @MainActor static func guestRequest(_ request: Request, container: LinuxContainer?, processes: Processes) async {
        guard let id = request.workspace else { return }
        do {
            try health.check(id)
            switch request.action {
                case "process_input", "process_resize", "process_stop":
                    guard let pid = request.process else { throw RuntimeError("Process ID is required") }
                    let (process, input) = try await processes.get(pid, workspace: id)
                    if request.action == "process_input" {
                        if let encoded = request.data {
                            guard let data = Data(base64Encoded: encoded), data.count <= 1024 * 1024 else {
                                throw RuntimeError("Invalid process input")
                            }
                            input.pair.continuation.yield(data)
                        } else { input.pair.continuation.finish() }
                    } else if request.action == "process_resize" {
                        try await health.guest(id) { try await process.resize(to: .init(width: request.cols ?? 80, height: request.rows ?? 24)) }
                    } else { try await health.guest(id) { try await process.kill(.kill) } }
                    try emit(Response(id: request.id))
                case "process_start":
                    guard let container, let command = request.arguments, !command.isEmpty,
                          let pid = request.process, UUID(uuidString: pid) != nil else {
                        throw RuntimeError("A running workspace, process UUID and arguments are required")
                    }
                    let input = Input(), output = Output(pid)
                    let process = try await startGuestProcess(id, request: request) { try await container.exec(pid) { config in
                        config.arguments = command
                        config.capabilities = .allCapabilities
                        config.workingDirectory = container.config.process.workingDirectory
                        config.environmentVariables = ["PATH=/root/.local/bin:\(LinuxProcessConfiguration.defaultPath)", "HOME=/root", "TMPDIR=/tmp", "TERM=xterm-256color"] + guestGPUEnvironment
                        config.terminal = request.terminal ?? false
                        config.stdin = input
                        config.stdout = output
                        // A PTY merges stderr into its output stream. The SDK
                        // rejects a separate stderr writer for terminal execs.
                        if request.terminal != true { config.stderr = output }
                    } }
                    await processes.add(pid, workspace: id, process: process, input: input)
                    do {
                        try emit(Response(id: request.id, process: pid))
                    } catch {
                        Task { try? await process.kill(.kill); try? await process.delete() }
                        await processes.remove(pid)
                        throw error
                    }
                    Task {
                        do {
                            let status = try await process.wait()
                            try? await process.delete()
                            await processes.remove(pid)
                            try emit(Response(event: "exit", exitCode: status.exitCode, process: pid))
                        } catch {
                            await processes.remove(pid)
                            try? emit(Response(event: "exit", error: String(describing: error), process: pid))
                        }
                    }
                case "exec":
                    try emit(try await executeGuest(request, container: container))

            default: throw RuntimeError("Unknown guest action")
            }
        } catch { try? emit(Response(id: request.id, error: String(describing: error))) }
    }

    @MainActor static func executeGuest(_ request: Request, container: LinuxContainer?, input: (any ReaderStream)? = nil) async throws -> Response {
        guard let id = request.workspace else { throw RuntimeError("Workspace ID is required") }
                    guard let container, let command = request.arguments, !command.isEmpty else {
                        throw RuntimeError("A running workspace and arguments are required")
                    }
                    let stdout = Capture(), stderr = Capture()
                    let process = try await startGuestProcess(id, request: request) { try await container.exec(UUID().uuidString) { config in
                        config.arguments = command
                        config.capabilities = .allCapabilities
                        config.workingDirectory = container.config.process.workingDirectory
                        config.environmentVariables = ["PATH=\(LinuxProcessConfiguration.defaultPath)", "HOME=/root", "TMPDIR=/tmp"] + guestGPUEnvironment
                        config.stdin = input
                        config.stdout = stdout
                        config.stderr = stderr
                    } }
                    do {
                        let status = try await withTaskCancellationHandler {
                            try await health.guest(id, seconds: Double(min(max(request.timeout ?? 300, 1), 1800)) + 5) { try await process.wait(timeoutInSeconds: min(max(request.timeout ?? 300, 1), 1800)) }
                        } onCancel: {
                            Task { try? await process.kill(.kill) }
                        }
                        try Task.checkCancellation()
                        try await health.guest(id) { try await process.delete() }
                        return Response(id: request.id, stdout: stdout.text, stderr: stderr.text,
                                          exitCode: status.exitCode, truncated: stdout.truncated || stderr.truncated)
                    } catch {
                        Task { try? await process.kill(.kill); try? await process.delete() }
                        throw error
                    }
    }
}
