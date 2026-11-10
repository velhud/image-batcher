import SwiftUI

@MainActor
struct SettingsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var appModel: AppViewModel
    @State private var concurrency: Int = 2
    @State private var exportFolder: String = ""
    @State private var rpm: Int = 60
    @State private var threshold: Int = 300

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Stepper("Concurrency: \(concurrency)", value: $concurrency, in: 1...16)
            TextField("Export folder", text: $exportFolder)
            Stepper("Requests per minute: \(rpm)", value: $rpm, in: 1...6000)
            Stepper("Confirm threshold: \(threshold)", value: $threshold, in: 10...10000)
            if let error = appModel.errorMessage { Text(error).font(.caption).foregroundColor(.red).textSelection(.enabled) }
            HStack {
                Spacer()
                Button("Save") {
                    Task {
                        if var gs = appModel.session?.global_settings {
                            gs.concurrency_limit = concurrency
                            gs.export_folder = exportFolder.isEmpty ? nil : exportFolder
                            gs.rate_limit_rpm = rpm
                            gs.confirm_generate_threshold = threshold
                            if await appModel.updateGlobal(settings: gs) { dismiss() }
                        }
                    }
                }
                Button("Cancel", role: .cancel) { dismiss() }
            }
        }
        .disabled(appModel.isMutating)
        .padding()
        .onAppear {
            if let gs = appModel.session?.global_settings {
                concurrency = gs.concurrency_limit
                exportFolder = gs.export_folder ?? ""
                rpm = gs.rate_limit_rpm
                threshold = gs.confirm_generate_threshold
            }
        }
        .frame(minWidth: 400)
    }
}
