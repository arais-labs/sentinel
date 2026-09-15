import Foundation
import Darwin

struct RuntimeError: Error, CustomStringConvertible {
    let description: String
    init(_ message: String) { description = message }
}
struct Response: Encodable { var id: String?; var event: String? }

@main struct Harness {
    static func main() async {
        do {
            let args = CommandLine.arguments
            if args[1] == "--short-lease" { try RuntimeUpdate.serve(args[2], leaseSeconds: 0.2); return }
            if args[1] == "--update-session" { try RuntimeUpdate.serve(args[2]); return }
            if args[1] == "--legacy-owner" {
                let owner = try RuntimeUpdate.lock(args[2] + "/owner.lock", LOCK_EX)
                defer { close(owner) }
                print("locked"); fflush(stdout)
                _ = readLine()
                return
            }
            guard args[1] == "--owned-service" else { throw RuntimeError("Unexpected launch") }
            let owner = RuntimeUpdate.inheritedOwner
            guard flock(owner, LOCK_EX | LOCK_NB) == 0 else { throw RuntimeError("Missing inherited owner") }
            defer { close(owner) }
            let control = try RemoteControl(path: args[2] + "/control.sock")
            control.emit(Response(event: "ready"), bytes: Data("{\"event\":\"ready\"}\n".utf8))
            for await line in control.requests {
                let request = try JSONSerialization.jsonObject(with: Data(line.utf8)) as! [String: String]
                control.emit(Response(id: request["id"]), bytes: Data((line + "\n").utf8))
                if request["action"] == "maintenance" { return }
            }
        } catch { try? RuntimeUpdate.reply(["error": String(describing: error)]) }
    }
}
