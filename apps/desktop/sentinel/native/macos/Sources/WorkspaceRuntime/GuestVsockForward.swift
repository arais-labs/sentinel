import Containerization
import ContainerizationOS
import Darwin
import Foundation
import Synchronization

final class GuestVsockForward: @unchecked Sendable {
    private struct Endpoint {
        let descriptor: Int32
        var relay: BidirectionalRelay?
        var task: Task<Void, Never>?
    }

    private struct State {
        var closed = false
        var listenerOpen = true
        var endpoints: [UUID: Endpoint] = [:]
    }

    private let state = Mutex(State())
    private let listener: Int32
    let path: String

    convenience init(path: String, container: LinuxContainer, port: UInt32) throws {
        try self.init(path: path, maximumConnections: 1) { try await container.dialVsock(port: port) }
    }

    init(path: String, maximumConnections: Int = 2,
         dial: @escaping @Sendable () async throws -> FileHandle) throws {
        precondition(maximumConnections > 0)
        self.path = path
        listener = socket(AF_UNIX, SOCK_STREAM, 0)
        guard listener >= 0 else { throw RuntimeError("Cannot create GPU socket relay") }
        do {
            var address = try RemoteControl.address(path)
            let result = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            guard result == 0 else { throw RuntimeError("Cannot bind GPU socket relay") }
            guard chmod(path, 0o600) == 0, listen(listener, Int32(maximumConnections)) == 0 else {
                unlink(path)
                throw RuntimeError("Cannot listen on GPU socket relay")
            }
        } catch {
            Darwin.close(listener)
            throw error
        }

        DispatchQueue.global().async { [self] in
            defer {
                stop()
                state.withLock { state in
                    if state.listenerOpen {
                        state.listenerOpen = false
                        Darwin.close(listener)
                    }
                }
            }
            while !state.withLock({ $0.closed }) {
                let descriptor = accept(listener, nil, nil)
                if descriptor < 0 {
                    if errno == EINTR { continue }
                    break
                }
                var noSignal: Int32 = 1
                setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
                let id = UUID()
                let accepted = state.withLock { state in
                    guard !state.closed, state.endpoints.count < maximumConnections else { return false }
                    state.endpoints[id] = Endpoint(descriptor: descriptor)
                    state.endpoints[id]?.task = Task {
                        await self.serve(id: id, descriptor: descriptor, dial: dial)
                    }
                    return true
                }
                if !accepted { Darwin.close(descriptor) }
            }
        }
    }

    private func serve(id: UUID, descriptor: Int32,
                       dial: @Sendable () async throws -> FileHandle) async {
        var peerDescriptor: Int32 = -1
        var ownsDescriptors = true
        defer {
            state.withLock { _ = $0.endpoints.removeValue(forKey: id) }
            if ownsDescriptors {
                Darwin.close(descriptor)
                if peerDescriptor >= 0 { Darwin.close(peerDescriptor) }
            }
        }
        do {
            try Task.checkCancellation()
            let peer = try await dial()
            peerDescriptor = dup(peer.fileDescriptor)
            try? peer.close()
            guard peerDescriptor >= 0 else { throw RuntimeError("Cannot retain GPU VM socket") }
            var noSignal: Int32 = 1
            setsockopt(peerDescriptor, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
            let relay = BidirectionalRelay(fd1: descriptor, fd2: peerDescriptor)
            let started = try state.withLock { state in
                guard !state.closed else { return false }
                try relay.start()
                state.endpoints[id]?.relay = relay
                return true
            }
            guard started else { return }
            ownsDescriptors = false
            await relay.waitForCompletion()
        } catch {
            shutdown(descriptor, SHUT_RDWR)
        }
    }

    func stop() {
        let pending = state.withLock { state -> ([BidirectionalRelay], [Task<Void, Never>])? in
            guard !state.closed else { return nil }
            state.closed = true
            if state.listenerOpen { shutdown(listener, SHUT_RDWR) }
            var relays: [BidirectionalRelay] = []
            var tasks: [Task<Void, Never>] = []
            for endpoint in state.endpoints.values {
                if let relay = endpoint.relay { relays.append(relay) }
                else { shutdown(endpoint.descriptor, SHUT_RDWR) }
                if let task = endpoint.task { tasks.append(task) }
            }
            return (relays, tasks)
        }
        guard let pending else { return }
        for relay in pending.0 { relay.stop() }
        for task in pending.1 { task.cancel() }
        unlink(path)
    }
}
