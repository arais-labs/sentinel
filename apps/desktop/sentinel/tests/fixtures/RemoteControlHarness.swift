import Foundation
import Darwin
import Synchronization
struct RuntimeError: Error { init(_ message: String) {} }
struct Response: Encodable { var id: String?; var event: String? }
@main struct Harness {
    static func main() async throws {
        // Match Runtime.main: a disconnected socket is an IO error, not a
        // process-terminating signal. This harness bypasses that entry point.
        signal(SIGPIPE, SIG_IGN)
        if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--ping" {
            try RemoteControl.ping(path: CommandLine.arguments[2])
            return
        }
        if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--relay" {
            do { try RemoteControl.relay(path: CommandLine.arguments[2]) }
            catch is RemoteSocketUnavailable {
                print("{\"event\":\"unavailable\"}")
            }
            return
        }
        let holder = Mutex<RemoteControl?>(nil)
        let control = try RemoteControl(path: CommandLine.arguments[1], answer: { line in
            guard let data = line.data(using: .utf8), let request = try? JSONSerialization.jsonObject(with: data) as? [String: Any], request["action"] as? String == "status" else { return false }
            holder.withLock { $0 }?.emit(Response(id: request["id"] as? String), bytes: Data((line + "\n").utf8))
            return true
        })
        holder.withLock { $0 = control }
        control.emit(Response(event: "ready"), bytes: Data("{\"event\":\"ready\"}\n".utf8))
        for await line in control.requests {
            if line.contains("block-guest") { try await Task.sleep(for: .seconds(2)) }
            control.emit(Response(id: "reply"), bytes: Data((line + "\n").utf8))
        }
    }
}
