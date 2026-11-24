import ClerkKit
import ClerkKitUI
import SwiftUI

@main
struct SonereliApp: App {
    init() {
        SongAssets.clearPreviousRun()
        SonereliAppearance.apply()
        #if DEBUG
        ShareInbox.resetFixture()
        if LiveVerification.active, let video = LiveVerification.video {
            try? ShareInbox.push(SharedVideo(url: video))
        } else if ProcessInfo.processInfo.arguments.contains("--ui-test-share"),
           ProcessInfo.processInfo.arguments.contains("--ui-test-fixture"),
           let url = SharedVideo.validatedURL(from: FixtureCoordinator.youtube.absoluteString) {
            try? ShareInbox.push(SharedVideo(url: url))
        }
        #endif
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .preferredColorScheme(.dark)
                .tint(Color.snPink)
        }
    }
}

enum SonereliAppearance {
    static func apply() {
        let tab = UITabBarAppearance()
        tab.configureWithOpaqueBackground()
        tab.backgroundColor = UIColor(Color.snBlackout)
        tab.shadowColor = UIColor(Color.snDivider)
        let item = UITabBarItemAppearance()
        let font = UIFont(name: "Inter-SemiBold", size: 12) ?? .systemFont(ofSize: 12, weight: .semibold)
        item.normal.iconColor = UIColor(Color.snMuted)
        item.normal.titleTextAttributes = [.foregroundColor: UIColor(Color.snMuted), .font: font]
        item.selected.iconColor = UIColor(Color.snPink)
        item.selected.titleTextAttributes = [.foregroundColor: UIColor(Color.snPink), .font: font]
        tab.stackedLayoutAppearance = item
        tab.inlineLayoutAppearance = item
        tab.compactInlineLayoutAppearance = item
        UITabBar.appearance().standardAppearance = tab
        UITabBar.appearance().scrollEdgeAppearance = tab

        let nav = UINavigationBarAppearance()
        nav.configureWithTransparentBackground()
        nav.backgroundColor = UIColor(Color.snBlackout)
        nav.titleTextAttributes = [
            .foregroundColor: UIColor(Color.snText),
            .font: UIFont(name: "Inter-SemiBold", size: 17) ?? .systemFont(ofSize: 17, weight: .semibold),
        ]
        UINavigationBar.appearance().standardAppearance = nav
        UINavigationBar.appearance().scrollEdgeAppearance = nav
        UINavigationBar.appearance().compactAppearance = nav
    }
}

private struct RootView: View {
    @State private var clerk: Clerk?
    @State private var connectionError: String?
    @State private var attempt = 0
    #if DEBUG
    @State private var fixtureSignedIn = false
    @State private var fixturePrefs: Prefs?
    private let useFixture = ProcessInfo.processInfo.arguments.contains("--ui-test-fixture")
    #endif

    var body: some View {
        #if DEBUG
        if useFixture {
            if fixtureSignedIn, let fixturePrefs {
                AppShell(api: FixtureCoordinator(), prefs: fixturePrefs, signOut: { fixtureSignedIn = false })
            } else {
                signInCover {
                    Button(L10n.t("action.signIn"), action: {
                        fixturePrefs = Prefs.fixture()
                        fixtureSignedIn = true
                    })
                        .buttonStyle(.snPrimary)
                        .accessibilityIdentifier("auth.fixtureSignIn")
                }
            }
        } else {
            liveView
        }
        #else
        liveView
        #endif
    }

    private var liveView: some View {
        Group {
            if let clerk {
                ClerkGate().environment(clerk)
            } else {
                signInCover {
                    if let connectionError {
                        Text(connectionError)
                            .font(SnFont.body)
                            .foregroundStyle(Color.snMuted)
                        Button(L10n.t("action.retry")) {
                            self.connectionError = nil
                            attempt += 1
                        }
                        .buttonStyle(.snPrimary)
                    } else {
                        ProgressView(L10n.t("auth.connecting"))
                            .tint(Color.snCyan)
                    }
                }
            }
        }
        .task(id: attempt) {
            guard clerk == nil else { return }
            do {
                let key = try await Coordinator.publishableKey()
                try Task.checkCancellation()
                clerk = Clerk.configure(publishableKey: key, options: .init(telemetryEnabled: false))
            } catch is CancellationError {
                return
            } catch {
                connectionError = L10n.t("auth.offline")
            }
        }
    }
}

private struct ClerkGate: View {
    @Environment(Clerk.self) private var clerk
    @State private var showingSignIn = false
    @State private var prefs = Prefs()
    #if DEBUG
    @State private var liveSignInFailed = false
    #endif

    private var signedIn: Bool {
        #if DEBUG
        if LiveVerification.active, clerk.user?.id != LiveVerification.subject { return false }
        #endif
        return clerk.isAuthFlowComplete
    }

    var body: some View {
        if signedIn, let user = clerk.user {
            AppShell(api: Coordinator(), prefs: prefs, signOut: { try await clerk.auth.signOut() })
                .id(user.id)
        } else {
            signInCover {
                #if DEBUG
                if LiveVerification.active {
                    if liveSignInFailed { Text(L10n.t("auth.testFailed")).foregroundStyle(Color.snError) }
                    Button(L10n.t("action.signIn")) { Task { await signInWithTestTicket() } }
                        .buttonStyle(.snPrimary)
                        .accessibilityIdentifier("auth.liveSignIn")
                } else {
                    signInButton
                }
                #else
                signInButton
                #endif
            }
            .sheet(isPresented: $showingSignIn) {
                AuthView(mode: .signIn)
            }
        }
    }

    private var signInButton: some View {
        Button(L10n.t("action.signIn")) { showingSignIn = true }.buttonStyle(.snPrimary)
    }

    #if DEBUG
    private func signInWithTestTicket() async {
        guard LiveVerification.active, let ticket = LiveVerification.ticket, clerk.session == nil else {
            liveSignInFailed = true
            return
        }
        do {
            let attempt = try await clerk.auth.signInWithTicket(ticket)
            guard let sessionId = attempt.createdSessionId, attempt.status == .complete else {
                liveSignInFailed = true
                return
            }
            try await clerk.auth.setActive(sessionId: sessionId)
        } catch {
            liveSignInFailed = true
        }
    }
    #endif
}

@MainActor
private func signInCover<Content: View>(@ViewBuilder content: () -> Content) -> some View {
    VStack(spacing: 24) {
        Spacer()
        Image("BrandMark")
            .resizable()
            .scaledToFit()
            .frame(width: 88, height: 88)
            .accessibilityHidden(true)
        SnDisplay(text: "Sonereli", size: 44)
        Text(L10n.t("auth.tagline"))
            .font(SnFont.inter(18, .medium, relativeTo: .title3))
            .foregroundStyle(Color.snMuted)
        Spacer()
        content()
    }
    .multilineTextAlignment(.center)
    .padding(24)
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(Color.snBlackout.ignoresSafeArea())
}
