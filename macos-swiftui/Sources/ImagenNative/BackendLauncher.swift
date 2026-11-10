import Foundation

@MainActor
final class BackendLauncher {
    static let shared = BackendLauncher()
    private var process: Process?
    private var checking = false
    private init() {}

    /// Connection/bootstrap only; mutations are never retried automatically.
    func ensureRunning() async throws {
        if checking || process?.isRunning == true { return }
        checking = true
        defer { checking = false }
        if await isReachable() { return }
        try Task.checkCancellation()
        guard let root = repositoryRoot() else {
            throw BackendError.server("Cannot locate app/backend_server.py. Run from the repository or set IMAGEAI_PROJECT_ROOT, then click Reload.")
        }
        let candidate = Process()
        let configured = ProcessInfo.processInfo.environment["IMAGEAI_PYTHON"]
        let virtualenv = root.appendingPathComponent(".venv/bin/python").path
        if let configured, !configured.isEmpty {
            candidate.executableURL = URL(fileURLWithPath: configured)
            candidate.arguments = ["-m", "app.backend_server"]
        } else if FileManager.default.isExecutableFile(atPath: virtualenv) {
            candidate.executableURL = URL(fileURLWithPath: virtualenv)
            candidate.arguments = ["-m", "app.backend_server"]
        } else {
            candidate.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            candidate.arguments = ["python3", "-m", "app.backend_server"]
        }
        candidate.currentDirectoryURL = root
        try candidate.run()
        process = candidate
    }

    private func repositoryRoot() -> URL? {
        let fm = FileManager.default
        if let explicit = ProcessInfo.processInfo.environment["IMAGEAI_PROJECT_ROOT"] {
            let root = URL(fileURLWithPath: explicit)
            return fm.fileExists(atPath: root.appendingPathComponent("app/backend_server.py").path) ? root : nil
        }
        let starts = [URL(fileURLWithPath: fm.currentDirectoryPath), URL(fileURLWithPath: #filePath).deletingLastPathComponent()]
        for start in starts {
            var root = start
            for _ in 0..<6 {
                if fm.fileExists(atPath: root.appendingPathComponent("app/backend_server.py").path) { return root }
                root.deleteLastPathComponent()
            }
        }
        return nil
    }

    private func isReachable() async -> Bool {
        let url = URL(string: "http://127.0.0.1:8765/rpc")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 2
        request.httpBody = #"{"action":"state","data":{}}"#.data(using: .utf8)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else { return false }
            let _: BackendState = try BackendClient.decodeResponse(BackendState.self, data: data, statusCode: http.statusCode)
            return true
        } catch { return false }
    }
}
