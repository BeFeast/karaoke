import Observation
import SwiftUI

enum AppTab: Hashable { case songs, catalog, settings }

enum Route: Hashable {
    case processing(Int)
    case feedback
    case shelf(axis: String, value: String, title: String)
    case youtubeHelp
}

struct AddRequest: Identifiable {
    let id = UUID()
    var url = ""
    var shared: SharedVideo?
}

@MainActor
@Observable
final class AppRouter {
    var tab: AppTab = .songs
    var songsPath: [Route] = []
    var catalogPath: [Route] = []
    var settingsPath: [Route] = []
    var add: AddRequest?
    /// Screen captured when Feedback was opened (attached only on request).
    var feedbackShot: Data?

    func push(_ route: Route) {
        switch tab {
        case .songs: songsPath.append(route)
        case .catalog: catalogPath.append(route)
        case .settings: settingsPath.append(route)
        }
    }

    /// Processing replaces Add: it opens on the tab Add was started from.
    func showProcessing(_ id: Int) {
        if tab == .settings { tab = .songs }
        push(.processing(id))
    }

    /// A retried job takes the place of the failed one in the history.
    func replaceProcessing(_ old: Int, with new: Int) {
        func swap(_ path: inout [Route]) -> Bool {
            guard let index = path.lastIndex(of: .processing(old)) else { return false }
            path[index] = .processing(new)
            return true
        }
        if swap(&songsPath) || swap(&catalogPath) || swap(&settingsPath) { return }
        showProcessing(new)
    }

    func openFeedback() {
        feedbackShot = ScreenCapture.png()
        if tab != .settings {
            tab = .settings
        }
        settingsPath = [.feedback]
    }
}

struct AppShell: View {
    let signOut: () async throws -> Void

    @Environment(\.scenePhase) private var scenePhase
    @State private var library: LibraryStore
    @State private var session: PlayerSession
    @State private var prefs: Prefs
    @State private var router = AppRouter()

    init(api: any SongsAPI, prefs: Prefs, signOut: @escaping () async throws -> Void) {
        self.signOut = signOut
        _library = State(initialValue: LibraryStore(api: api))
        _prefs = State(initialValue: prefs)
        _session = State(initialValue: PlayerSession(prefs: prefs))
    }

    var body: some View {
        TabView(selection: $router.tab) {
            NavigationStack(path: $router.songsPath) {
                MySongsView()
                    .safeAreaInset(edge: .bottom, spacing: 0) { Dock(showAdd: true) }
                    .navigationDestination(for: Route.self, destination: destination)
            }
            .tabItem { Label(L10n.t("tab.mySongs"), systemImage: "music.note.list") }
            .tag(AppTab.songs)

            NavigationStack(path: $router.catalogPath) {
                CatalogView()
                    .safeAreaInset(edge: .bottom, spacing: 0) { Dock(showAdd: true) }
                    .navigationDestination(for: Route.self, destination: destination)
            }
            .tabItem { Label(L10n.t("tab.catalog"), systemImage: "square.grid.2x2") }
            .tag(AppTab.catalog)

            NavigationStack(path: $router.settingsPath) {
                SettingsView(signOut: {
                    session.close()
                    try await signOut()
                })
                .safeAreaInset(edge: .bottom, spacing: 0) { Dock(showAdd: false) }
                .navigationDestination(for: Route.self, destination: destination)
            }
            .tabItem { Label(L10n.t("tab.settings"), systemImage: "slider.horizontal.3") }
            .tag(AppTab.settings)
        }
        .environment(library)
        .environment(session)
        .environment(prefs)
        .environment(router)
        .fullScreenCover(isPresented: Binding(get: { session.stagePresented }, set: { session.stagePresented = $0 })) {
            StageView()
                .environment(library)
                .environment(session)
                .environment(prefs)
                .environment(router)
        }
        .sheet(item: Binding(get: { router.add }, set: { router.add = $0 }), onDismiss: presentPendingShare) { request in
            AddSongView(request: request)
                .environment(library)
                .environment(session)
                .environment(router)
                .presentationBackground(Color.snBlackout)
        }
        .sensoryFeedback(trigger: library.completions) { _, _ in prefs.haptics ? .success : nil }
        .task {
            await library.refresh()
            presentPendingShare()
        }
        .task(id: scenePhase) {
            // One UI clock for the mini-player, the Stage and Continue singing.
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                session.tick()
                try? await Task.sleep(for: .milliseconds(50))
            }
        }
        .onChange(of: scenePhase, initial: true) { _, phase in
            library.setActive(phase == .active)
            if phase == .active {
                presentPendingShare()
                Task { await library.refresh() }
            } else {
                session.rememberNow()
            }
            updateIdleTimer()
        }
        .onChange(of: session.stagePresented) { _, _ in updateIdleTimer() }
        .onChange(of: prefs.keepAwake) { _, _ in updateIdleTimer() }
        .onDisappear { UIApplication.shared.isIdleTimerDisabled = false }
    }

    @ViewBuilder
    private func destination(_ route: Route) -> some View {
        switch route {
        case let .processing(id): ProcessingView(jobId: id)
        case .feedback: FeedbackView()
        case let .shelf(axis, value, title): ShelfGridView(axis: axis, value: value, title: title)
        case .youtubeHelp: YouTubeHelpView()
        }
    }

    private func updateIdleTimer() {
        UIApplication.shared.isIdleTimerDisabled = session.stagePresented && prefs.keepAwake && scenePhase == .active
    }

    /// A link shared from YouTube opens Add song, prefilled, after sign-in.
    private func presentPendingShare() {
        guard library.identity != nil, router.add == nil, scenePhase == .active,
              let video = ShareInbox.pending() else { return }
        session.stagePresented = false
        router.tab = .songs
        router.songsPath = []
        router.add = AddRequest(url: video.url.absoluteString, shared: video)
    }
}

/// Pinned above the tab bar: the mini-player, then Add song (one tap).
struct Dock: View {
    let showAdd: Bool
    @Environment(PlayerSession.self) private var session
    @Environment(AppRouter.self) private var router

    var body: some View {
        if session.song != nil || showAdd {
            VStack(spacing: 8) {
                if session.song != nil { MiniPlayer() }
                if showAdd {
                    Button {
                        router.add = AddRequest()
                    } label: {
                        Label(L10n.t("action.add"), systemImage: "plus")
                    }
                    .buttonStyle(.snPrimary)
                    .accessibilityIdentifier("songs.add")
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 8)
            .background(
                LinearGradient(colors: [Color.snBlackout.opacity(0), Color.snBlackout], startPoint: .top, endPoint: .init(x: 0.5, y: 0.25))
                    .ignoresSafeArea(edges: .bottom)
            )
        }
    }
}

struct MiniPlayer: View {
    @Environment(PlayerSession.self) private var session
    @Environment(Prefs.self) private var prefs

    var body: some View {
        if let song = session.song {
            let player = session.player
            HStack(spacing: 12) {
                SongCover(title: song.title, url: song.coverURL, size: 40)
                VStack(alignment: .leading, spacing: 2) {
                    Text(song.title)
                        .font(SnFont.inter(15, .semibold, relativeTo: .subheadline))
                        .foregroundStyle(Color.snText)
                        .lineLimit(1)
                    Text(currentLine ?? song.artist ?? "")
                        .font(SnFont.caption)
                        .foregroundStyle(Color.snMuted)
                        .lineLimit(1)
                        .accessibilityIdentifier("mini.line")
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Button {
                    player?.toggle()
                } label: {
                    Image(systemName: player?.isPlaying == true ? "pause.fill" : "play.fill")
                        .font(.system(size: 20, weight: .bold))
                        .foregroundStyle(Color.snText)
                        .frame(width: 44, height: 44)
                }
                .disabled(player == nil)
                .accessibilityLabel(player?.isPlaying == true ? L10n.t("stage.pause") : L10n.t("stage.play"))
                .accessibilityIdentifier("mini.playPause")
                .sensoryFeedback(trigger: player?.isPlaying) { _, _ in prefs.haptics ? .impact(weight: .light) : nil }
            }
            .padding(.leading, 8)
            .padding(.trailing, 4)
            .padding(.vertical, 8)
            .background(alignment: .bottom) {
                ZStack(alignment: .bottomLeading) {
                    RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.snRaised)
                    GeometryReader { geo in
                        Capsule().fill(Color.snCyan)
                            .frame(width: geo.size.width * progress, height: 2)
                            .frame(maxHeight: .infinity, alignment: .bottom)
                    }
                    .padding(.horizontal, 6)
                }
            }
            .contentShape(Rectangle())
            .onTapGesture { session.stagePresented = true }
            .accessibilityElement(children: .contain)
            .accessibilityAction(named: L10n.t("mini.open", ["title": song.title])) { session.stagePresented = true }
            .accessibilityIdentifier("mini.player")
        }
    }

    private var progress: CGFloat {
        guard let player = session.player, player.duration > 0 else { return 0 }
        return CGFloat(min(1, player.position / player.duration))
    }

    private var currentLine: String? {
        guard let player = session.player, let lines = session.lyrics?.timedLines, !lines.isEmpty,
              let index = LyricsModel.activeIndex(at: player.position, in: lines) else { return nil }
        return lines[index].text
    }
}

enum ScreenCapture {
    /// PNG of the key window as it is now, before Feedback covers it.
    @MainActor
    static func png() -> Data? {
        let window = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first { $0.isKeyWindow }
        guard let window else { return nil }
        let renderer = UIGraphicsImageRenderer(bounds: window.bounds)
        return renderer.pngData { _ in window.drawHierarchy(in: window.bounds, afterScreenUpdates: false) }
    }
}
