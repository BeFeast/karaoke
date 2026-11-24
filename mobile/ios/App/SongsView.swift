import SwiftUI

struct SongsView: View {
    let api: any SongsAPI
    let signOut: () async throws -> Void

    @Environment(\.scenePhase) private var scenePhase
    @State private var identity: Identity?
    @State private var jobs: [SongJob] = []
    @State private var navigation: [Int] = []
    @State private var hasMore = false
    @State private var loading = false
    @State private var errorMessage: String?
    @State private var sharedVideo: SharedVideo?
    @State private var showingPaste = false
    @State private var pastedVideo: SharedVideo?

    var body: some View {
        NavigationStack(path: $navigation) {
            List {
                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.secondary)
                        Button("Повторить") { Task { await refresh() } }
                    }
                }
                ForEach(jobs) { job in
                    NavigationLink(value: job.id) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(job.name).font(.headline)
                            if let artist = job.artist {
                                Text(artist).foregroundStyle(.secondary)
                            }
                            if job.status != .completed {
                                Text(job.status.title)
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .accessibilityIdentifier("songs.job.\(job.id)")
                }
                if hasMore {
                    Button("Показать ещё") {
                        Task { await loadPage(before: jobs.last?.id) }
                    }
                    .disabled(loading)
                }
            }
            .overlay {
                if loading && jobs.isEmpty {
                    ProgressView("Загрузка песен")
                } else if errorMessage == nil && jobs.isEmpty {
                    ContentUnavailableView(
                        "Песен пока нет",
                        systemImage: "music.note",
                        description: Text("Поделитесь видео из YouTube или вставьте ссылку.")
                    )
                }
            }
            .refreshable { await refresh() }
            .navigationTitle("Мои песни")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Menu {
                        if let identity { Text(identity.label) }
                        Button("Выйти", role: .destructive) {
                            Task {
                                do { try await signOut() }
                                catch { errorMessage = "Не удалось выйти. Проверьте сеть." }
                            }
                        }
                    } label: {
                        Label("Аккаунт", systemImage: "person.crop.circle")
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showingPaste = true
                    } label: {
                        Label("Добавить песню", systemImage: "plus")
                    }
                    .accessibilityIdentifier("songs.add")
                }
            }
            .navigationDestination(for: Int.self) { id in
                if let job = jobs.first(where: { $0.id == id }) {
                    JobView(job: job, api: api, onUpdate: upsert)
                }
            }
        }
        .sheet(isPresented: $showingPaste, onDismiss: {
            if let pastedVideo {
                sharedVideo = pastedVideo
                self.pastedVideo = nil
            } else {
                presentPendingShare()
            }
        }) {
            PasteVideoSheet { video in
                pastedVideo = video
                showingPaste = false
            }
        }
        .sheet(item: $sharedVideo, onDismiss: presentPendingShare) { video in
            ConfirmVideoSheet(video: video, submit: { try await api.submit(video.url) }) { job in
                ShareInbox.remove(video)
                sharedVideo = nil
                if let job {
                    upsert(job)
                    navigation = [job.id]
                }
            }
            .interactiveDismissDisabled()
        }
        .task {
            await refresh()
            presentPendingShare()
        }
        .onChange(of: navigation.isEmpty) { _, atList in
            if atList { presentPendingShare() }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { presentPendingShare() }
        }
    }

    private func presentPendingShare() {
        guard identity != nil, sharedVideo == nil, !showingPaste, navigation.isEmpty else { return }
        sharedVideo = ShareInbox.pending()
    }

    private func refresh() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            let owner = try await api.identity()
            guard owner.state == "clerk_user" else { throw CoordinatorError.missingSession }
            identity = owner
            let current = try await api.jobs(before: nil)
            try Task.checkCancellation()
            jobs = current
            hasMore = current.count == Coordinator.pageSize
            errorMessage = nil
            presentPendingShare()
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadPage(before id: Int?) async {
        guard !loading, let id else { return }
        loading = true
        defer { loading = false }
        do {
            let page = try await api.jobs(before: id)
            try Task.checkCancellation()
            let existing = Set(jobs.map(\.id))
            jobs.append(contentsOf: page.filter { !existing.contains($0.id) })
            hasMore = page.count == Coordinator.pageSize
            errorMessage = nil
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func upsert(_ job: SongJob) {
        if let index = jobs.firstIndex(where: { $0.id == job.id }) {
            jobs[index] = job
        } else {
            jobs.insert(job, at: 0)
        }
    }
}

private struct PasteVideoSheet: View {
    let onAccept: (SharedVideo) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        NavigationStack {
            Form {
                TextField("Ссылка YouTube", text: $text)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .accessibilityIdentifier("paste.url")
                if !text.isEmpty && SharedVideo.validatedURL(from: text) == nil {
                    Text("Вставьте ссылку на видео YouTube.").foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Добавить песню")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Отмена") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Далее") {
                        if let url = SharedVideo.validatedURL(from: text) {
                            onAccept(SharedVideo(url: url))
                        }
                    }
                    .disabled(SharedVideo.validatedURL(from: text) == nil)
                    .accessibilityIdentifier("paste.next")
                }
            }
        }
    }
}

private struct ConfirmVideoSheet: View {
    let video: SharedVideo
    let submit: () async throws -> SongJob
    let finish: (SongJob?) -> Void
    @State private var submitting = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Text(video.url.absoluteString).textSelection(.enabled)
                if let errorMessage {
                    Text(errorMessage).foregroundStyle(.secondary)
                }
                Button {
                    Task { await confirm() }
                } label: {
                    if submitting { ProgressView("Отправка") }
                    else { Text("Подготовить песню") }
                }
                .disabled(submitting)
                .accessibilityIdentifier("confirm.submit")
            }
            .navigationTitle("Подготовить песню?")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Отмена") { finish(nil) }.disabled(submitting)
                }
            }
        }
    }

    private func confirm() async {
        guard !submitting else { return }
        submitting = true
        errorMessage = nil
        defer { submitting = false }
        do {
            finish(try await submit())
        } catch let error as URLError {
            errorMessage = "\(error.localizedDescription) Проверьте список песен перед повторной отправкой."
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct JobView: View {
    let api: any SongsAPI
    let onUpdate: (SongJob) -> Void
    @Environment(\.scenePhase) private var scenePhase
    @State private var job: SongJob
    @State private var showingStage = false
    @State private var usingPolling = false
    @State private var errorMessage: String?

    init(job: SongJob, api: any SongsAPI, onUpdate: @escaping (SongJob) -> Void) {
        _job = State(initialValue: job)
        self.api = api
        self.onUpdate = onUpdate
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(job.name).font(.largeTitle.bold())
            if let artist = job.artist { Text(artist).font(.title3).foregroundStyle(.secondary) }
            if job.status == .completed {
                Button("Sing", systemImage: "mic") { showingStage = true }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .accessibilityIdentifier("job.sing")
            } else if job.status.isTerminal {
                Text(job.status.title).font(.headline)
                if let error = job.error { Text(error).foregroundStyle(.secondary) }
            } else {
                ProgressView(value: Double(job.progress), total: 100) {
                    Text(job.status.title)
                } currentValueLabel: {
                    Text("\(job.progress)%").monospacedDigit()
                }
                .accessibilityIdentifier("job.progress")
                if usingPolling {
                    Text(errorMessage ?? "Восстанавливаем связь").foregroundStyle(.secondary)
                }
            }
            Spacer()
        }
        .padding(24)
        .frame(maxWidth: .infinity, alignment: .leading)
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(isPresented: $showingStage) { StageView(job: job, api: api) }
        .task(id: scenePhase) {
            guard scenePhase == .active, !job.status.isTerminal else { return }
            await monitor()
        }
    }

    private func monitor() async {
        while !Task.isCancelled && !job.status.isTerminal {
            do {
                usingPolling = false
                try await api.subscribe(to: job.id) { event in
                    job.update(from: event)
                    return !job.status.isTerminal
                }
                try Task.checkCancellation()
                if job.status.isTerminal {
                    // WS is the progress source; the full owner projection carries final metadata.
                    if let current = try? await api.status(job.id), current.status.isTerminal {
                        job = current
                    }
                    onUpdate(job)
                    return
                }
            } catch {
                if Task.isCancelled { return }
            }
            usingPolling = true
            do {
                let current = try await api.status(job.id)
                try Task.checkCancellation()
                if job.status != .finalizing || current.status.isTerminal { job = current }
                onUpdate(job)
                errorMessage = nil
            } catch {
                errorMessage = "Не удалось обновить статус песни."
            }
            try? await Task.sleep(for: .seconds(4))
        }
    }
}
