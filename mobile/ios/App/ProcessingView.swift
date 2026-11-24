import SwiftUI

/// Preparation of one job: stages from the job status, live over the
/// WebSocket (LibraryStore watches it), with a pinned action.
struct ProcessingView: View {
    let jobId: Int

    @Environment(LibraryStore.self) private var library
    @Environment(PlayerSession.self) private var session
    @Environment(AppRouter.self) private var router
    @Environment(Prefs.self) private var prefs
    @Environment(\.dismiss) private var dismiss
    @State private var retrying = false

    var body: some View {
        Group {
            if let job = library.job(jobId) {
                content(job)
            } else {
                ProgressView().tint(Color.snCyan).frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .snScreen()
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let job = library.job(jobId), job.status.isActive {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(L10n.t("action.cancel")) { Task { await library.cancel(jobId) } }
                        .font(SnFont.inter(16, .semibold))
                        .foregroundStyle(Color.snText)
                        .accessibilityIdentifier("job.cancel")
                }
            }
        }
        .onAppear { library.watch(jobId) }
        .sensoryFeedback(trigger: library.job(jobId)?.status) { _, new in
            guard prefs.haptics else { return nil }
            switch new {
            case .completed: return .success
            case .failed: return .error
            default: return nil
            }
        }
    }

    private func content(_ job: SongJob) -> some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    HStack(spacing: 14) {
                        SongCover(title: job.name, url: job.song.coverURL, size: 96, radius: 10)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(job.name).font(SnFont.title).foregroundStyle(Color.snText).lineLimit(2)
                            if let artist = job.artist {
                                Text(artist).font(SnFont.inter(15, .regular)).foregroundStyle(Color.snMuted)
                            }
                        }
                    }
                    SnDisplay(text: heading(job), size: 40)
                    Text(subline(job)).font(SnFont.body).foregroundStyle(Color.snMuted)
                    if job.status.isActive, let since = library.reconnecting[job.id] {
                        TimelineView(.periodic(from: .now, by: 1)) { context in
                            SnNote(text: L10n.t("proc.reconnecting", ["s": max(1, Int(context.date.timeIntervalSince(since)))]), tone: .warning)
                        }
                    }
                    if job.status != .failed && job.status != .cancelled {
                        stages(job)
                    } else if let reason = job.error, !reason.isEmpty, job.status == .failed {
                        Text(reason).font(SnFont.caption).foregroundStyle(Color.snPast).lineLimit(3)
                    }
                    if job.status.isActive {
                        SnNote(text: L10n.t("proc.hint"), tone: .info)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)
            }
            actions(job)
                .padding(.horizontal, 16)
                .padding(.bottom, 12)
        }
    }

    private func heading(_ job: SongJob) -> String {
        switch job.status {
        case .completed: L10n.t("proc.ready")
        case .failed: L10n.t("proc.failed")
        case .cancelled: L10n.t("proc.cancelled")
        case .queued: L10n.t("proc.queued")
        default: L10n.t("proc.running")
        }
    }

    private func subline(_ job: SongJob) -> String {
        switch job.status {
        case .completed: return L10n.t("proc.subReady")
        case .cancelled: return L10n.t("proc.subCancelled")
        case .queued: return L10n.t("proc.subQueued")
        case .failed:
            let reason = (job.error ?? "").lowercased()
            let source = ["download", "unavailable", "private", "yt-dlp", "video", "source"].contains { reason.contains($0) }
            return L10n.t(source ? "proc.subSource" : "proc.subFailed")
        default: return L10n.t("proc.subRunning")
        }
    }

    private enum StepState { case done, current, waiting }

    private func stages(_ job: SongJob) -> some View {
        let steps = [("proc.download", JobStatus.downloading), ("proc.separate", .separating), ("proc.lyrics", .transcribing)]
        let order: [JobStatus] = [.queued, .downloading, .separating, .transcribing, .finalizing, .completed]
        let currentIndex = order.firstIndex(of: job.status) ?? 0
        return VStack(spacing: 0) {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                let stepIndex = order.firstIndex(of: step.1)!
                let state: StepState = job.status == .completed || currentIndex > stepIndex ? .done
                    : currentIndex == stepIndex ? .current : .waiting
                HStack(spacing: 14) {
                    stepIcon(state, progress: job.progress)
                    Text(L10n.t(step.0))
                        .font(SnFont.inter(16, state == .waiting ? .medium : .semibold))
                        .foregroundStyle(state == .waiting ? Color.snMuted : Color.snText)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    switch state {
                    case .done: Text(L10n.t("proc.done")).font(SnFont.caption).foregroundStyle(Color.snMuted)
                    case .current:
                        Text("\(job.progress)%").font(SnFont.inter(15, .bold).monospacedDigit()).foregroundStyle(Color.snCyan)
                            .accessibilityIdentifier("job.progress")
                    case .waiting: EmptyView()
                    }
                }
                .frame(minHeight: 56)
                .overlay(alignment: .top) { Rectangle().fill(Color.snDivider).frame(height: 1) }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier(state == .current ? "job.stage.current" : "job.stage.\(index)")
            }
            Rectangle().fill(Color.snDivider).frame(height: 1)
        }
        .overlay {
            // The progress identifier must exist even while queued (no current step yet).
            if job.status == .queued {
                Color.clear.accessibilityElement().accessibilityLabel(L10n.t("proc.queued")).accessibilityIdentifier("job.progress")
            }
        }
    }

    @ViewBuilder
    private func stepIcon(_ state: StepState, progress: Int) -> some View {
        switch state {
        case .done:
            Image(systemName: "checkmark")
                .font(.system(size: 14, weight: .bold))
                .foregroundStyle(Color.snBlackout)
                .frame(width: 28, height: 28)
                .background(Circle().fill(Color.snSuccess))
        case .current:
            ZStack {
                Circle().stroke(Color.snTrack, lineWidth: 3)
                Circle().trim(from: 0, to: max(0.08, CGFloat(progress) / 100))
                    .stroke(Color.snCyan, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                    .rotationEffect(.degrees(-90))
            }
            .frame(width: 28, height: 28)
        case .waiting:
            Circle().stroke(Color.snBoundary, lineWidth: 1.5).frame(width: 28, height: 28)
        }
    }

    @ViewBuilder
    private func actions(_ job: SongJob) -> some View {
        switch job.status {
        case .completed:
            Button {
                session.open(job.song, api: library.api, autoplay: false)
            } label: {
                Label(L10n.t("action.sing"), systemImage: "play.fill")
            }
            .buttonStyle(.snPrimary)
            .accessibilityIdentifier(singIdentifier(job))
        case .failed, .cancelled:
            VStack(spacing: 10) {
                if let url = job.retryURL {
                    Button(L10n.t(job.status == .cancelled ? "proc.prepareAgain" : "action.retry")) {
                        Task { await resubmit(url, replacing: job) }
                    }
                    .buttonStyle(.snPrimary)
                    .disabled(retrying)
                }
                Button(L10n.t("proc.tryAnother")) {
                    dismiss()
                    router.add = AddRequest()
                }
                .buttonStyle(.snSecondary)
            }
        default:
            Button(L10n.t("proc.keepBrowsing")) { dismiss() }
                .buttonStyle(.snSecondary)
        }
    }

    private func singIdentifier(_ job: SongJob) -> String {
        #if DEBUG
        if LiveVerification.active && library.completedOnSocket.contains(job.id) { return "job.sing.websocket" }
        #endif
        return "job.sing"
    }

    private func resubmit(_ url: URL, replacing job: SongJob) async {
        retrying = true
        defer { retrying = false }
        guard let fresh = try? await library.submit(url) else { return }
        router.replaceProcessing(job.id, with: fresh.id)
        try? await library.remove(job.id)
    }
}
