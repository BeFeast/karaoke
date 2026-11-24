import SwiftUI

/// Start screen: Continue singing, songs being prepared, songs that need
/// attention and the ready list. Add song stays pinned in every state.
struct MySongsView: View {
    @Environment(LibraryStore.self) private var library
    @Environment(PlayerSession.self) private var session
    @Environment(Prefs.self) private var prefs
    @Environment(AppRouter.self) private var router
    @State private var removing: SongJob?

    var body: some View {
        List {
            SnDisplay(text: L10n.t("tab.mySongs"), mic: true)
                .padding(.top, 8)
                .plainRow()

            switch library.phase {
            case .loading where library.jobs.isEmpty:
                ForEach(0..<5, id: \.self) { _ in SkeletonRow().plainRow() }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(L10n.t("home.loading"))
            case let .failed(message) where library.jobs.isEmpty:
                VStack(alignment: .leading, spacing: 12) {
                    SnNote(text: message)
                    Button(L10n.t("action.retry")) { Task { await library.refresh() } }
                        .buttonStyle(.snSecondary)
                }
                .plainRow()
            default:
                content
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .snScreen()
        .toolbar(.hidden, for: .navigationBar)
        .refreshable { await library.refresh() }
        .confirmationDialog(L10n.t("home.removeTitle"), isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
                            titleVisibility: .visible, presenting: removing) { job in
            Button(L10n.t("action.remove"), role: .destructive) { remove(job) }
        } message: { job in
            Text(L10n.t("home.removeBody", ["title": job.name]))
        }
    }

    @ViewBuilder
    private var content: some View {
        if library.jobs.isEmpty {
            EmptyLibrary { router.tab = .catalog }.plainRow()
        } else {
            if let last = continueSong {
                ContinueCard(last: last) {
                    session.open(last.song, api: library.api, at: last.position, autoplay: true)
                }
                .plainRow()
                .padding(.bottom, 8)
            }
            let preparing = library.preparing
            if !preparing.isEmpty {
                Section {
                    ForEach(preparing) { job in
                        Button { router.push(.processing(job.id)) } label: { PreparingRow(job: job) }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("songs.job.\(job.id)")
                            .songRow()
                    }
                } header: { sectionHeader(L10n.t("home.preparing")) }
            }
            let attention = library.attention
            if !attention.isEmpty {
                Section {
                    ForEach(attention) { job in
                        AttentionRow(job: job, retry: { retry(job) }, remove: { removing = job })
                            .accessibilityIdentifier("songs.job.\(job.id)")
                            .songRow()
                    }
                } header: { sectionHeader(L10n.t("home.attention")) }
            }
            let ready = library.ready(playedAt: prefs.playedAt)
            if !ready.isEmpty {
                Section {
                    ForEach(ready) { job in
                        Button { session.open(job.song, api: library.api, autoplay: false) } label: { ReadyRow(job: job) }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("songs.job.\(job.id)")
                            .songRow()
                            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                Button(L10n.t("action.remove"), systemImage: "trash") { removing = job }
                                    .tint(Color.snError)
                            }
                    }
                } header: { sectionHeader(L10n.t("home.ready"), count: ready.count) }
            }
            if library.hasMore {
                Button(L10n.t("home.more")) { Task { await library.loadMore() } }
                    .buttonStyle(.snSecondary)
                    .disabled(library.loadingMore)
                    .plainRow()
            }
        }
    }

    /// The last song sung, unless it is already in the mini-player or gone.
    private var continueSong: LastPlayed? {
        guard session.song == nil, let last = prefs.lastPlayed, last.position > 1,
              last.position < last.duration - 3 else { return nil }
        if last.song.isCatalogAsset { return last }
        return library.jobs.contains { $0.jobToken == last.song.token && $0.status == .completed } ? last : nil
    }

    private func sectionHeader(_ title: String, count: Int? = nil) -> some View {
        HStack(spacing: 8) {
            Text(title).font(SnFont.title).foregroundStyle(Color.snText)
            if let count { Text("\(count)").font(SnFont.label).foregroundStyle(Color.snMuted) }
        }
        .padding(.top, 16)
        .padding(.bottom, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 0, trailing: 16))
        .accessibilityAddTraits(.isHeader)
    }

    private func retry(_ job: SongJob) {
        guard let url = job.retryURL else {
            router.add = AddRequest()
            return
        }
        Task {
            if let fresh = try? await library.submit(url) {
                try? await library.remove(job.id)
                router.push(.processing(fresh.id))
            }
        }
    }

    private func remove(_ job: SongJob) {
        if session.song?.token == job.jobToken { session.close() }
        prefs.forget(token: job.jobToken)
        Task { try? await library.remove(job.id) }
    }
}

private extension View {
    func plainRow() -> some View {
        listRowInsets(EdgeInsets(top: 4, leading: 16, bottom: 4, trailing: 16))
            .listRowBackground(Color.clear)
            .listRowSeparator(.hidden)
    }

    func songRow() -> some View {
        listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 0, trailing: 16))
            .listRowBackground(Color.snBlackout)
            .listRowSeparatorTint(Color.snDivider)
            .alignmentGuide(.listRowSeparatorLeading) { _ in 0 }
    }
}

private struct ContinueCard: View {
    let last: LastPlayed
    let play: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L10n.t("home.continue").uppercased())
                .font(SnFont.eyebrow)
                .tracking(1)
                .foregroundStyle(Color.snMuted)
            HStack(spacing: 14) {
                SongCover(title: last.song.title, url: last.song.coverURL, size: 64, radius: 6)
                VStack(alignment: .leading, spacing: 4) {
                    Text(last.song.title).font(SnFont.inter(20, .semibold, relativeTo: .title3)).foregroundStyle(Color.snText).lineLimit(1)
                    if let artist = last.song.artist {
                        Text(artist).font(SnFont.inter(15, .regular)).foregroundStyle(Color.snMuted).lineLimit(1)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Button(action: play) {
                    Image(systemName: "play.fill")
                        .font(.system(size: 22, weight: .bold))
                        .foregroundStyle(Color.snBlackout)
                        .frame(width: 56, height: 56)
                        .background(Circle().fill(Color.snLime))
                }
                .accessibilityLabel(L10n.t("home.resume", ["title": last.song.title, "time": Clock.label(last.position)]))
                .accessibilityIdentifier("home.continue")
            }
            HStack(spacing: 12) {
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Color.snTrack)
                        Capsule().fill(Color.snCyan).frame(width: geo.size.width * min(1, last.position / max(1, last.duration)))
                    }
                }
                .frame(height: 4)
                Text(Clock.label(last.position)).font(SnFont.caption.monospacedDigit()).foregroundStyle(Color.snCyan)
                Text("/ \(Clock.label(last.duration))").font(SnFont.caption.monospacedDigit()).foregroundStyle(Color.snMuted)
            }
            .environment(\.layoutDirection, .leftToRight)
        }
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.snSurface))
    }
}

private struct PreparingRow: View {
    let job: SongJob

    var body: some View {
        HStack(spacing: 12) {
            SongCover(title: job.name, url: job.song.coverURL)
            VStack(alignment: .leading, spacing: 4) {
                Text(job.name).font(SnFont.headline).foregroundStyle(Color.snText).lineLimit(1)
                Text(job.status == .queued ? job.status.title : "\(job.status.title) · \(job.progress)%")
                    .font(SnFont.inter(14, .medium))
                    .foregroundStyle(Color.snCyan)
                    .monospacedDigit()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Image(systemName: "chevron.forward").foregroundStyle(Color.snMuted)
        }
        .frame(minHeight: 68)
        .contentShape(Rectangle())
    }
}

private struct ReadyRow: View {
    let job: SongJob

    var body: some View {
        HStack(spacing: 12) {
            SongCover(title: job.name, url: job.song.coverURL)
            VStack(alignment: .leading, spacing: 4) {
                Text(job.name).font(SnFont.headline).foregroundStyle(Color.snText).lineLimit(1)
                Text([job.artist, job.duration.map(Clock.label)].compactMap { $0 }.joined(separator: " · "))
                    .font(SnFont.inter(14, .regular))
                    .foregroundStyle(Color.snMuted)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Image(systemName: "play.fill")
                .font(.system(size: 18))
                .foregroundStyle(Color.snMuted)
                .frame(width: 44, height: 44)
        }
        .frame(minHeight: 68)
        .contentShape(Rectangle())
    }
}

private struct AttentionRow: View {
    let job: SongJob
    let retry: () -> Void
    let remove: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                SongCover(title: job.name, url: job.song.coverURL)
                VStack(alignment: .leading, spacing: 4) {
                    Text(job.name).font(SnFont.headline).foregroundStyle(Color.snText).lineLimit(1)
                    Text(job.status == .cancelled ? L10n.t("home.cancelled") : L10n.t("home.couldntPrepare"))
                        .font(SnFont.inter(14, .medium))
                        .foregroundStyle(job.status == .cancelled ? Color.snMuted : Color.snError)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack(spacing: 12) {
                Button(L10n.t("action.retry"), action: retry).buttonStyle(SnSecondaryButtonStyle(height: 44))
                Button(L10n.t("action.remove"), action: remove).buttonStyle(SnSecondaryButtonStyle(height: 44))
            }
        }
        .padding(.vertical, 10)
    }
}

private struct EmptyLibrary: View {
    let browse: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "music.mic").font(.system(size: 44)).foregroundStyle(Color.snPink)
            Text(L10n.t("home.emptyTitle")).font(SnFont.title).foregroundStyle(Color.snText)
            Text(L10n.t("home.emptyLead"))
                .font(SnFont.body)
                .foregroundStyle(Color.snMuted)
                .multilineTextAlignment(.center)
            Button(L10n.t("home.browse"), action: browse).buttonStyle(.snSecondary)
        }
        .padding(.vertical, 48)
        .frame(maxWidth: .infinity)
    }
}

struct SkeletonRow: View {
    var body: some View {
        HStack(spacing: 12) {
            RoundedRectangle(cornerRadius: 6).fill(Color.snSurface).frame(width: 48, height: 48)
            VStack(alignment: .leading, spacing: 8) {
                RoundedRectangle(cornerRadius: 4).fill(Color.snSurface).frame(width: 160, height: 14)
                RoundedRectangle(cornerRadius: 4).fill(Color.snSurface).frame(width: 100, height: 12)
            }
            Spacer()
        }
        .frame(height: 68)
    }
}
