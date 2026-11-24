import Foundation
import Observation

/// The owner's songs and their live preparation state. One watcher per
/// in-flight job: WebSocket first, `/status` polling as the fallback.
@MainActor
@Observable
final class LibraryStore {
    enum Phase: Equatable { case loading, loaded, failed(String) }

    let api: any SongsAPI
    private(set) var identity: Identity?
    private(set) var jobs: [SongJob] = []
    private(set) var phase: Phase = .loading
    private(set) var hasMore = false
    private(set) var loadingMore = false
    /// Jobs whose socket dropped: when the last update arrived.
    private(set) var reconnecting: [Int: Date] = [:]
    /// Jobs that finished while being watched on the WebSocket.
    private(set) var completedOnSocket: Set<Int> = []
    /// Bumped when a watched job completes (success haptic, notification).
    private(set) var completions = 0

    @ObservationIgnored private var watchers: [Int: Task<Void, Never>] = [:]
    @ObservationIgnored private var active = true
    @ObservationIgnored private var refreshing = false

    init(api: any SongsAPI) {
        self.api = api
    }

    func job(_ id: Int) -> SongJob? { jobs.first { $0.id == id } }

    var preparing: [SongJob] { jobs.filter { $0.status.isActive } }
    var attention: [SongJob] { jobs.filter { $0.status == .failed || $0.status == .cancelled } }

    /// Ready songs: most recently sung first, then by completion order.
    func ready(playedAt: [String: Date]) -> [SongJob] {
        let completed = jobs.filter { $0.status == .completed }
        let order = Dictionary(uniqueKeysWithValues: completed.enumerated().map { ($1.id, $0) })
        return completed.sorted { a, b in
            let pa = playedAt[a.jobToken], pb = playedAt[b.jobToken]
            if let pa, let pb, pa != pb { return pa > pb }
            if (pa == nil) != (pb == nil) { return pa != nil }
            return order[a.id]! < order[b.id]!
        }
    }

    func refresh() async {
        guard !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        if jobs.isEmpty { phase = .loading }
        do {
            let owner = try await api.identity()
            guard owner.state == "clerk_user" else { throw CoordinatorError.missingSession }
            identity = owner
            let current = try await api.jobs(before: nil)
            try Task.checkCancellation()
            jobs = current
            hasMore = current.count == Coordinator.pageSize
            phase = .loaded
            watchActive()
        } catch is CancellationError {
            return
        } catch {
            if jobs.isEmpty { phase = .failed(L10n.t("home.error")) }
        }
    }

    func loadMore() async {
        guard !loadingMore, let last = jobs.last?.id else { return }
        loadingMore = true
        defer { loadingMore = false }
        if let page = try? await api.jobs(before: last) {
            let existing = Set(jobs.map(\.id))
            jobs.append(contentsOf: page.filter { !existing.contains($0.id) })
            hasMore = page.count == Coordinator.pageSize
        }
    }

    /// One POST per call; a transport error is never retried automatically.
    func submit(_ url: URL) async throws -> SongJob {
        let job = try await api.submit(url)
        upsert(job)
        watch(job.id)
        return job
    }

    func upload(_ file: URL) async throws -> SongJob {
        let job = try await api.upload(file)
        upsert(job)
        watch(job.id)
        return job
    }

    func cancel(_ id: Int) async {
        guard let job = try? await api.cancel(id) else { return }
        upsert(job)
    }

    func remove(_ id: Int) async throws {
        try await api.delete(id)
        watchers.removeValue(forKey: id)?.cancel()
        jobs.removeAll { $0.id == id }
    }

    func upsert(_ job: SongJob) {
        if let index = jobs.firstIndex(where: { $0.id == job.id }) {
            jobs[index] = job
        } else {
            jobs.insert(job, at: 0)
        }
    }

    /// Watchers live only while the app is in the foreground.
    func setActive(_ isActive: Bool) {
        active = isActive
        if isActive {
            watchActive()
        } else {
            watchers.values.forEach { $0.cancel() }
            watchers.removeAll()
        }
    }

    private func watchActive() {
        for job in jobs where job.status.isActive { watch(job.id) }
    }

    func watch(_ id: Int) {
        guard active, watchers[id] == nil, job(id)?.status.isActive == true else { return }
        watchers[id] = Task { [weak self] in
            await self?.monitor(id)
            self?.watchers[id] = nil
        }
    }

    private func monitor(_ id: Int) async {
        var lastUpdate = Date()
        while !Task.isCancelled, let current = job(id), current.status.isActive {
            do {
                try await api.subscribe(to: id) { [weak self] event in
                    guard let self, var job = self.job(id) else { return false }
                    job.update(from: event)
                    self.upsert(job)
                    lastUpdate = Date()
                    self.reconnecting[id] = nil
                    return job.status.isActive
                }
                try Task.checkCancellation()
                if let finished = job(id), finished.status.isTerminal {
                    if finished.status == .completed { completedOnSocket.insert(id) }
                    // WS is the progress source; the owner projection carries final metadata.
                    if let full = try? await api.status(id), full.status.isTerminal { upsert(full) }
                    finish(id)
                    return
                }
            } catch {
                if Task.isCancelled { return }
            }
            reconnecting[id] = lastUpdate
            if let polled = try? await api.status(id) {
                try? Task.checkCancellation()
                if Task.isCancelled { return }
                upsert(polled)
                if polled.status.isTerminal {
                    reconnecting[id] = nil
                    finish(id)
                    return
                }
            }
            try? await Task.sleep(for: .seconds(4))
        }
    }

    private func finish(_ id: Int) {
        if job(id)?.status == .completed { completions += 1 }
    }
}
