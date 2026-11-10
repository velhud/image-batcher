import SwiftUI

@MainActor
struct BatchInputSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var appModel: AppViewModel
    @State private var rawText = ""
    @State private var mode: BatchMode = .lines
    @State private var preview: BatchParseResponse?
    @State private var previewPayload: BatchInputPayload?
    @State private var error: String?
    @State private var applyMode: ApplyMode = .append
    @State private var promptField = "prompt"
    @State private var csvColumn = "prompt"
    @State private var promptAppend = ""
    @State private var isBusy = false

    private var payload: BatchInputPayload {
        BatchInputPayload(raw: rawText, mode: mode.rawValue, prompt_field: promptField,
                          csv_column: csvColumn, prompt_append: promptAppend, apply_mode: applyMode.rawValue)
    }

    private var canApply: Bool {
        !isBusy && !appModel.isMutating && previewPayload == payload &&
        preview?.rows.isEmpty == false && preview?.errors.isEmpty == true
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Batch input").font(.title2)
            Picker("Mode", selection: $mode) {
                ForEach(BatchMode.allCases, id: \.self) { Text($0.title).tag($0) }
            }.pickerStyle(.segmented)
            if mode == .json_array || mode == .json_lines { TextField("Prompt field", text: $promptField) }
            if mode == .csv { TextField("CSV column (name or zero-based index)", text: $csvColumn) }
            TextEditor(text: $rawText).frame(minHeight: 150)
            TextField("Append to every prompt", text: $promptAppend)
            HStack {
                Button("Preview") { Task { await parse() } }.disabled(isBusy || rawText.isEmpty)
                if isBusy { ProgressView().controlSize(.small) }
                Spacer()
                if previewPayload != nil && previewPayload != payload { Text("Input changed; preview again.").font(.caption) }
            }
            if let error { Text(error).foregroundColor(.red).textSelection(.enabled) }
            if let preview {
                Text("\(preview.rows.count) prompts · \(preview.errors.count) errors").font(.caption)
                if !preview.errors.isEmpty {
                    Text(preview.errors.joined(separator: "\n")).font(.caption).foregroundColor(.red).textSelection(.enabled)
                }
                List(Array(preview.rows.enumerated()), id: \.offset) { item in
                    VStack(alignment: .leading) {
                        if !item.element.prompt_id.isEmpty {
                            Text("\(item.element.category_id) / \(item.element.prompt_id)").font(.caption).foregroundColor(.secondary)
                        }
                        Text(item.element.prompt)
                    }
                }
            }
            Picker("Apply", selection: $applyMode) {
                ForEach(ApplyMode.allCases, id: \.self) { Text($0.title).tag($0) }
            }
            HStack {
                Spacer()
                Button("Apply") { Task { await apply() } }.disabled(!canApply)
                Button("Cancel", role: .cancel) { dismiss() }.disabled(isBusy)
            }
        }
        .padding()
        .frame(minWidth: 600, minHeight: 550)
    }

    @MainActor private func parse() async {
        let requested = payload
        isBusy = true
        error = nil
        defer { isBusy = false }
        do {
            preview = try await appModel.client.parseBatch(requested)
            previewPayload = requested
        } catch { self.error = error.localizedDescription; preview = nil; previewPayload = nil }
    }

    @MainActor private func apply() async {
        guard canApply else { return }
        isBusy = true
        error = nil
        defer { isBusy = false }
        do {
            let response = try await appModel.importBatch(payload)
            if response.errors.isEmpty { dismiss() }
            else {
                error = "Import was not applied: " + response.errors.joined(separator: "; ")
                preview = nil
                previewPayload = nil
            }
        } catch {
            self.error = error.localizedDescription + " Review the current rows before applying again."
            preview = nil
            previewPayload = nil
        }
    }
}

enum BatchMode: String, CaseIterable {
    case lines, numbered, json_array, json_lines, csv
    var title: String {
        switch self {
        case .lines: return "Lines"
        case .numbered: return "Numbered"
        case .json_array: return "JSON Array"
        case .json_lines: return "JSON Lines"
        case .csv: return "CSV"
        }
    }
}

enum ApplyMode: String, CaseIterable {
    case append, replace, fill
    var title: String {
        switch self {
        case .append: return "Append new rows"
        case .replace: return "Replace all rows"
        case .fill: return "Fill empty, then append"
        }
    }
}
