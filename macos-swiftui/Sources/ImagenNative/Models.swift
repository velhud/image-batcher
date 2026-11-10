import Foundation

/// Arbitrary source/receipt metadata remains intact in typed RPC responses.
enum JSONValue: Codable, Equatable {
    case null, bool(Bool), integer(Int), number(Double), string(String)
    case array([JSONValue]), object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let item = try? value.decode(Bool.self) { self = .bool(item) }
        else if let item = try? value.decode(Int.self) { self = .integer(item) }
        else if let item = try? value.decode(Double.self) { self = .number(item) }
        else if let item = try? value.decode(String.self) { self = .string(item) }
        else if let item = try? value.decode([JSONValue].self) { self = .array(item) }
        else { self = .object(try value.decode([String: JSONValue].self)) }
    }

    func encode(to encoder: Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case .null: try value.encodeNil()
        case .bool(let item): try value.encode(item)
        case .integer(let item): try value.encode(item)
        case .number(let item): try value.encode(item)
        case .string(let item): try value.encode(item)
        case .array(let item): try value.encode(item)
        case .object(let item): try value.encode(item)
        }
    }
}

struct ProviderInfo: Codable, Identifiable {
    let id: String
    let name: String
    let rate_limit_note: String?
    let models: [ModelInfo]
}

struct ModelCapabilitiesDTO: Codable {
    let max_images: Int
    let supports_style: Bool
    let supports_negative_prompt: Bool
    let supports_seed: Bool
    let supports_quality: Bool
    let supports_safety: Bool
    let supports_custom_size: Bool?
    let supports_background: Bool?
    let output_formats: [String]?
}

struct ModelInfo: Codable, Identifiable {
    let id: String
    let name: String
    let provider_id: String
    let capabilities: ModelCapabilitiesDTO?
}

struct GlobalSettingsDTO: Codable {
    var provider_id: String
    var model_id: String
    var size_preset: String
    var custom_size: [String: Int]?
    var num_images: Int
    var style_preset: String
    var negative_prompt: String
    var seed: Int?
    var random_seed: Bool
    var quality: Int
    var safety: Int
    var export_folder: String?
    var naming_pattern: String
    var concurrency_limit: Int
    var theme: String
    var prompt_highlighting: Bool
    var generate_behavior: String
    var regen_use_same_seed: Bool
    var confirm_generate_threshold: Int
    var rate_limit_rpm: Int
    var thinking_budget: Int?
    var thinking_level: String?
    var prompt_wrapper: String?
    var output_format: String?
    var background: String?

    /// Explicit nulls clear previously configured values in the backend merge.
    func encode(to encoder: Encoder) throws {
        let values: [String: JSONValue] = [
            "provider_id": .string(provider_id), "model_id": .string(model_id),
            "size_preset": .string(size_preset),
            "custom_size": custom_size.map { JSONValue.object($0.mapValues { JSONValue.integer($0) }) } ?? .null,
            "num_images": .integer(num_images), "style_preset": .string(style_preset),
            "negative_prompt": .string(negative_prompt), "seed": seed.map { .integer($0) } ?? .null,
            "random_seed": .bool(random_seed), "quality": .integer(quality), "safety": .integer(safety),
            "export_folder": export_folder.map { .string($0) } ?? .null,
            "naming_pattern": .string(naming_pattern), "concurrency_limit": .integer(concurrency_limit),
            "theme": .string(theme), "prompt_highlighting": .bool(prompt_highlighting),
            "generate_behavior": .string(generate_behavior), "regen_use_same_seed": .bool(regen_use_same_seed),
            "confirm_generate_threshold": .integer(confirm_generate_threshold), "rate_limit_rpm": .integer(rate_limit_rpm),
            "thinking_budget": .integer(thinking_budget ?? 0), "thinking_level": .string(thinking_level ?? "high"),
            "prompt_wrapper": .string(prompt_wrapper ?? ""), "output_format": .string(output_format ?? "png"),
            "background": .string(background ?? "auto"),
        ]
        try values.encode(to: encoder)
    }

    mutating func selectModel(_ model: ModelInfo) {
        provider_id = model.provider_id
        model_id = model.id
        size_preset = "Square 1024"
        custom_size = nil
        thinking_budget = 0
        thinking_level = "high"
        guard let caps = model.capabilities else { return }
        num_images = min(max(1, num_images), caps.max_images)
        if !caps.supports_style { style_preset = "None" }
        if !caps.supports_negative_prompt { negative_prompt = "" }
        if !caps.supports_seed { seed = nil; random_seed = true }
        if !caps.supports_quality { quality = 5 }
        if !caps.supports_safety { safety = 2 }
        let formats = caps.output_formats ?? ["png"]
        if !formats.contains(output_format ?? "png") { output_format = formats.first ?? "png" }
        if caps.supports_background != true { background = "auto" }
        if output_format == "jpeg" && background == "transparent" { background = "auto" }
    }
}

struct RowSettingsDTO: Codable {
    var provider_id: String?
    var model_id: String?
    var size_preset: String?
    var custom_size: [String: Int]?
    var num_images: Int?
    var style_preset: String?
    var negative_prompt: String?
    var seed: Int?
    var random_seed: Bool?
    var quality: Int?
    var safety: Int?
    var keep_images: Bool?
    var generate_behavior: String?
    var regen_use_same_seed: Bool?
    var output_format: String?
    var background: String?
}

struct ImageResultDTO: Codable, Identifiable {
    let id: String
    let row_id: String
    let file_path: String
    let metadata: [String: JSONValue]?
}

struct RowDTO: Codable, Identifiable {
    let id: String
    var prompt: String
    var prompt_id: String?
    var category_id: String?
    var source_metadata: [String: JSONValue]?
    var status: String
    var error_message: String
    var selected: Bool
    var settings: RowSettingsDTO
    var images: [ImageResultDTO]
    var tags: [String]?

    var requiresGenerationReview: Bool { status == "Interrupted" }
}

struct SessionDTO: Codable {
    let id: String
    var global_settings: GlobalSettingsDTO
    var rows: [RowDTO]
}

struct StatsDTO: Codable {
    let total: Int
    let completed: Int
    let errors: Int
    let average_duration: Double
    let per_provider: [String: Int]?
    let per_model: [String: Int]?
}

struct BackendState: Codable {
    let session: SessionDTO
    let providers: [ProviderInfo]
    let stats: StatsDTO
    let restore_warning: String?
}

struct BatchInputPayload: Codable, Equatable {
    let raw: String
    let mode: String
    let prompt_field: String
    let csv_column: String
    let prompt_append: String
    let apply_mode: String
}

struct ParsedBatchRowDTO: Codable {
    let prompt: String
    let prompt_id: String
    let category_id: String
    let source_metadata: [String: JSONValue]
}

struct BatchParseResponse: Codable {
    let prompts: [String]
    let errors: [String]
    let rows: [ParsedBatchRowDTO]
}

struct BatchImportResponse: Codable {
    let rows: [RowDTO]
    let errors: [String]
}
