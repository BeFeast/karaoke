import SwiftUI

struct StageView: View {
    let job: SongJob
    let api: any SongsAPI

    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var player: StemPlayer?
    @State private var assets: SongAssets?
    @State private var lines: [Lyrics.Line] = []
    @State private var loadError: String?
    @State private var attempt = 0
    @State private var resumeAfterScrub = false
    @State private var scrollTarget: Int?
    @ScaledMetric(relativeTo: .body) private var lyricSize = 16.0
    @ScaledMetric(relativeTo: .title3) private var activeSize = 20.0

    var body: some View {
        Group {
            if let player, let assets {
                stage(player: player, lyrics: assets.lyrics)
            } else if let loadError {
                ContentUnavailableView {
                    Label("Не удалось открыть песню", systemImage: "waveform")
                } description: {
                    Text(loadError)
                } actions: {
                    Button("Повторить") {
                        self.loadError = nil
                        attempt += 1
                    }
                    .buttonStyle(.borderedProminent)
                }
            } else {
                ProgressView("Загрузка дорожек")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .navigationTitle(job.name)
        .navigationBarTitleDisplayMode(.inline)
        .background(SonereliColor.background.ignoresSafeArea())
        .task(id: attempt) { await load() }
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                player?.updatePosition()
                try? await Task.sleep(for: .milliseconds(30))
            }
        }
        .onDisappear {
            player?.close()
            player = nil
            assets?.remove()
            assets = nil
            UIApplication.shared.isIdleTimerDisabled = false
        }
        .onChange(of: scenePhase, initial: true) { _, phase in
            UIApplication.shared.isIdleTimerDisabled = phase == .active
        }
    }

    private func load() async {
        guard player == nil else { return }
        do {
            let downloaded = try await api.assets(for: job)
            do {
                try Task.checkCancellation()
                let prepared = try StemPlayer(assets: downloaded, title: job.name, artist: job.artist)
                lines = downloaded.lyrics.timedLines
                assets = downloaded
                player = prepared
            } catch {
                downloaded.remove()
                throw error
            }
        } catch is CancellationError {
            return
        } catch {
            loadError = error.localizedDescription
        }
    }

    private func stage(player: StemPlayer, lyrics: Lyrics) -> some View {
        let active = lyrics.activeIndex(at: player.position, in: lines)
        return ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                if lines.isEmpty {
                    if let text = lyrics.plain, !text.isEmpty {
                        ForEach(Array(text.components(separatedBy: .newlines).enumerated()), id: \.offset) { _, line in
                            if !line.isEmpty { lyricText(line, active: false) }
                        }
                    } else {
                        Text("Текст песни недоступен").foregroundStyle(.secondary)
                    }
                } else {
                    ForEach(lines.indices, id: \.self) { index in
                        lyricText(lines[index].text, active: index == active)
                            .accessibilityIdentifier("stage.lyric.\(index)")
                            .accessibilityAddTraits(index == active ? .isSelected : [])
                            .id(index)
                    }
                }
            }
            .scrollTargetLayout()
            .padding(.horizontal, 24)
            .padding(.vertical, 24)
        }
        .scrollPosition(id: $scrollTarget, anchor: .center)
        .accessibilityIdentifier("stage.lyrics")
        .onChange(of: active, initial: true) { _, index in
            guard let index else { return }
            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.20)) {
                scrollTarget = index
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { controls(player) }
    }

    private func lyricText(_ text: String, active: Bool) -> some View {
        Text(text)
            .font(.system(size: active ? max(20, activeSize) : max(16, lyricSize),
                          weight: active ? .semibold : .regular))
            .foregroundStyle(active ? Color.white : Color.secondary)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .environment(\.layoutDirection, Self.direction(of: text))
    }

    private static func direction(of text: String) -> LayoutDirection {
        let firstLetter = text.unicodeScalars.first(where: { CharacterSet.letters.contains($0) })
        guard let code = firstLetter?.value else { return .leftToRight }
        return (0x0590...0x08FF).contains(code) || (0xFB1D...0xFEFC).contains(code)
            ? .rightToLeft : .leftToRight
    }

    private func controls(_ player: StemPlayer) -> some View {
        @Bindable var player = player
        return VStack(spacing: 8) {
            if let error = player.errorMessage {
                Text(error).foregroundStyle(.secondary)
            }
            Slider(
                value: Binding(get: { player.position }, set: { player.seek(to: $0) }),
                in: 0...player.duration,
                onEditingChanged: { editing in
                    if editing {
                        resumeAfterScrub = player.isPlaying
                        if resumeAfterScrub { player.pause() }
                    } else if resumeAfterScrub {
                        resumeAfterScrub = false
                        if player.position < player.duration { player.play() }
                    }
                }
            )
            .accessibilityLabel("Позиция")
            .accessibilityIdentifier("stage.seek")

            HStack {
                Text(timeLabel(player.position))
                .accessibilityIdentifier("stage.elapsed")
                Spacer()
                Text(timeLabel(player.duration))
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)

            Button {
                if player.isPlaying { player.pause() } else { player.play() }
            } label: {
                Label(player.isPlaying ? "Пауза" : "Играть",
                      systemImage: player.isPlaying ? "pause.fill" : "play.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .accessibilityIdentifier("stage.playPause")

            gain("Минус", id: "stage.backing", level: $player.backingLevel)
            gain("Вокал", id: "stage.vocals", level: $player.vocalsLevel)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 12)
        .background(.regularMaterial)
    }

    private func gain(_ label: String, id: String, level: Binding<Double>) -> some View {
        HStack(spacing: 12) {
            Text(label)
            Slider(value: level, in: 0...1)
                .accessibilityLabel(label)
                .accessibilityIdentifier(id)
        }
    }

    private func timeLabel(_ interval: Double) -> String {
        let seconds = Int(max(0, interval))
        return String(format: "%d:%02d", seconds / 60, seconds % 60)
    }
}
