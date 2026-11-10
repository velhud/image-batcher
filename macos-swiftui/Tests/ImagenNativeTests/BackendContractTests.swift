import Foundation
import XCTest
@testable import ImagenNative

final class BackendContractTests: XCTestCase {
    private let settingsJSON = #"""
    {
      "provider_id":"mock", "model_id":"mock-standard", "size_preset":"Square 1024",
      "custom_size":null, "num_images":1, "style_preset":"None", "negative_prompt":"",
      "seed":null, "random_seed":true, "quality":5, "safety":2, "export_folder":null,
      "naming_pattern":"{index}_{slug}.png", "concurrency_limit":2,
      "generate_behavior":"keep", "regen_use_same_seed":true,
      "confirm_generate_threshold":300, "rate_limit_rpm":60, "thinking_budget":0,
      "thinking_level":"high", "prompt_wrapper":"", "output_format":"png", "background":"auto"
    }
    """#

    private func settings() throws -> GlobalSettingsDTO {
        try JSONDecoder().decode(GlobalSettingsDTO.self, from: Data(settingsJSON.utf8))
    }

    func testRPCFailureIsNotSuccessWithHTTP200() {
        let data = Data(#"{"ok":false,"error":"Batch validation failed"}"#.utf8)
        XCTAssertThrowsError(try BackendClient.decodeResponse(GenerateResponse.self, data: data, statusCode: 200)) {
            XCTAssertEqual($0.localizedDescription, "Batch validation failed")
        }
        XCTAssertThrowsError(try BackendClient.decodeResponse(AddRowsResponse.self, data: data, statusCode: 200))
        XCTAssertThrowsError(try BackendClient.decodeResponse(Empty.self, data: data, statusCode: 200))
    }

    func testHTTPErrorRetainsBackendExplanation() {
        let data = Data(#"{"ok":false,"error":"Wait for active jobs before deleting rows"}"#.utf8)
        XCTAssertThrowsError(try BackendClient.decodeResponse(Empty.self, data: data, statusCode: 400)) {
            XCTAssertEqual($0.localizedDescription, "Wait for active jobs before deleting rows")
        }
    }

    func testMalformedResponseAndMissingDataFail() {
        XCTAssertThrowsError(try BackendClient.decodeResponse(Empty.self, data: Data("not JSON".utf8), statusCode: 200))
        XCTAssertThrowsError(try BackendClient.decodeResponse(Empty.self, data: Data(#"{"ok":true}"#.utf8), statusCode: 200))
        XCTAssertThrowsError(try BackendClient.decodeResponse(Empty.self, data: Data(#"{"ok":true,"data":{}}"#.utf8), statusCode: 503))
    }

    func testGenerateAndStopSuccessEnvelopesDecode() throws {
        let generated = try BackendClient.decodeResponse(GenerateResponse.self,
            data: Data(#"{"ok":true,"data":{"queued":["row-one","row-two"]}}"#.utf8), statusCode: 200)
        XCTAssertEqual(generated.queued, ["row-one", "row-two"])
        let _: Empty = try BackendClient.decodeResponse(Empty.self,
            data: Data(#"{"ok":true,"data":{"stopped":true}}"#.utf8), statusCode: 200)
    }

    func testBatchPayloadPreservesCSVAndEveryApplyMode() throws {
        let raw = "prompt_id,category_id,prompt\nlogo-1,brand,\"A blue, round mark\nwith a white border\""
        for mode in ApplyMode.allCases {
            let payload = BatchInputPayload(raw: raw, mode: "csv", prompt_field: "prompt",
                                            csv_column: "prompt", prompt_append: "Transparent background", apply_mode: mode.rawValue)
            let encoded = try JSONEncoder().encode(RPCRequest(action: "import_batch", data: payload))
            let request = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
            let data = try XCTUnwrap(request["data"] as? [String: Any])
            XCTAssertEqual(request["action"] as? String, "import_batch")
            XCTAssertEqual(data["raw"] as? String, raw)
            XCTAssertEqual(data["apply_mode"] as? String, mode.rawValue)
            XCTAssertEqual(data["prompt_append"] as? String, "Transparent background")
        }
        XCTAssertEqual(ApplyMode.allCases.map(\.rawValue), ["append", "replace", "fill"])
    }

    func testBackendParsedMetadataIsRetained() throws {
        let response = #"""
        {"ok":true,"data":{"prompts":["A blue, round mark"],"errors":[],"rows":[{
          "prompt":"A blue, round mark", "prompt_id":"logo-1", "category_id":"brand",
          "source_metadata":{"campaign":"winter","revision":2,"nested":{"enabled":true,"palette":["blue","white"]}}
        }]}}
        """#
        let parsed = try BackendClient.decodeResponse(BatchParseResponse.self, data: Data(response.utf8), statusCode: 200)
        let row = try XCTUnwrap(parsed.rows.first)
        XCTAssertEqual(row.prompt_id, "logo-1")
        XCTAssertEqual(row.category_id, "brand")
        XCTAssertEqual(row.source_metadata["revision"], .integer(2))
        XCTAssertEqual(row.source_metadata["nested"], .object([
            "enabled": .bool(true), "palette": .array([.string("blue"), .string("white")])
        ]))
    }

    func testGlobalSettingsCanExplicitlyClearNullableFields() throws {
        var value = try settings()
        value.custom_size = nil
        value.seed = nil
        value.export_folder = nil
        let encoded = try JSONEncoder().encode(value)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        XCTAssertTrue(object["custom_size"] is NSNull)
        XCTAssertTrue(object["seed"] is NSNull)
        XCTAssertTrue(object["export_folder"] is NSNull)
        XCTAssertEqual(object["output_format"] as? String, "png")
    }

    func testSelectingModelUpdatesProviderAndClearsUnsupportedSettings() throws {
        var value = try settings()
        value.custom_size = ["width": 2048, "height": 2048]
        value.size_preset = "Custom"
        value.num_images = 10
        value.style_preset = "Photographic"
        value.negative_prompt = "blur"
        value.seed = 123
        value.random_seed = false
        value.quality = 10
        value.safety = 3
        value.thinking_budget = 4096
        value.output_format = "webp"
        value.background = "transparent"
        let caps = ModelCapabilitiesDTO(max_images: 2, supports_style: false, supports_negative_prompt: false,
            supports_seed: false, supports_quality: false, supports_safety: false,
            supports_custom_size: false, supports_background: false, output_formats: ["png"])
        value.selectModel(ModelInfo(id: "current-model", name: "Current model", provider_id: "provider", capabilities: caps))
        XCTAssertEqual(value.provider_id, "provider")
        XCTAssertEqual(value.model_id, "current-model")
        XCTAssertEqual(value.size_preset, "Square 1024")
        XCTAssertNil(value.custom_size)
        XCTAssertEqual(value.num_images, 2)
        XCTAssertEqual(value.style_preset, "None")
        XCTAssertEqual(value.negative_prompt, "")
        XCTAssertNil(value.seed)
        XCTAssertTrue(value.random_seed)
        XCTAssertEqual(value.quality, 5)
        XCTAssertEqual(value.safety, 2)
        XCTAssertEqual(value.thinking_budget, 0)
        XCTAssertEqual(value.output_format, "png")
        XCTAssertEqual(value.background, "auto")
    }

    func testImportedRowPreservesIDsAndInterruptedReviewState() throws {
        let response = #"""
        {"ok":true,"data":{"rows":[{
          "id":"row-one","prompt":"A mark","prompt_id":"logo-1","category_id":"brand",
          "source_metadata":{"campaign":"winter"},"status":"Interrupted","error_message":"Outcome unknown",
          "selected":true,"settings":{},"images":[],"tags":[]
        }],"errors":[]}}
        """#
        let imported = try BackendClient.decodeResponse(BatchImportResponse.self, data: Data(response.utf8), statusCode: 200)
        var row = try XCTUnwrap(imported.rows.first)
        XCTAssertEqual(row.prompt_id, "logo-1")
        XCTAssertEqual(row.source_metadata?["campaign"], .string("winter"))
        XCTAssertTrue(row.requiresGenerationReview)
        row.status = "Error"
        XCTAssertFalse(row.requiresGenerationReview)
    }

    func testJSONMetadataRoundTripsNestedValues() throws {
        let source: JSONValue = .object([
            "array": .array([.null, .bool(false), .integer(5), .number(1.25), .string("value")]),
            "object": .object(["escaped": .string("line one\nline two")])
        ])
        XCTAssertEqual(try JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(source)), source)
    }
}
