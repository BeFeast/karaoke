import Network
import SwiftUI
import UniformTypeIdentifiers

/// Add song: one field for a song link with Paste, a live check, an audio
/// upload as the alternative and a pinned Prepare song. A link the catalog
/// already has ready turns the action into Start singing (#277 reuse).
struct AddSongView: View {
    let request: AddRequest

    @Environment(LibraryStore.self) private var library
    @Environment(PlayerSession.self) private var session
    @Environment(AppRouter.self) private var router
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var file: URL?
    @State private var picking = false
    @State private var sending = false
    @State private var error: String?
    @State private var catalog: [CatalogSong] = []
    @State private var online = true
    @State private var failures = 0
    @FocusState private var focused: Bool

    private enum Link { case empty, youtube(URL), other(URL), invalid }

    private var link: Link {
        let raw = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if raw.isEmpty { return .empty }
        if let url = SharedVideo.validatedURL(from: raw) { return .youtube(url) }
        let candidate = raw.contains("://") ? raw : "https://\(raw)"
        if !raw.contains(" "), let url = URL(string: candidate), ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
           let host = url.host, host.contains(".") {
            return .other(url)
        }
        return .invalid
    }

    private var ready: CatalogSong? {
        guard file == nil, case let .youtube(url) = link, let id = YouTube.id(of: url.absoluteString) else { return nil }
        return catalog.first { $0.stageToken != nil && YouTube.id(of: $0.sourceUrl) == id }
    }

    private var canSubmit: Bool {
        guard !sending, online else { return false }
        if file != nil { return true }
        switch link {
        case .youtube, .other: return true
        default: return false
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button {
                    finish(nil)
                } label: {
                    Image(systemName: "xmark").font(.system(size: 20, weight: .semibold)).frame(width: 44, height: 44)
                }
                .foregroundStyle(Color.snText)
                .accessibilityLabel(L10n.t("action.close"))
                .accessibilityIdentifier("add.close")
                .disabled(sending)
                Spacer()
            }
            .padding(.horizontal, 8)

            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    SnDisplay(text: L10n.t("add.title"))
                    Text(L10n.t("add.lead")).font(SnFont.body).foregroundStyle(Color.snMuted)
                    if request.shared != nil {
                        Label(L10n.t("add.shared"), systemImage: "square.and.arrow.up")
                            .font(SnFont.label)
                            .foregroundStyle(Color.snCyan)
                    }
                    if let file {
                        fileRow(file)
                    } else {
                        linkField
                    }
                    if let ready {
                        readyCard(ready)
                    } else if request.shared == nil {
                        alternatives
                    }
                    if !online { SnNote(text: L10n.t("add.offline"), tone: .warning) }
                    if let error { SnNote(text: error).accessibilityIdentifier("add.error") }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
            .scrollDismissesKeyboard(.interactively)

            Button {
                Task { await submit() }
            } label: {
                if sending {
                    Text(L10n.t("add.sending"))
                } else if ready != nil {
                    Label(L10n.t("action.sing"), systemImage: "play.fill")
                } else {
                    Text(L10n.t("add.submit"))
                }
            }
            .buttonStyle(.snPrimary)
            .disabled(!canSubmit)
            .padding(.horizontal, 16)
            .padding(.bottom, 12)
            .accessibilityIdentifier("add.submit")
        }
        .snScreen()
        .interactiveDismissDisabled(sending || request.shared != nil)
        .sensoryFeedback(trigger: failures) { _, _ in .error }
        .fileImporter(isPresented: $picking, allowedContentTypes: [.mp3, .mpeg4Audio, .wav, .audio]) { result in
            if case let .success(url) = result {
                file = url
                error = nil
            }
        }
        .task {
            text = request.url
            if request.url.isEmpty { focused = true }
            if let page = try? await library.api.catalog(query: "", axis: nil, value: nil) {
                catalog = page.songs
            }
        }
        .task {
            for await state in NetworkStatus.updates() { online = state }
        }
    }

    private var linkField: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L10n.t("add.field")).font(SnFont.label).foregroundStyle(Color.snText)
            HStack(spacing: 10) {
                Image(systemName: "link").foregroundStyle(Color.snMuted)
                TextField("https://youtu.be/…", text: $text)
                    .font(SnFont.body)
                    .foregroundStyle(Color.snText)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .submitLabel(.go)
                    .focused($focused)
                    .onSubmit { Task { await submit() } }
                    .onChange(of: text) { _, _ in error = nil }
                    .accessibilityIdentifier("paste.url")
                    .disabled(sending)
                PasteButton(payloadType: String.self) { strings in
                    guard let value = strings.first?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return }
                    Task { @MainActor in text = value }
                }
                .labelStyle(.titleOnly)
                .buttonBorderShape(.capsule)
                .tint(Color.snRaised)
                .disabled(sending)
            }
            .padding(.leading, 14)
            .padding(.trailing, 8)
            .frame(minHeight: 54)
            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.snSurface))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(isInvalid ? Color.snError : Color.snBoundary, lineWidth: 1)
            )
            linkMessage
        }
    }

    private var isInvalid: Bool {
        if case .invalid = link { return true }
        return false
    }

    @ViewBuilder
    private var linkMessage: some View {
        switch link {
        case .youtube:
            Label(L10n.t("add.valid"), systemImage: "checkmark").font(SnFont.inter(14, .medium)).foregroundStyle(Color.snSuccess)
                .accessibilityIdentifier("add.valid")
        case .other:
            Label(L10n.t("add.validOther"), systemImage: "checkmark").font(SnFont.inter(14, .medium)).foregroundStyle(Color.snSuccess)
        case .invalid:
            Label(L10n.t("add.invalid"), systemImage: "exclamationmark.circle").font(SnFont.inter(14, .medium)).foregroundStyle(Color.snError)
        case .empty:
            EmptyView()
        }
    }

    private var alternatives: some View {
        VStack(spacing: 16) {
            HStack(spacing: 12) {
                Rectangle().fill(Color.snDivider).frame(height: 1)
                Text(L10n.t("add.or")).font(SnFont.caption).foregroundStyle(Color.snMuted)
                Rectangle().fill(Color.snDivider).frame(height: 1)
            }
            if file != nil {
                Button { file = nil } label: { Label(L10n.t("add.linkInstead"), systemImage: "link") }
                    .buttonStyle(.snSecondary)
            } else {
                Button { picking = true } label: { Label(L10n.t("add.upload"), systemImage: "square.and.arrow.up") }
                    .buttonStyle(.snSecondary)
                    .disabled(sending)
                Text(L10n.t("add.uploadHint")).font(SnFont.caption).foregroundStyle(Color.snMuted)
            }
            Button { router.add = nil; router.tab = .settings; router.settingsPath = [.youtubeHelp] } label: {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: "square.and.arrow.up").foregroundStyle(Color.snMuted)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(L10n.t("add.youtube")).font(SnFont.inter(15, .semibold)).foregroundStyle(Color.snText)
                        Text(L10n.t("add.youtubeHint")).font(SnFont.inter(14, .regular)).foregroundStyle(Color.snMuted)
                            .multilineTextAlignment(.leading)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: "chevron.forward").foregroundStyle(Color.snMuted)
                }
                .padding(14)
                .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.snSurface))
            }
            .buttonStyle(.plain)
        }
    }

    private func fileRow(_ url: URL) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "music.note").foregroundStyle(Color.snMuted)
            Text(url.lastPathComponent).font(SnFont.headline).foregroundStyle(Color.snText).lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button { file = nil } label: { Image(systemName: "xmark").frame(width: 44, height: 44) }
                .foregroundStyle(Color.snMuted)
                .accessibilityLabel(L10n.t("add.remove"))
        }
        .padding(.leading, 14)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.snSurface))
    }

    private func readyCard(_ song: CatalogSong) -> some View {
        HStack(spacing: 12) {
            SongCover(title: song.title, url: song.coverURL)
            VStack(alignment: .leading, spacing: 2) {
                Text(L10n.t("add.ready").uppercased()).font(SnFont.eyebrow).tracking(1).foregroundStyle(Color.snSuccess)
                Text(song.title).font(SnFont.headline).foregroundStyle(Color.snText)
                Text(song.artist).font(SnFont.inter(14, .regular)).foregroundStyle(Color.snMuted)
                Text(L10n.t("add.readyLead")).font(SnFont.caption).foregroundStyle(Color.snMuted)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.snSurface))
        .accessibilityIdentifier("add.alreadyReady")
    }

    private func submit() async {
        guard canSubmit else { return }
        sending = true
        error = nil
        do {
            let job: SongJob
            if let file {
                job = try await library.upload(file)
            } else {
                switch link {
                case let .youtube(url), let .other(url): job = try await library.submit(url)
                default:
                    sending = false
                    return
                }
            }
            finish(job)
        } catch let failure as URLError where failure.code != .notConnectedToInternet {
            // The POST may have landed: never resend automatically.
            error = L10n.t("err.ambiguous")
            failures += 1
            sending = false
        } catch {
            self.error = error.localizedDescription
            failures += 1
            sending = false
        }
    }

    /// Processing replaces Add; a catalog-ready song opens straight on the Stage.
    private func finish(_ job: SongJob?) {
        if let shared = request.shared { ShareInbox.remove(shared) }
        router.add = nil
        guard let job else { return }
        if job.status == .completed {
            session.open(job.song, api: library.api)
        } else {
            router.showProcessing(job.id)
        }
    }
}

enum NetworkStatus {
    static func updates() -> AsyncStream<Bool> {
        AsyncStream { continuation in
            let monitor = NWPathMonitor()
            monitor.pathUpdateHandler = { path in continuation.yield(path.status == .satisfied) }
            continuation.onTermination = { _ in monitor.cancel() }
            monitor.start(queue: DispatchQueue(label: "sonereli.network"))
        }
    }
}
