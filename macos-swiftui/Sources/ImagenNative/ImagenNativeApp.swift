import SwiftUI

@main
@MainActor
struct ImagenNativeApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
            .commands { CommandGroup(replacing: .newItem) { } }
    }
}
