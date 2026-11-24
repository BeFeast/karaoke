import SwiftUI

struct SettingsView: View {
    let signOut: () async throws -> Void

    @Environment(Prefs.self) private var prefs
    @Environment(LibraryStore.self) private var library
    @Environment(AppRouter.self) private var router
    @State private var signOutFailed = false

    var body: some View {
        @Bindable var prefs = prefs
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                SnDisplay(text: L10n.t("tab.settings"), mic: true).padding(.top, 8)

                section(L10n.t("settings.singing")) {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(L10n.t("stage.size")).font(SnFont.label).foregroundStyle(Color.snText)
                        Picker(L10n.t("stage.size"), selection: $prefs.lyricsSize) {
                            ForEach(LyricsSize.allCases) { Text($0.label).tag($0) }
                        }
                        .pickerStyle(.segmented)
                        .accessibilityIdentifier("settings.size")
                        Text(L10n.t("settings.preview"))
                            .font(SnFont.inter(LyricsModel.fontSize(.active, size: prefs.lyricsSize, performance: false).size, .bold, relativeTo: .title))
                            .foregroundStyle(Color.snPink)
                            .lineLimit(2)
                            .animation(.snappy(duration: 0.2), value: prefs.lyricsSize)
                    }
                    .padding(.vertical, 12)
                    divider
                    HStack(spacing: 14) {
                        Image(systemName: "mic.fill")
                            .foregroundStyle(Color.snPink)
                            .frame(width: 44, height: 44)
                            .background(Circle().fill(Color.snRaised))
                        VStack(spacing: 4) {
                            HStack {
                                Text(L10n.t("settings.vocalsStart")).font(SnFont.label).foregroundStyle(Color.snText)
                                Spacer()
                                Text("\(prefs.vocalsDefault)%").font(SnFont.inter(15, .bold).monospacedDigit()).foregroundStyle(Color.snPink)
                            }
                            Slider(value: Binding(get: { Double(prefs.vocalsDefault) }, set: { prefs.vocalsDefault = Int($0.rounded()) }),
                                   in: 0...100, step: 5)
                                .tint(Color.snPink)
                                .accessibilityLabel(L10n.t("settings.vocalsStart"))
                                .accessibilityValue(L10n.t("stage.vocalsValue", ["n": prefs.vocalsDefault]))
                                .accessibilityIdentifier("settings.vocals")
                        }
                    }
                    .padding(.vertical, 12)
                    divider
                    toggle(L10n.t("settings.keepAwake"), isOn: $prefs.keepAwake)
                    divider
                    toggle(L10n.t("settings.countIn"), hint: L10n.t("settings.countInHint"), isOn: $prefs.countIn)
                    divider
                    toggle(L10n.t("settings.haptics"), isOn: $prefs.haptics)
                }

                section(L10n.t("settings.help")) {
                    link(L10n.t("settings.feedback"), icon: "flag", id: "settings.feedback") { router.openFeedback() }
                    divider
                    link(L10n.t("settings.youtube"), icon: "square.and.arrow.up", id: "settings.youtube") {
                        router.settingsPath.append(.youtubeHelp)
                    }
                }

                section(L10n.t("settings.account")) {
                    if let identity = library.identity {
                        Text(L10n.t("settings.signedIn", ["name": identity.label]))
                            .font(SnFont.body)
                            .foregroundStyle(Color.snMuted)
                            .padding(.vertical, 12)
                        divider
                    }
                    Button {
                        Task {
                            do { try await signOut() } catch { signOutFailed = true }
                        }
                    } label: {
                        Text(L10n.t("action.signOut"))
                            .font(SnFont.inter(16, .semibold))
                            .foregroundStyle(Color.snError)
                            .frame(maxWidth: .infinity, minHeight: 50, alignment: .leading)
                    }
                    .accessibilityIdentifier("settings.signOut")
                    if signOutFailed { Text(L10n.t("err.generic")).font(SnFont.caption).foregroundStyle(Color.snError) }
                }

                Text(L10n.t("settings.version", ["version": Self.version]))
                    .font(SnFont.caption)
                    .foregroundStyle(Color.snPast)
                    .frame(maxWidth: .infinity)
                    .padding(.top, 8)
                    .accessibilityIdentifier("settings.version")
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .snScreen()
        .toolbar(.hidden, for: .navigationBar)
    }

    static var version: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "?"
        let build = info?["CFBundleVersion"] as? String ?? "?"
        return "\(short) (\(build))"
    }

    private var divider: some View {
        Rectangle().fill(Color.snDivider).frame(height: 1)
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(SnFont.title).foregroundStyle(Color.snText).padding(.top, 12)
                .accessibilityAddTraits(.isHeader)
            VStack(alignment: .leading, spacing: 0) { content() }
                .padding(.horizontal, 16)
                .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.snSurface))
        }
    }

    private func toggle(_ title: String, hint: String? = nil, isOn: Binding<Bool>) -> some View {
        Toggle(isOn: isOn) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(SnFont.inter(16, .regular)).foregroundStyle(Color.snText)
                if let hint { Text(hint).font(SnFont.caption).foregroundStyle(Color.snMuted) }
            }
        }
        .tint(Color.snLime)
        .frame(minHeight: 54)
    }

    private func link(_ title: String, icon: String, id: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 14) {
                Image(systemName: icon).foregroundStyle(Color.snText).frame(width: 24)
                Text(title).font(SnFont.inter(16, .regular)).foregroundStyle(Color.snText)
                Spacer()
                Image(systemName: "chevron.forward").foregroundStyle(Color.snMuted)
            }
            .frame(minHeight: 54)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(id)
    }
}

struct YouTubeHelpView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                SnDisplay(text: L10n.t("settings.youtube"), size: 34)
                Text(L10n.t("settings.youtubeSteps"))
                    .font(SnFont.body)
                    .foregroundStyle(Color.snText)
                    .lineSpacing(6)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .snScreen()
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// Feedback: topic in one tap, a message, and what gets attached, shown
/// before sending (pomoi contract #344).
struct FeedbackView: View {
    @Environment(LibraryStore.self) private var library
    @Environment(PlayerSession.self) private var session
    @Environment(Prefs.self) private var prefs
    @Environment(AppRouter.self) private var router
    @State private var topic = "fb.app"
    @State private var message = ""
    @State private var attachState = true
    @State private var attachShot = false
    @State private var phase = Phase.normal
    @State private var issue: Int?

    private enum Phase { case normal, sending, sent, error }
    private struct Topic: Identifiable {
        let key: String
        let kind: String
        var id: String { key }
    }

    private static let topics = [
        Topic(key: "fb.timing", kind: "bug"), Topic(key: "fb.words", kind: "bug"), Topic(key: "fb.vocals", kind: "bug"),
        Topic(key: "fb.app", kind: "bug"), Topic(key: "fb.idea", kind: "fr"),
    ]

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    SnDisplay(text: L10n.t("fb.title"))
                    if phase == .sent {
                        VStack(spacing: 14) {
                            Image(systemName: "checkmark.circle").font(.system(size: 44)).foregroundStyle(Color.snSuccess)
                            Text(L10n.t("fb.sent")).font(SnFont.title).foregroundStyle(Color.snText)
                            if let issue {
                                Text(L10n.t("fb.sentLead", ["n": issue])).font(SnFont.body).foregroundStyle(Color.snMuted)
                            }
                            Button(L10n.t("fb.another")) { phase = .normal }.buttonStyle(.snSecondary)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 32)
                        .accessibilityIdentifier("feedback.sent")
                    } else {
                        form
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 16)
            }
            .scrollDismissesKeyboard(.interactively)
            if phase != .sent {
                Button(phase == .sending ? L10n.t("fb.sending") : L10n.t("fb.send")) { Task { await send() } }
                    .buttonStyle(.snPrimary)
                    .disabled(phase == .sending)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 12)
                    .accessibilityIdentifier("feedback.send")
            }
        }
        .snScreen()
        .navigationBarTitleDisplayMode(.inline)
        .sensoryFeedback(trigger: phase == .error) { _, failed in failed && prefs.haptics ? .error : nil }
    }

    private var form: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(L10n.t("fb.lead")).font(SnFont.body).foregroundStyle(Color.snMuted)
            ChipFlow(spacing: 8) {
                ForEach(Self.topics) { item in
                    let selected = topic == item.key
                    Button { topic = item.key } label: {
                        Text(L10n.t(item.key))
                            .font(SnFont.inter(15, .semibold))
                            .foregroundStyle(selected ? Color.snBlackout : Color.snText)
                            .padding(.horizontal, 14)
                            .frame(minHeight: 40)
                            .background(Capsule().fill(selected ? Color.snText : Color.clear))
                            .overlay(Capsule().strokeBorder(selected ? Color.clear : Color.snBoundary, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
            VStack(alignment: .leading, spacing: 8) {
                Text(L10n.t("fb.message")).font(SnFont.label).foregroundStyle(Color.snText)
                ZStack(alignment: .topLeading) {
                    if message.isEmpty {
                        Text(L10n.t("fb.placeholder")).font(SnFont.body).foregroundStyle(Color.snPast)
                            .padding(.horizontal, 14).padding(.vertical, 14)
                    }
                    TextEditor(text: $message)
                        .font(SnFont.body)
                        .foregroundStyle(Color.snText)
                        .scrollContentBackground(.hidden)
                        .padding(8)
                        .frame(minHeight: 130)
                        .accessibilityIdentifier("feedback.message")
                }
                .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.snSurface))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Color.snBoundary, lineWidth: 1))
            }
            VStack(spacing: 0) {
                Toggle(isOn: $attachState) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(L10n.t("fb.attachState")).font(SnFont.inter(16, .regular)).foregroundStyle(Color.snText)
                        Text(stateSummary).font(SnFont.caption).foregroundStyle(Color.snMuted)
                    }
                }
                .frame(minHeight: 58)
                Rectangle().fill(Color.snDivider).frame(height: 1)
                Toggle(isOn: $attachShot) {
                    Text(L10n.t("fb.attachShot")).font(SnFont.inter(16, .regular)).foregroundStyle(Color.snText)
                }
                .frame(minHeight: 54)
                .disabled(router.feedbackShot == nil)
            }
            .tint(Color.snLime)
            .padding(.horizontal, 16)
            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.snSurface))
            if phase == .error { SnNote(text: L10n.t("fb.error")).accessibilityIdentifier("feedback.error") }
        }
    }

    private var stateSummary: String {
        guard let song = session.song else { return L10n.t("fb.noSong") }
        let position = Clock.label(session.player?.position ?? 0)
        return "\(song.title) · \(position) · \(L10n.t("stage.vocals").lowercased()) \(session.vocalsPct)%"
    }

    private func send() async {
        guard phase != .sending else { return }
        phase = .sending
        let kind = Self.topics.first { $0.key == topic }?.kind ?? "bug"
        var meta: [String: Any] = [
            "version": SettingsView.version,
            "platform": "ios",
            "os": UIDevice.current.systemVersion,
            "device": UIDevice.current.model,
            "locale": Locale.preferredLanguages.first ?? "",
            "topic": L10n.english(topic),
        ]
        if attachState, let song = session.song {
            meta["player"] = [
                "title": song.title,
                "position_s": ((session.player?.position ?? 0) * 10).rounded() / 10,
                "vocals_pct": session.vocalsPct,
                "playing": session.player?.isPlaying ?? false,
                "catalog": song.isCatalogAsset,
            ] as [String: Any]
        }
        let report = FeedbackReport(
            text: Redact.text("[\(L10n.english(topic))] \(message)".trimmingCharacters(in: .whitespaces)),
            kind: kind, installId: prefs.installId, meta: meta,
            screenshot: attachShot ? router.feedbackShot : nil
        )
        do {
            issue = try await library.api.feedback(report)
            message = ""
            phase = .sent
        } catch {
            phase = .error
        }
    }
}

enum Redact {
    static func text(_ input: String) -> String {
        var text = input
        let patterns = [
            (#"[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[A-Za-z]{2,}"#, "[email]"),
            (#"(?:/share/)[^\s/?#]+|\b(?:ktx_|Bearer\s+)[\w.-]+"#, "[credential]"),
            (#"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b"#, "[credential]"),
        ]
        for (pattern, replacement) in patterns {
            text = text.replacingOccurrences(of: pattern, with: replacement, options: .regularExpression)
        }
        return text
    }
}

/// Wrapping row of chips.
struct ChipFlow: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0, widest: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0, x + size.width > width {
                y += row + spacing
                x = 0
                row = 0
            }
            x += size.width + spacing
            row = max(row, size.height)
            widest = max(widest, x - spacing)
        }
        return CGSize(width: min(widest, width), height: y + row)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, row: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX {
                y += row + spacing
                x = bounds.minX
                row = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            row = max(row, size.height)
        }
    }
}
