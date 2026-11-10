import Foundation
import SwiftUI

@MainActor
final class AppViewModel: ObservableObject {
    @Published var providers: [ProviderInfo] = []
    @Published var session: SessionDTO?
    @Published var stats: StatsDTO?
    @Published var errorMessage: String?
    @Published var restoreWarning = ""
    @Published var isLoading = false
    @Published var isMutating = false
    @Published var promptDrafts: [String: String] = [:]

    let client: BackendClient

    init(client: BackendClient = BackendClient()) { self.client = client }

    var hasActiveWork: Bool {
        session?.rows.contains { ["Queued", "Generating"].contains($0.status) } ?? false
    }

    var selectedIDs: [String] { session?.rows.filter(\.selected).map(\.id) ?? [] }

    @discardableResult
    func load(reportErrors: Bool = true) async -> Bool {
        if isLoading { return true }
        isLoading = true
        defer { isLoading = false }
        do {
            let state = try await client.fetchState()
            guard !Task.isCancelled else { return false }
            if let previousID = session?.id, previousID != state.session.id { promptDrafts.removeAll() }
            providers = state.providers
            session = state.session
            stats = state.stats
            restoreWarning = state.restore_warning ?? ""
            let ids = Set(state.session.rows.map(\.id))
            promptDrafts = promptDrafts.filter { ids.contains($0.key) }
            return true
        } catch {
            if reportErrors && !Task.isCancelled { errorMessage = error.localizedDescription }
            return false
        }
    }

    /// Owned by the visible active view's .task; disappearing/inactive views cancel it.
    func observe() async {
        var failures = 0
        while !Task.isCancelled {
            let loaded = await load(reportErrors: false)
            guard !Task.isCancelled else { return }
            if loaded { failures = 0 }
            else {
                failures += 1
                if failures >= 5 {
                    errorMessage = "Could not refresh the backend after five attempts. Check the Python backend, then click Reload."
                    return
                }
            }
            do { try await Task.sleep(nanoseconds: hasActiveWork || !loaded ? 500_000_000 : 2_000_000_000) }
            catch { return }
        }
    }

    private func flushPrompts(ids: [String]) async throws {
        for id in ids {
            guard let prompt = promptDrafts[id] else { continue }
            _ = try await client.updateRow(id: id, prompt: prompt)
            if promptDrafts[id] == prompt { promptDrafts.removeValue(forKey: id) }
        }
    }

    @discardableResult
    private func mutate(_ operation: () async throws -> Void) async -> Bool {
        guard !isMutating else { return false }
        isMutating = true
        defer { isMutating = false }
        do {
            try await operation()
            await load()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func addRows(prompts: [String]) async {
        await mutate { _ = try await client.addRows(prompts: prompts) }
    }

    func savePrompt(id: String) async {
        await mutate { try await flushPrompts(ids: [id]) }
    }

    func setSelection(id: String, selected: Bool) async {
        await mutate { _ = try await client.updateRow(id: id, selected: selected) }
    }

    func deleteRows(ids: [String]) async {
        await mutate {
            try await client.deleteRows(ids: ids)
            for id in ids { promptDrafts.removeValue(forKey: id) }
        }
    }

    func requiresGenerationReview(ids: [String]) -> Bool {
        let requested = Set(ids)
        return session?.rows.contains { requested.contains($0.id) && $0.requiresGenerationReview } ?? false
    }

    func generate(ids: [String], acknowledgeInterrupted: Bool = false) async {
        guard !ids.isEmpty else { return }
        guard acknowledgeInterrupted || !requiresGenerationReview(ids: ids) else {
            errorMessage = "Review the interrupted rows and confirm a new generation. The previous provider request may have completed."
            return
        }
        await mutate {
            try await flushPrompts(ids: ids)
            try await client.generate(ids: ids)
        }
    }

    func stopAll() async {
        await mutate { try await client.stopAll() }
    }

    func export(ids: [String], folder: String) async {
        await mutate { _ = try await client.export(ids: ids, folder: folder) }
    }

    @discardableResult
    func updateGlobal(settings: GlobalSettingsDTO) async -> Bool {
        await mutate { _ = try await client.updateGlobal(settings: settings) }
    }

    func selectProvider(id: String) async {
        guard let first = providers.first(where: { $0.id == id })?.models.first else { return }
        await selectModel(first)
    }

    func selectModel(id: String) async {
        guard let selected = providers.first(where: { $0.id == session?.global_settings.provider_id })?.models.first(where: { $0.id == id }) else { return }
        await selectModel(selected)
    }

    private func selectModel(_ model: ModelInfo) async {
        guard var settings = session?.global_settings else { return }
        settings.selectModel(model)
        await updateGlobal(settings: settings)
    }

    func importBatch(_ payload: BatchInputPayload) async throws -> BatchImportResponse {
        guard !isMutating else { throw BackendError.server("Another change is still being saved.") }
        isMutating = true
        defer { isMutating = false }
        // Fill-empty must consider the visible draft text, not an older saved prompt.
        try await flushPrompts(ids: Array(promptDrafts.keys))
        let response = try await client.importBatch(payload)
        if payload.apply_mode == "replace" { promptDrafts.removeAll() }
        await load()
        return response
    }
}
