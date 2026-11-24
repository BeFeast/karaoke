import ClerkKit
import ClerkKitUI
import SwiftUI

@main
struct SonereliApp: App {
    init() {
        SongAssets.clearPreviousRun()
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
                .tint(SonereliColor.pink)
        }
    }
}

enum SonereliColor {
    static let background = Color(red: 8 / 255, green: 9 / 255, blue: 13 / 255)
    static let pink = Color(red: 1, green: 45 / 255, blue: 170 / 255)
}

private struct RootView: View {
    @State private var clerk: Clerk?
    @State private var connectionError: String?
    @State private var attempt = 0
    #if DEBUG
    @State private var fixtureSignedIn = false
    private let useFixture = ProcessInfo.processInfo.arguments.contains("--ui-test-fixture")
    #endif

    var body: some View {
        #if DEBUG
        if useFixture {
            if fixtureSignedIn {
                SongsView(api: FixtureCoordinator(), signOut: { fixtureSignedIn = false })
            } else {
                signInCover {
                    Button("Войти", action: { fixtureSignedIn = true })
                        .buttonStyle(.borderedProminent)
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
                        Button("Повторить") {
                            self.connectionError = nil
                            attempt += 1
                        }
                        .buttonStyle(.borderedProminent)
                    } else {
                        ProgressView("Подключение")
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
                connectionError = "Не удалось подключиться к Sonereli. Проверьте сеть."
            }
        }
    }
}

private struct ClerkGate: View {
    @Environment(Clerk.self) private var clerk
    @State private var showingSignIn = false
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
            SongsView(api: Coordinator(), signOut: { try await clerk.auth.signOut() })
                .id(user.id)
        } else {
            signInCover {
                #if DEBUG
                if LiveVerification.active {
                    if liveSignInFailed { Text("Не удалось войти в тестовый аккаунт.") }
                    Button("Войти") { Task { await signInWithTestTicket() } }
                        .buttonStyle(.borderedProminent)
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
        Button("Войти") { showingSignIn = true }.buttonStyle(.borderedProminent)
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
        Image("BrandMark")
            .resizable()
            .scaledToFit()
            .frame(width: 80, height: 80)
            .accessibilityHidden(true)
        Text("Sonereli").font(.largeTitle.bold())
        Text("Пойте под любимые песни")
            .font(.title3)
            .foregroundStyle(.secondary)
        content()
    }
    .multilineTextAlignment(.center)
    .padding(24)
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(SonereliColor.background.ignoresSafeArea())
}
