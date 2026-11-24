import Observation
import SwiftUI

/// Catalog: search (always visible), shelves and one-tap Prepare (no
/// confirmation, Oleg 2026-11-24). A card being prepared opens Processing.
struct CatalogView: View {
    @Environment(LibraryStore.self) private var library
    @Environment(AppRouter.self) private var router
    @State private var query = ""
    @State private var page: CatalogPage?
    @State private var failed = false
    @State private var attempt = 0
    @State private var actions = CatalogActions()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                SnDisplay(text: L10n.t("tab.catalog"), mic: true)
                    .padding(.top, 8)
                    .padding(.horizontal, 16)
                SearchField(text: $query)
                    .padding(.horizontal, 16)
                if actions.prepareFailed { SnNote(text: L10n.t("err.prepare")).padding(.horizontal, 16) }
                results
            }
            .padding(.bottom, 16)
        }
        .scrollDismissesKeyboard(.immediately)
        .snScreen()
        .toolbar(.hidden, for: .navigationBar)
        .task(id: "\(query.trimmingCharacters(in: .whitespaces))|\(attempt)") {
            let search = query.trimmingCharacters(in: .whitespaces)
            if !search.isEmpty { try? await Task.sleep(for: .milliseconds(180)) }
            guard !Task.isCancelled else { return }
            failed = false
            do {
                let result = try await library.api.catalog(query: search, axis: nil, value: nil)
                guard !Task.isCancelled else { return }
                page = result
            } catch is CancellationError {
                return
            } catch {
                if !Task.isCancelled { failed = true }
            }
        }
    }

    @ViewBuilder
    private var results: some View {
        if failed {
            VStack(spacing: 12) {
                SnNote(text: L10n.t("catalog.error"))
                Button(L10n.t("action.retry")) { attempt += 1 }.buttonStyle(.snSecondary)
            }
            .padding(.horizontal, 16)
        } else if let page {
            let search = query.trimmingCharacters(in: .whitespaces)
            if page.songs.isEmpty {
                VStack(spacing: 14) {
                    Image(systemName: "magnifyingglass").font(.system(size: 32)).foregroundStyle(Color.snMuted)
                    Text(search.isEmpty ? L10n.t("catalog.empty") : L10n.t("catalog.noResults"))
                        .font(SnFont.title).foregroundStyle(Color.snText)
                    if !search.isEmpty {
                        Text(L10n.t("catalog.noResultsLead")).font(SnFont.body).foregroundStyle(Color.snMuted)
                            .multilineTextAlignment(.center)
                    }
                    Button { router.add = AddRequest() } label: { Label(L10n.t("action.add"), systemImage: "plus") }
                        .buttonStyle(.snSecondary)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 32)
                .accessibilityIdentifier("catalog.noResults")
            } else if !search.isEmpty {
                Text(page.songs.count == 1 ? L10n.t("catalog.countOne") : L10n.t("catalog.count", ["n": page.songs.count]))
                    .font(SnFont.caption).foregroundStyle(Color.snMuted)
                    .padding(.horizontal, 16)
                LazyVStack(spacing: 0) {
                    ForEach(page.songs) { song in
                        CatalogRow(song: song, actions: actions)
                            .padding(.horizontal, 16)
                        Rectangle().fill(Color.snDivider).frame(height: 1).padding(.leading, 76)
                    }
                }
            } else {
                ForEach(Shelf.all(in: page), id: \.self) { shelf in
                    ShelfSection(shelf: shelf, songs: shelf.songs(in: page), actions: actions)
                }
            }
        } else {
            ForEach(0..<2, id: \.self) { _ in
                VStack(alignment: .leading, spacing: 12) {
                    RoundedRectangle(cornerRadius: 4).fill(Color.snSurface).frame(width: 140, height: 18)
                    HStack(spacing: 12) {
                        ForEach(0..<3, id: \.self) { _ in
                            RoundedRectangle(cornerRadius: 14).fill(Color.snSurface).frame(width: 148, height: 210)
                        }
                    }
                }
                .padding(.leading, 16)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(L10n.t("catalog.loading"))
        }
    }
}

/// Shared card state: prepares in flight and the last Prepare error.
@MainActor
@Observable
final class CatalogActions {
    var pending: Set<Int> = []
    var prepareFailed = false
}

struct Shelf: Hashable {
    let axis: String
    let value: String

    var title: String {
        switch axis {
        case "decade": return "\(value)s"
        case "genre": return ["pop": "Pop", "hip-hop": "Hip hop", "mizrahi": "Mizrahi"][value] ?? value.capitalized
        default: return value
        }
    }

    func songs(in page: CatalogPage) -> [CatalogSong] {
        page.songs.filter { song in
            switch axis {
            case "collection": return song.collections.contains(value)
            case "genre": return song.genres.contains(value)
            case "decade": return song.decade.map(String.init) == value
            default: return false
            }
        }
    }

    /// Hebrew hits and English hits first, then other collections; genre
    /// and decade shelves appear from four songs (#277).
    static func all(in page: CatalogPage) -> [Shelf] {
        var values = ["Hebrew hits", "English hits"]
        for key in (page.facets["collection"] ?? [:]).keys.sorted() where !values.contains(key) { values.append(key) }
        var shelves = values.map { Shelf(axis: "collection", value: $0) }
        for axis in ["genre", "decade"] {
            for (value, count) in (page.facets[axis] ?? [:]).sorted(by: { $0.key < $1.key }) where count >= 4 {
                shelves.append(Shelf(axis: axis, value: value))
            }
        }
        return shelves.filter { !$0.songs(in: page).isEmpty }
    }
}

private struct ShelfSection: View {
    let shelf: Shelf
    let songs: [CatalogSong]
    let actions: CatalogActions
    @Environment(AppRouter.self) private var router

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(shelf.title).font(SnFont.title).foregroundStyle(Color.snText)
                Spacer()
                Button {
                    router.push(.shelf(axis: shelf.axis, value: shelf.value, title: shelf.title))
                } label: {
                    HStack(spacing: 4) {
                        Text(L10n.t("catalog.seeAll"))
                        Image(systemName: "chevron.forward").font(.system(size: 13, weight: .semibold))
                    }
                    .font(SnFont.inter(15, .semibold))
                    .foregroundStyle(Color.snMuted)
                    .frame(minHeight: 44)
                }
            }
            .padding(.horizontal, 16)
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 12) {
                    ForEach(songs.prefix(8)) { song in
                        CatalogCard(song: song, actions: actions, showVersion: Self.duplicated(song, in: songs))
                    }
                }
                .padding(.horizontal, 16)
            }
        }
        .padding(.top, 8)
        .accessibilityIdentifier("catalog.shelf.\(shelf.value)")
    }

    /// The recording version shows only when one shelf holds two recordings of a song.
    static func duplicated(_ song: CatalogSong, in songs: [CatalogSong]) -> Bool {
        songs.filter { $0.title.lowercased() == song.title.lowercased() && $0.artist.lowercased() == song.artist.lowercased() }.count > 1
    }
}

struct ShelfGridView: View {
    let axis: String
    let value: String
    let title: String
    @Environment(LibraryStore.self) private var library
    @State private var page: CatalogPage?
    @State private var failed = false
    @State private var actions = CatalogActions()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                SnDisplay(text: title, size: 34)
                if failed { SnNote(text: L10n.t("catalog.error")) }
                if actions.prepareFailed { SnNote(text: L10n.t("err.prepare")) }
                if let page {
                    LazyVGrid(columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)], alignment: .leading, spacing: 20) {
                        ForEach(page.songs) { song in
                            CatalogCard(song: song, actions: actions, width: nil,
                                        showVersion: ShelfSection.duplicated(song, in: page.songs))
                        }
                    }
                } else if !failed {
                    ProgressView().tint(Color.snCyan).frame(maxWidth: .infinity)
                }
            }
            .padding(16)
        }
        .snScreen()
        .navigationBarTitleDisplayMode(.inline)
        .task {
            do { page = try await library.api.catalog(query: "", axis: axis, value: value) } catch { failed = true }
        }
    }
}

private enum CardState: Equatable {
    case ready, prepare, preparing(job: Int?, pct: Int)
}

@MainActor
private enum CardModel {
    static func state(of song: CatalogSong, library: LibraryStore, actions: CatalogActions) -> CardState {
        if song.stageToken != nil { return .ready }
        let id = YouTube.id(of: song.sourceUrl)
        if let id, let job = library.jobs.first(where: { $0.status.isActive && YouTube.id(of: $0.sourceUrl) == id }) {
            return .preparing(job: job.id, pct: job.progress)
        }
        return actions.pending.contains(song.id) ? .preparing(job: nil, pct: 0) : .prepare
    }
}

/// Tap behaviour shared by cards and rows.
private struct CatalogTap: ViewModifier {
    let song: CatalogSong
    let state: CardState
    let actions: CatalogActions
    @Environment(LibraryStore.self) private var library
    @Environment(PlayerSession.self) private var session
    @Environment(AppRouter.self) private var router

    func body(content: Content) -> some View {
        Button {
            switch state {
            case .ready:
                if let ref = song.song { session.open(ref, api: library.api) }
            case let .preparing(job, _):
                if let job { router.push(.processing(job)) }
            case .prepare:
                prepare()
            }
        } label: { content }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
        .accessibilityIdentifier("catalog.song.\(song.id)")
    }

    private var label: String {
        let vars: [String: CustomStringConvertible] = ["title": song.title, "artist": song.artist]
        switch state {
        case .ready: return L10n.t("catalog.singLabel", vars)
        case .prepare: return L10n.t("catalog.prepareLabel", vars)
        case .preparing: return "\(song.title), \(L10n.t("catalog.queued"))"
        }
    }

    private func prepare() {
        guard !actions.pending.contains(song.id), let url = URL(string: song.sourceUrl) else { return }
        actions.pending.insert(song.id)
        actions.prepareFailed = false
        Task {
            do {
                let job = try await library.submit(url)
                if job.status == .completed {
                    actions.pending.remove(song.id)
                }
            } catch {
                actions.pending.remove(song.id)
                actions.prepareFailed = true
            }
        }
    }
}

private struct StateLabel: View {
    let state: CardState

    var body: some View {
        switch state {
        case .ready:
            HStack(spacing: 6) {
                Circle().fill(Color.snSuccess).frame(width: 8, height: 8)
                Text(L10n.t("catalog.ready")).font(SnFont.inter(14, .semibold)).foregroundStyle(Color.snSuccess)
            }
        case .prepare:
            Text(L10n.t("catalog.prepare"))
                .font(SnFont.inter(14, .semibold))
                .foregroundStyle(Color.snText)
                .padding(.horizontal, 14)
                .frame(minHeight: 30)
                .overlay(Capsule().strokeBorder(Color.snBoundary, lineWidth: 1))
        case let .preparing(job, pct):
            Text(job != nil && pct > 0 ? L10n.t("catalog.preparing", ["pct": pct]) : L10n.t("catalog.queued"))
                .font(SnFont.inter(14, .semibold).monospacedDigit())
                .foregroundStyle(Color.snCyan)
        }
    }
}

private struct CatalogCard: View {
    let song: CatalogSong
    let actions: CatalogActions
    var width: CGFloat? = 148
    var showVersion = false
    @Environment(LibraryStore.self) private var library

    var body: some View {
        let state = CardModel.state(of: song, library: library, actions: actions)
        VStack(alignment: .leading, spacing: 6) {
            ZStack(alignment: .bottomTrailing) {
                GeometryReader { geo in
                    SongCover(title: song.title, url: song.coverURL, size: geo.size.width, radius: 14)
                }
                .aspectRatio(1, contentMode: .fit)
                if state == .ready {
                    Image(systemName: "play.fill")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundStyle(Color.snBlackout)
                        .frame(width: 40, height: 40)
                        .background(Circle().fill(Color.snLime))
                        .padding(8)
                }
            }
            Text(song.title)
                .font(SnFont.inter(16, .semibold))
                .foregroundStyle(Color.snText)
                .lineLimit(2, reservesSpace: false)
                .multilineTextAlignment(.leading)
            Text(showVersion ? "\(song.artist) · \(song.version)" : song.artist)
                .font(SnFont.inter(14, .regular))
                .foregroundStyle(Color.snMuted)
                .lineLimit(1)
            StateLabel(state: state).padding(.top, 2)
        }
        .frame(width: width, alignment: .leading)
        .frame(maxWidth: width == nil ? .infinity : nil, alignment: .leading)
        .contentShape(Rectangle())
        .modifier(CatalogTap(song: song, state: state, actions: actions))
    }
}

private struct CatalogRow: View {
    let song: CatalogSong
    let actions: CatalogActions
    @Environment(LibraryStore.self) private var library

    var body: some View {
        let state = CardModel.state(of: song, library: library, actions: actions)
        HStack(spacing: 12) {
            SongCover(title: song.title, url: song.coverURL)
            VStack(alignment: .leading, spacing: 4) {
                Text(song.title).font(SnFont.headline).foregroundStyle(Color.snText).lineLimit(1)
                Text("\(song.artist) · \(song.version)").font(SnFont.inter(14, .regular)).foregroundStyle(Color.snMuted).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            StateLabel(state: state)
        }
        .frame(minHeight: 68)
        .contentShape(Rectangle())
        .modifier(CatalogTap(song: song, state: state, actions: actions))
    }
}

private struct SearchField: View {
    @Binding var text: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass").foregroundStyle(Color.snMuted)
            TextField(L10n.t("catalog.placeholder"), text: $text)
                .font(SnFont.body)
                .foregroundStyle(Color.snText)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .submitLabel(.search)
                .accessibilityIdentifier("catalog.search")
            if !text.isEmpty {
                Button { text = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(Color.snMuted) }
                    .accessibilityLabel(L10n.t("action.close"))
            }
        }
        .padding(.horizontal, 14)
        .frame(minHeight: 50)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.snSurface))
    }
}
