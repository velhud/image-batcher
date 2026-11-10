import Foundation

enum BackendError: LocalizedError {
    case server(String)
    case invalidResponse(String)

    var errorDescription: String? {
        switch self {
        case .server(let message): return message
        case .invalidResponse(let message): return "Invalid backend response: \(message)"
        }
    }
}

struct RPCRequest<Payload: Encodable>: Encodable {
    let action: String
    let data: Payload
}

struct RPCResponse<DataType: Decodable>: Decodable {
    let ok: Bool
    let data: DataType?
    let error: String?
}

private struct RPCStatus: Decodable {
    let ok: Bool
    let error: String?
}

final class BackendClient {
    private let baseURL: URL
    private let session: URLSession

    init(port: Int = 8765, session: URLSession = .shared) {
        self.baseURL = URL(string: "http://127.0.0.1:\(port)/rpc")!
        self.session = session
    }

    func fetchState() async throws -> BackendState {
        try await send(action: "state", payload: Empty())
    }

    func addRows(prompts: [String]) async throws -> [RowDTO] {
        let response: AddRowsResponse = try await send(action: "add_rows", payload: ["prompts": prompts])
        return response.rows
    }

    func updateRow(id: String, prompt: String? = nil, selected: Bool? = nil) async throws -> RowDTO {
        var payload: [String: JSONValue] = ["row_id": .string(id)]
        if let prompt { payload["prompt"] = .string(prompt) }
        if let selected { payload["selected"] = .bool(selected) }
        return try await send(action: "update_row", payload: payload)
    }

    func deleteRows(ids: [String]) async throws {
        let _: AddRowsResponse = try await send(action: "delete_rows", payload: ["row_ids": ids])
    }

    func generate(ids: [String]) async throws {
        let _: GenerateResponse = try await send(action: "generate_rows", payload: ["row_ids": ids])
    }

    func stopAll() async throws {
        let _: Empty = try await send(action: "stop_all", payload: Empty())
    }

    func export(ids: [String], folder: String) async throws -> Int {
        let payload: [String: JSONValue] = ["row_ids": .array(ids.map { .string($0) }), "folder": .string(folder)]
        let response: ExportResponse = try await send(action: "export", payload: payload)
        return response.exported
    }

    func updateGlobal(settings: GlobalSettingsDTO) async throws -> GlobalSettingsDTO {
        try await send(action: "global_settings", payload: settings)
    }

    func parseBatch(_ payload: BatchInputPayload) async throws -> BatchParseResponse {
        try await send(action: "parse_batch", payload: payload)
    }

    func importBatch(_ payload: BatchInputPayload) async throws -> BatchImportResponse {
        try await send(action: "import_batch", payload: payload)
    }

    /// Every action, including generate/delete/stop, checks the same error envelope.
    static func decodeResponse<Output: Decodable>(_ type: Output.Type, data: Data, statusCode: Int) throws -> Output {
        let decoder = JSONDecoder()
        let status = try? decoder.decode(RPCStatus.self, from: data)
        guard (200...299).contains(statusCode) else {
            throw BackendError.server(status?.error ?? "Backend returned HTTP \(statusCode).")
        }
        guard let status else { throw BackendError.invalidResponse("missing RPC status") }
        guard status.ok else { throw BackendError.server(status.error ?? "The operation failed.") }
        do {
            let response = try decoder.decode(RPCResponse<Output>.self, from: data)
            guard let result = response.data else { throw BackendError.invalidResponse("missing data") }
            return result
        } catch let error as BackendError {
            throw error
        } catch {
            throw BackendError.invalidResponse(error.localizedDescription)
        }
    }

    private func send<Payload: Encodable, Output: Decodable>(action: String, payload: Payload) async throws -> Output {
        var request = URLRequest(url: baseURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 15
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(RPCRequest(action: action, data: payload))
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw BackendError.invalidResponse("not HTTP") }
        return try Self.decodeResponse(Output.self, data: data, statusCode: response.statusCode)
    }
}

struct Empty: Codable {}
struct AddRowsResponse: Codable { let rows: [RowDTO] }
struct GenerateResponse: Codable { let queued: [String] }
struct ExportResponse: Codable { let exported: Int }
