import SwiftUI

/// Stage: the lyrics fill the screen, the dock (scrub, transport, Original
/// vocals) sits in thumb reach. Minimizing keeps the song playing.
struct StageView: View {
    @Environment(PlayerSession.self) private var session
    @Environment(LibraryStore.self) private var library
    @Environment(Prefs.self) private var prefs
    @Environment(AppRouter.self) private var router
    @State private var performance = false
    @State private var follow = true
    @State private var copied = false

    var body: some View {
        VStack(spacing: 0) {
            if !performance { topBar }
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            if let player = session.player, session.state == .ready, !player.ended || performance {
                if performance {
                    PerformanceDock(player: player, exit: { performance = false })
                } else {
                    StageDock(player: player, lines: session.lyrics?.timedLines ?? [])
                }
            } else if session.state != .ready {
                StageDock(player: nil, lines: [])
            }
        }
        .background(Color.snBlackout.ignoresSafeArea())
        .overlay(alignment: .top) {
            if copied {
                Text(L10n.t("stage.copied"))
                    .font(SnFont.label)
                    .foregroundStyle(Color.snBlackout)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .background(Capsule().fill(Color.snCyan))
                    .padding(.top, 56)
                    .transition(.opacity)
            }
        }
        .statusBarHidden(performance)
    }

    private var topBar: some View {
        HStack(spacing: 8) {
            Button {
                session.stagePresented = false
            } label: {
                Image(systemName: "chevron.down").font(.system(size: 20, weight: .semibold)).frame(width: 44, height: 44)
            }
            .foregroundStyle(Color.snText)
            .accessibilityLabel(L10n.t("stage.minimize"))
            .accessibilityIdentifier("stage.minimize")

            VStack(spacing: 2) {
                Text(session.song?.title ?? "")
                    .font(SnFont.inter(16, .semibold, relativeTo: .headline))
                    .foregroundStyle(Color.snText)
                if let artist = session.song?.artist {
                    Text(artist).font(SnFont.caption).foregroundStyle(Color.snMuted)
                }
            }
            .lineLimit(1)
            .frame(maxWidth: .infinity)

            Menu {
                Picker(L10n.t("stage.size"), selection: Binding(get: { prefs.lyricsSize }, set: { prefs.lyricsSize = $0 })) {
                    ForEach(LyricsSize.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.inline)
                Button { performance = true } label: { Label(L10n.t("stage.perf"), systemImage: "music.mic") }
                if let song = session.song, !song.isCatalogAsset {
                    Button {
                        UIPasteboard.general.string = library.api.shareBase.appendingPathComponent("share/\(song.token)").absoluteString
                        withAnimation { copied = true }
                        Task {
                            try? await Task.sleep(for: .seconds(2))
                            withAnimation { copied = false }
                        }
                    } label: { Label(L10n.t("stage.copyLink"), systemImage: "link") }
                }
                Button { report() } label: { Label(L10n.t("stage.report"), systemImage: "flag") }
            } label: {
                Image(systemName: "ellipsis").font(.system(size: 20, weight: .bold)).frame(width: 44, height: 44)
            }
            .foregroundStyle(Color.snText)
            .accessibilityLabel(L10n.t("stage.more"))
            .accessibilityIdentifier("stage.more")
        }
        .padding(.horizontal, 8)
        .frame(height: 52)
    }

    @ViewBuilder
    private var content: some View {
        switch session.state {
        case .idle, .loading:
            VStack(spacing: 16) {
                ProgressView().tint(Color.snCyan)
                Text(L10n.t("stage.loading")).font(SnFont.body).foregroundStyle(Color.snMuted)
            }
        case .failed:
            VStack(spacing: 16) {
                Image(systemName: "waveform.slash").font(.system(size: 40)).foregroundStyle(Color.snError)
                Text(L10n.t("stage.audioError")).font(SnFont.title).foregroundStyle(Color.snText)
                Button(L10n.t("action.retry")) { session.retry() }
                    .buttonStyle(.snSecondary)
                    .frame(maxWidth: 240)
            }
            .padding(24)
        case .ready:
            if let player = session.player, let lyrics = session.lyrics {
                if player.ended && !performance {
                    EndCard(player: player)
                } else if !lyrics.timedLines.isEmpty {
                    SyncedLyricsView(lines: lyrics.timedLines, player: player, performance: performance, follow: $follow)
                        .id(session.song?.token)
                } else if !lyrics.plainLines.isEmpty {
                    UnsyncedLyrics(lines: lyrics.plainLines)
                } else {
                    NoLyrics(song: session.song, report: report)
                }
            }
        }
    }

    private func report() {
        router.openFeedback()
        session.stagePresented = false
    }
}

// MARK: - Lyrics

struct SyncedLyricsView: View {
    let lines: [Lyrics.Line]
    let player: StemPlayer
    let performance: Bool
    @Binding var follow: Bool

    @Environment(Prefs.self) private var prefs
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var scrollTarget: Int?
    @State private var tapped = 0
    @State private var cues: [LyricsModel.Cue] = []

    var body: some View {
        let time = player.position
        let active = LyricsModel.activeIndex(at: time, in: lines)
        let roles = LyricsModel.roles(lines, active: active)
        let direction = LyricsModel.songDirection(lines.map(\.text))
        ScrollView {
            LazyVStack(alignment: performance ? .center : .leading, spacing: performance ? 20 : 14) {
                ForEach(lines.indices, id: \.self) { index in
                    VStack(alignment: performance ? .center : .leading, spacing: 10) {
                        if prefs.countIn, let cue = cues.first(where: { $0.before == index }),
                           cue.kind == .instrumental || time < cue.end {
                            CueRow(cue: cue, time: time)
                        }
                        lineView(index, role: roles[index], time: time)
                    }
                    .frame(maxWidth: .infinity, alignment: performance ? .center : .leading)
                }
                Color.clear.frame(height: 320)
            }
            .scrollTargetLayout()
            .padding(.horizontal, 20)
            .padding(.top, 28)
            .environment(\.layoutDirection, direction)
        }
        .scrollPosition(id: $scrollTarget, anchor: UnitPoint(x: 0.5, y: LyricsModel.anchor))
        .modifier(ManualScrollDetector { follow = false })
        .mask(
            LinearGradient(stops: [
                .init(color: .clear, location: 0), .init(color: .black, location: 0.05),
                .init(color: .black, location: 0.9), .init(color: .clear, location: 1),
            ], startPoint: .top, endPoint: .bottom)
        )
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("stage.lyrics")
        .overlay(alignment: .bottom) {
            if !follow, active != nil {
                Button {
                    follow = true
                } label: {
                    Label(L10n.t("stage.follow"), systemImage: "arrow.down.to.line")
                        .font(SnFont.inter(15, .semibold))
                        .foregroundStyle(Color.snBlackout)
                        .padding(.horizontal, 16)
                        .frame(minHeight: 44)
                        .background(Capsule().fill(Color.snCyan))
                }
                .padding(.bottom, 12)
                .accessibilityIdentifier("stage.follow")
            }
        }
        .sensoryFeedback(trigger: tapped) { _, _ in prefs.haptics ? .selection : nil }
        .onAppear {
            cues = LyricsModel.cues(lines, duration: player.duration)
            scrollTarget = active ?? 0
        }
        .onChange(of: active) { _, index in
            guard follow else { return }
            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.28)) { scrollTarget = index ?? 0 }
        }
        .onChange(of: follow) { _, following in
            guard following else { return }
            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.28)) { scrollTarget = active ?? 0 }
        }
    }

    private func lineView(_ index: Int, role: LyricsModel.Role, time: Double) -> some View {
        let line = lines[index]
        let style = LyricsModel.fontSize(role, size: prefs.lyricsSize, performance: performance)
        return VStack(alignment: performance ? .center : .leading, spacing: 2) {
            if role == .speech {
                Text(L10n.t("stage.speech").uppercased()).font(SnFont.eyebrow).foregroundStyle(Color.snPast)
            }
            Text(text(for: line, role: role, time: time))
                .font(SnFont.inter(style.size, style.weight, relativeTo: .title))
                .foregroundStyle(LyricsModel.color(role))
                .italic(role == .speech)
                .multilineTextAlignment(performance ? .center : .leading)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: performance ? .center : .leading)
                .environment(\.layoutDirection, LyricsModel.direction(of: line.text))
        }
        .animation(reduceMotion ? nil : .snappy(duration: 0.2), value: role)
        .contentShape(Rectangle())
        .onTapGesture {
            tapped += 1
            follow = true
            player.seek(to: line.t)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(role == .active ? [.isSelected] : [])
        .accessibilityHint(L10n.t("stage.seekLine", ["time": Clock.label(line.t)]))
        .accessibilityAction { player.seek(to: line.t) }
        .accessibilityIdentifier("stage.lyric.\(index)")
    }

    /// The active line underlines its current word in cyan when word timing exists.
    private func text(for line: Lyrics.Line, role: LyricsModel.Role, time: Double) -> AttributedString {
        guard role == .active, let words = line.words, let current = LyricsModel.currentWord(in: line, at: time) else {
            return AttributedString(line.text)
        }
        var result = AttributedString()
        for (index, word) in words.enumerated() {
            if index > 0 { result.append(AttributedString(" ")) }
            var piece = AttributedString(word.text)
            if index == current {
                piece.underlineStyle = Text.LineStyle(pattern: .solid, color: .snCyan)
            }
            result.append(piece)
        }
        return result
    }
}

/// Manual scrolling turns follow off (iOS 18 scroll phases; a drag on iOS 17).
private struct ManualScrollDetector: ViewModifier {
    let onManualScroll: () -> Void

    func body(content: Content) -> some View {
        if #available(iOS 18.0, *) {
            content.onScrollPhaseChange { _, phase in
                if phase == .interacting { onManualScroll() }
            }
        } else {
            content.simultaneousGesture(DragGesture(minimumDistance: 12).onChanged { _ in onManualScroll() })
        }
    }
}

private struct CueRow: View {
    let cue: LyricsModel.Cue
    let time: Double

    var body: some View {
        let state = LyricsModel.countdown(cue, at: time)
        HStack(spacing: 8) {
            HStack(spacing: 6) {
                ForEach(0..<3, id: \.self) { dot in
                    Circle()
                        .fill(dot < state.lit ? Color.snCyan : Color.snTrack)
                        .frame(width: 10, height: 10)
                }
            }
            Image(systemName: "music.note").foregroundStyle(Color.snMuted)
            Text(label(state))
                .font(SnFont.inter(15, .medium).monospacedDigit())
                .foregroundStyle(state.active ? Color.snCyan : Color.snMuted)
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(cue.kind == .intro ? "stage.cue.intro" : "stage.cue.instrumental")
    }

    private func label(_ state: (active: Bool, remaining: Double, lit: Int)) -> String {
        switch cue.kind {
        case .intro:
            return L10n.t("stage.introIn", ["n": Int(state.remaining.rounded(.up))])
        case .instrumental:
            return L10n.t("stage.instrumental", ["time": Clock.label(state.active ? state.remaining : cue.end - cue.start)])
        }
    }
}

private struct UnsyncedLyrics: View {
    let lines: [String]
    @Environment(Prefs.self) private var prefs

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                SnNote(text: L10n.t("stage.unsynced"), tone: .info)
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    Text(line)
                        .font(SnFont.inter(LyricsModel.fontSize(.next, size: prefs.lyricsSize, performance: false).size, .semibold, relativeTo: .title))
                        .foregroundStyle(Color.snText)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .environment(\.layoutDirection, LyricsModel.direction(of: line))
                }
            }
            .padding(20)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("stage.unsynced")
    }
}

private struct NoLyrics: View {
    let song: SongRef?
    let report: () -> Void

    var body: some View {
        VStack(spacing: 20) {
            SongCover(title: song?.title ?? "♪", url: song?.coverURL, size: 200, radius: 20)
            Text(L10n.t("stage.noLyrics")).font(SnFont.title).foregroundStyle(Color.snText).multilineTextAlignment(.center)
            Button(L10n.t("stage.reportLyrics"), action: report).buttonStyle(.snSecondary).frame(maxWidth: 280)
        }
        .padding(24)
    }
}

private struct EndCard: View {
    let player: StemPlayer
    @Environment(LibraryStore.self) private var library
    @Environment(PlayerSession.self) private var session
    @Environment(Prefs.self) private var prefs

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                SnDisplay(text: L10n.t("stage.end"), size: 44)
                Button {
                    player.seek(to: 0)
                    player.play()
                } label: { Label(L10n.t("stage.again"), systemImage: "arrow.counterclockwise") }
                    .buttonStyle(.snPrimary)
                    .accessibilityIdentifier("stage.again")
                ForEach(next) { job in
                    Button {
                        session.open(job.song, api: library.api, autoplay: true)
                    } label: {
                        HStack(spacing: 12) {
                            SongCover(title: job.name, url: job.song.coverURL)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(job.name).font(SnFont.headline).foregroundStyle(Color.snText)
                                if let artist = job.artist { Text(artist).font(SnFont.caption).foregroundStyle(Color.snMuted) }
                            }
                            Spacer()
                            Image(systemName: "play.fill").foregroundStyle(Color.snMuted)
                        }
                        .frame(minHeight: 60)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(20)
        }
    }

    private var next: [SongJob] {
        Array(library.ready(playedAt: prefs.playedAt).filter { $0.jobToken != session.song?.token }.prefix(3))
    }
}

// MARK: - Dock

struct StageDock: View {
    let player: StemPlayer?
    let lines: [Lyrics.Line]
    @Environment(PlayerSession.self) private var session
    @Environment(Prefs.self) private var prefs

    var body: some View {
        VStack(spacing: 6) {
            if let message = player?.errorMessage {
                Text(message).font(SnFont.caption).foregroundStyle(Color.snError)
            }
            Scrubber(position: player?.position ?? 0, duration: player?.duration ?? 0, lines: lines,
                     enabled: player != nil) { player?.seek(to: $0) }
            HStack {
                skipButton(-5)
                Spacer()
                PlayButton(player: player, size: 72)
                Spacer()
                skipButton(5)
            }
            .padding(.horizontal, 28)
            .frame(height: 80)
            VocalsControl(enabled: player != nil)
        }
        .padding(.horizontal, 16)
        .padding(.top, 14)
        .padding(.bottom, 4)
        .background(
            UnevenRoundedRectangle(topLeadingRadius: 20, topTrailingRadius: 20, style: .continuous)
                .fill(Color.snSurface)
                .ignoresSafeArea(edges: .bottom)
        )
        .environment(\.layoutDirection, .leftToRight)
    }

    private func skipButton(_ seconds: Double) -> some View {
        Button {
            player?.skip(by: seconds)
        } label: {
            Image(systemName: seconds < 0 ? "gobackward.5" : "goforward.5")
                .font(.system(size: 28, weight: .medium))
                .foregroundStyle(Color.snText)
                .frame(width: 56, height: 56)
        }
        .disabled(player == nil)
        .accessibilityLabel(L10n.t(seconds < 0 ? "stage.back5" : "stage.fwd5"))
        .accessibilityIdentifier(seconds < 0 ? "stage.back5" : "stage.fwd5")
    }
}

struct PlayButton: View {
    let player: StemPlayer?
    let size: CGFloat
    @Environment(Prefs.self) private var prefs

    var body: some View {
        Button {
            player?.toggle()
        } label: {
            ZStack {
                Circle().fill(player == nil ? Color.snTrack : Color.snLime)
                if player == nil {
                    ProgressView().tint(Color.snBlackout)
                } else {
                    Image(systemName: player?.isPlaying == true ? "pause.fill" : "play.fill")
                        .font(.system(size: size * 0.4, weight: .bold))
                        .foregroundStyle(Color.snBlackout)
                }
            }
            .frame(width: size, height: size)
        }
        .disabled(player == nil)
        .accessibilityLabel(player?.isPlaying == true ? L10n.t("stage.pause") : L10n.t("stage.play"))
        .accessibilityIdentifier("stage.playPause")
        .sensoryFeedback(trigger: player?.isPlaying) { _, _ in prefs.haptics ? .impact(weight: .light) : nil }
    }
}

/// Scrub: thickens while dragging, shows a bubble with the time and the line
/// there, and on release snaps to a line start closer than 0.6 s.
struct Scrubber: View {
    let position: Double
    let duration: Double
    let lines: [Lyrics.Line]
    let enabled: Bool
    let seek: (Double) -> Void

    @Environment(Prefs.self) private var prefs
    @State private var drag: Double?
    @State private var snapped = 0

    var body: some View {
        let time = drag ?? position
        HStack(spacing: 12) {
            Text(Clock.label(time))
                .font(SnFont.inter(13, .semibold).monospacedDigit())
                .foregroundStyle(Color.snCyan)
                .frame(minWidth: 40, alignment: .leading)
                .accessibilityIdentifier("stage.elapsed")
            GeometryReader { geo in
                let fraction = duration > 0 ? min(1, max(0, time / duration)) : 0
                let thick = drag != nil
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.snTrack).frame(height: thick ? 12 : 6)
                    Capsule().fill(Color.snCyan).frame(width: max(0, geo.size.width * fraction), height: thick ? 12 : 6)
                    Circle()
                        .fill(Color.snText)
                        .frame(width: thick ? 30 : 24, height: thick ? 30 : 24)
                        .shadow(color: .black.opacity(0.4), radius: 4)
                        .offset(x: geo.size.width * fraction - (thick ? 15 : 12))
                }
                .frame(maxHeight: .infinity)
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { value in
                            guard enabled, duration > 0, geo.size.width > 0 else { return }
                            drag = min(1, max(0, value.location.x / geo.size.width)) * duration
                        }
                        .onEnded { value in
                            guard enabled, duration > 0, geo.size.width > 0 else { drag = nil; return }
                            let raw = min(1, max(0, value.location.x / geo.size.width)) * duration
                            let target = LyricsModel.snapToLine(raw, lines: lines)
                            if target != raw { snapped += 1 }
                            seek(target)
                            drag = nil
                        }
                )
                .overlay(alignment: .topLeading) {
                    if let drag {
                        bubble(drag)
                            .frame(width: 220)
                            .offset(x: min(max(0, geo.size.width * fraction - 110), geo.size.width - 220), y: -64)
                    }
                }
                .animation(.easeOut(duration: 0.12), value: thick)
            }
            .frame(height: 44)
            .accessibilityElement()
            .accessibilityLabel(L10n.t("stage.position"))
            .accessibilityValue(L10n.t("stage.positionValue", ["elapsed": Clock.label(time), "total": Clock.label(duration)]))
            .accessibilityAdjustableAction { direction in
                switch direction {
                case .increment: seek(min(duration, position + 5))
                case .decrement: seek(max(0, position - 5))
                @unknown default: break
                }
            }
            .accessibilityIdentifier("stage.seek")
            Text("-" + Clock.label(max(0, duration - time)))
                .font(SnFont.inter(13, .medium).monospacedDigit())
                .foregroundStyle(Color.snMuted)
                .frame(minWidth: 44, alignment: .trailing)
        }
        .sensoryFeedback(trigger: snapped) { _, _ in prefs.haptics ? .selection : nil }
        .zIndex(1)
    }

    private func bubble(_ time: Double) -> some View {
        let line = LyricsModel.activeIndex(at: time, in: lines).map { lines[$0].text }
        return VStack(spacing: 2) {
            Text(Clock.label(time)).font(SnFont.inter(15, .bold).monospacedDigit()).foregroundStyle(Color.snCyan)
            if let line {
                Text(line).font(SnFont.caption).foregroundStyle(Color.snText).lineLimit(1)
                    .environment(\.layoutDirection, LyricsModel.direction(of: line))
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .frame(maxWidth: 220)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.snRaised).shadow(color: .black.opacity(0.8), radius: 16, y: 12))
    }
}

/// Original vocals: mic toggle (0 ↔ previous level) and a 0–100% slider
/// with detents at 0/50/100.
struct VocalsControl: View {
    let enabled: Bool
    @Environment(PlayerSession.self) private var session
    @Environment(Prefs.self) private var prefs
    @State private var detent = 0

    var body: some View {
        let pct = session.vocalsPct
        HStack(alignment: .center, spacing: 14) {
            MicToggle(enabled: enabled)
            VStack(spacing: 2) {
                HStack {
                    Text(L10n.t("stage.vocals")).font(SnFont.label).foregroundStyle(Color.snText)
                    Spacer()
                    Text("\(pct)%").font(SnFont.inter(15, .bold).monospacedDigit()).foregroundStyle(Color.snPink)
                }
                Slider(value: Binding(get: { Double(pct) }, set: { setLevel(Int($0.rounded())) }), in: 0...100, step: 1)
                    .tint(Color.snPink)
                    .disabled(!enabled)
                    .accessibilityLabel(L10n.t("stage.vocalsA11y"))
                    .accessibilityValue(L10n.t("stage.vocalsValue", ["n": pct]))
                    .accessibilityIdentifier("stage.vocals")
                HStack {
                    Text(L10n.t("stage.off"))
                    Spacer()
                    Text(L10n.t("stage.half"))
                    Spacer()
                    Text(L10n.t("stage.full"))
                }
                .font(SnFont.inter(12, .medium))
                .foregroundStyle(Color.snMuted)
                .accessibilityHidden(true)
            }
        }
        .frame(minHeight: 64)
        .sensoryFeedback(trigger: detent) { _, _ in prefs.haptics ? .selection : nil }
    }

    private func setLevel(_ raw: Int) {
        var value = Prefs.clamp(raw)
        if let near = [0, 50, 100].first(where: { abs($0 - value) <= 3 }) { value = near }
        if value != session.vocalsPct, [0, 50, 100].contains(value) { detent += 1 }
        session.setVocals(value)
    }
}

struct MicToggle: View {
    let enabled: Bool
    var size: CGFloat = 48
    @Environment(PlayerSession.self) private var session

    var body: some View {
        let on = session.vocalsPct > 0
        Button {
            session.toggleVocals()
        } label: {
            Image(systemName: on ? "mic.fill" : "mic.slash.fill")
                .font(.system(size: size * 0.4, weight: .semibold))
                .foregroundStyle(on ? Color.snPink : Color.snMuted)
                .frame(width: size, height: size)
                .background(Circle().fill(Color.snRaised))
        }
        .disabled(!enabled)
        .accessibilityLabel(L10n.t("stage.vocalsMute"))
        .accessibilityAddTraits(.isToggle)
        .accessibilityValue(on ? "0" : "1")
        .accessibilityIdentifier("stage.mic")
    }
}

/// Performance view: centred larger text, × / play 60 / mic; no scrub.
private struct PerformanceDock: View {
    let player: StemPlayer
    let exit: () -> Void

    var body: some View {
        HStack {
            Button(action: exit) {
                Image(systemName: "xmark").font(.system(size: 20, weight: .semibold)).frame(width: 48, height: 48)
                    .background(Circle().fill(Color.snRaised))
            }
            .foregroundStyle(Color.snText)
            .accessibilityLabel(L10n.t("stage.perfExit"))
            .accessibilityIdentifier("stage.perfExit")
            Spacer()
            PlayButton(player: player, size: 60)
            Spacer()
            MicToggle(enabled: true)
        }
        .padding(.horizontal, 32)
        .padding(.vertical, 12)
    }
}
