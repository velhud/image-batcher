import AppKit
import SwiftUI

@MainActor
struct ContentView: View {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var model = AppViewModel()
    @State private var batchSheet = false
    @State private var addMany = false
    @State private var newRowsCount = 10
    @State private var showSettings = false
    @State private var refreshGeneration = 0
    @State private var showInterruptedConfirmation = false
    @State private var pendingGenerationIDs: [String] = []

    private var currentModels: [ModelInfo] {
        model.providers.first { $0.id == model.session?.global_settings.provider_id }?.models ?? []
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                toolbar
                if !model.restoreWarning.isEmpty {
                    Text(model.restoreWarning).font(.caption).foregroundColor(.orange).padding(8)
                }
                Divider()
                rowsList
                Divider()
                bottomBar
            }
            .sheet(isPresented: $batchSheet) { BatchInputSheet(appModel: model) }
            .sheet(isPresented: $showSettings) { SettingsSheet(appModel: model) }
            .alert("ImageAI", isPresented: Binding(get: { model.errorMessage != nil }, set: { _ in model.errorMessage = nil })) {
                Button("OK", role: .cancel) {}
            } message: { Text(model.errorMessage ?? "") }
            .confirmationDialog("Review interrupted generation", isPresented: $showInterruptedConfirmation, titleVisibility: .visible) {
                Button("Send a new generation") {
                    let ids = pendingGenerationIDs
                    pendingGenerationIDs = []
                    Task { await model.generate(ids: ids, acknowledgeInterrupted: true) }
                }
                Button("Cancel", role: .cancel) { pendingGenerationIDs = [] }
            } message: {
                Text("A previous request may have completed at the provider. Review the saved images and provider activity first. Sending a new generation can create additional images and charges.")
            }
            .task(id: scenePhase == .active ? refreshGeneration : -1) {
                guard scenePhase == .active else { return }
                do { try await BackendLauncher.shared.ensureRunning() }
                catch { if !Task.isCancelled { model.errorMessage = error.localizedDescription }; return }
                await model.observe()
            }
        }
        .frame(minWidth: 850, minHeight: 550)
    }

    private var toolbar: some View {
        HStack {
            Picker("Provider", selection: Binding(
                get: { model.session?.global_settings.provider_id ?? "" },
                set: { value in Task { await model.selectProvider(id: value) } }
            )) {
                if model.session == nil { Text("Loading…").tag("") }
                ForEach(model.providers) { Text($0.name).tag($0.id) }
            }.disabled(model.session == nil || model.isMutating)
            Picker("Model", selection: Binding(
                get: { model.session?.global_settings.model_id ?? "" },
                set: { value in Task { await model.selectModel(id: value) } }
            )) {
                if let selected = model.session?.global_settings.model_id,
                   !currentModels.contains(where: { $0.id == selected }) {
                    Text("Select current model (\(selected))").tag(selected)
                } else if model.session == nil { Text("Loading…").tag("") }
                ForEach(currentModels) { Text($0.name).tag($0.id) }
            }.disabled(model.session == nil || model.isMutating)
            Button("Batch Input…") { batchSheet = true }.disabled(model.session == nil || model.isMutating)
            Button("Settings…") { showSettings = true }.disabled(model.session == nil || model.isMutating)
            Button("Reload") { model.errorMessage = nil; refreshGeneration += 1 }
            Spacer()
            if let stats = model.stats {
                Text("\(stats.total) rows · \(stats.completed) done · \(stats.errors) errors").font(.caption)
            } else if model.isLoading { ProgressView().controlSize(.small) }
        }.padding(8)
    }

    private var rowsList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                ForEach(model.session?.rows ?? []) { row in
                    RowCard(row: row, prompt: Binding(
                        get: { model.promptDrafts[row.id] ?? row.prompt },
                        set: { model.promptDrafts[row.id] = $0 }
                    ), hasDraft: model.promptDrafts[row.id] != nil,
                    onSelection: { value in Task { await model.setSelection(id: row.id, selected: value) } },
                    savePrompt: { Task { await model.savePrompt(id: row.id) } },
                    generate: { requestGeneration(ids: [row.id]) },
                    delete: { Task { await model.deleteRows(ids: [row.id]) } })
                    .disabled(model.isMutating)
                    Divider()
                }
            }.padding()
        }
    }

    private var bottomBar: some View {
        HStack {
            Button { addMany = true } label: { Label("Add Rows", systemImage: "plus.square.on.square") }
                .popover(isPresented: $addMany) {
                    VStack {
                        Stepper("Rows: \(newRowsCount)", value: $newRowsCount, in: 1...500)
                        Button("Add") {
                            Task { await model.addRows(prompts: Array(repeating: "", count: newRowsCount)) }
                            addMany = false
                        }
                    }.padding()
                }
            Text("Prompt drafts are saved before generation.").font(.caption).foregroundColor(.secondary)
            Spacer()
            Button("Generate Selected") { requestGeneration(ids: model.selectedIDs) }
                .disabled(model.selectedIDs.isEmpty)
            Button("Generate All") { requestGeneration(ids: model.session?.rows.map(\.id) ?? []) }
                .disabled(model.session?.rows.isEmpty != false)
            Button("Stop All", role: .destructive) { Task { await model.stopAll() } }
                .disabled(!model.hasActiveWork)
            Button("Delete Selected", role: .destructive) { Task { await model.deleteRows(ids: model.selectedIDs) } }
                .disabled(model.selectedIDs.isEmpty || model.hasActiveWork)
        }
        .disabled(model.session == nil || model.isMutating)
        .padding(8)
    }

    private func requestGeneration(ids: [String]) {
        let active = Set(model.session?.rows.filter { ["Queued", "Generating"].contains($0.status) }.map(\.id) ?? [])
        let eligible = ids.filter { !active.contains($0) }
        guard !eligible.isEmpty else { return }
        if model.requiresGenerationReview(ids: eligible) {
            pendingGenerationIDs = eligible
            showInterruptedConfirmation = true
        } else {
            Task { await model.generate(ids: eligible) }
        }
    }
}

struct RowCard: View {
    let row: RowDTO
    @Binding var prompt: String
    let hasDraft: Bool
    var onSelection: (Bool) -> Void
    var savePrompt: () -> Void
    var generate: () -> Void
    var delete: () -> Void

    private var active: Bool { ["Queued", "Generating"].contains(row.status) }

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Toggle("Select", isOn: Binding(get: { row.selected }, set: onSelection)).toggleStyle(.checkbox)
                    Text(row.prompt_id?.isEmpty == false ? row.prompt_id! : String(row.id.prefix(8))).font(.caption)
                    if let category = row.category_id, !category.isEmpty { Text(category).font(.caption).foregroundColor(.secondary) }
                    Spacer()
                    Text(row.status).font(.caption).padding(4).background(statusColor.opacity(0.2)).cornerRadius(4)
                }
                TextEditor(text: $prompt).frame(minHeight: 80)
                if !row.error_message.isEmpty { Text(row.error_message).font(.caption).foregroundColor(.orange).textSelection(.enabled) }
                if row.requiresGenerationReview {
                    Text("Outcome unknown. Review saved images and provider activity before generating again.")
                        .font(.caption).foregroundColor(.orange)
                }
                HStack {
                    Button("Save Prompt", action: savePrompt).disabled(!hasDraft)
                    Button("Generate", action: generate).disabled(active)
                    Button("Delete", role: .destructive, action: delete).disabled(active)
                    Spacer()
                    Text("\(prompt.count) chars\(hasDraft ? " · unsaved draft" : "")").font(.caption)
                }
            }
            if let image = row.images.last {
                VStack {
                    if let preview = NSImage(contentsOfFile: image.file_path) {
                        Image(nsImage: preview).resizable().scaledToFit().frame(width: 160, height: 140)
                    }
                    Text("\(row.images.count) image(s)").font(.caption)
                    Button("Open image") { NSWorkspace.shared.open(URL(fileURLWithPath: image.file_path)) }
                }
            }
        }
    }

    private var statusColor: Color {
        switch row.status.lowercased() {
        case "completed": return .green
        case "error", "filtered": return .red
        case "generating", "interrupted": return .orange
        case "queued": return .yellow
        default: return .gray
        }
    }
}
