import Foundation
import Observation
import UIKit

/// The song lives above navigation: one session per app, shared by the
/// mini-player and the Stage. Minimizing the Stage never stops the sound.
@MainActor
@Observable
final class PlayerSession {
    enum State: Equatable { case idle, loading, ready, failed(String) }

    private(set) var song: SongRef?
    private(set) var player: StemPlayer?
    private(set) var lyrics: Lyrics?
    private(set) var state: State = .idle
    var stagePresented = false
    /// Mic toggle memory: the level to restore after muting.
    private(set) var lastAudibleVocals = Prefs.defaultVocalPct

    private let prefs: Prefs
    @ObservationIgnored private var assets: SongAssets?
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored private var lastSaved = Date.distantPast
    @ObservationIgnored private var api: (any SongsAPI)?

    init(prefs: Prefs) {
        self.prefs = prefs
    }

    var vocalsPct: Int { player?.vocalsPct ?? prefs.vocalsDefault }

    /// Open a song on the Stage. The same song just comes back to the front.
    func open(_ song: SongRef, api: any SongsAPI, at start: Double? = nil, present: Bool = true, autoplay: Bool = false) {
        self.api = api
        if present { stagePresented = true }
        if self.song?.token == song.token, state == .ready || state == .loading {
            if let start { player?.seek(to: start) }
            return
        }
        close(keepStage: present)
        self.song = song
        state = .loading
        loading = Task { [weak self] in await self?.load(song, api: api, start: start, autoplay: autoplay) }
    }

    func retry() {
        guard let song, let api else { return }
        open(song, api: api, present: stagePresented)
    }

    private func load(_ song: SongRef, api: any SongsAPI, start: Double?, autoplay: Bool) async {
        do {
            let downloaded = try await api.assets(for: song)
            do {
                try Task.checkCancellation()
                guard self.song?.token == song.token else {
                    downloaded.remove()
                    return
                }
                let level = prefs.vocalsDefault
                let prepared = try StemPlayer(assets: downloaded, title: song.title, artist: song.artist,
                                              coverURL: song.coverURL, vocalsPct: level)
                if level > 0 { lastAudibleVocals = level }
                assets = downloaded
                lyrics = downloaded.lyrics
                player = prepared
                if let start, start > 1, start < prepared.duration - 2 { prepared.seek(to: start) }
                state = .ready
                if autoplay { prepared.play() }
            } catch {
                downloaded.remove()
                throw error
            }
        } catch is CancellationError {
            return
        } catch {
            guard self.song?.token == song.token else { return }
            state = .failed(L10n.t("stage.audioError"))
        }
    }

    /// A level the user chose on the slider: it becomes the device start level.
    func setVocals(_ pct: Int) {
        let value = Prefs.clamp(pct)
        player?.vocalsPct = value
        if value > 0 { lastAudibleVocals = value }
        prefs.vocalsDefault = value
    }

    /// Mic toggle: 0 ↔ the previous level. It never changes the remembered level.
    func toggleVocals() {
        let value = vocalsPct > 0 ? 0 : max(lastAudibleVocals, 5)
        player?.vocalsPct = value
    }

    /// Called on the shared UI clock: refresh the position and remember it.
    func tick() {
        guard let player, let song else { return }
        player.updatePosition()
        if player.isPlaying, Date().timeIntervalSince(lastSaved) > 2 {
            lastSaved = Date()
            prefs.remember(song, position: player.position, duration: player.duration)
        }
    }

    func rememberNow() {
        guard let player, let song, player.position > 0 else { return }
        prefs.remember(song, position: player.ended ? 0 : player.position, duration: player.duration)
    }

    /// Stop and forget the current song (sign-out, song removed).
    func close(keepStage: Bool = false) {
        rememberNow()
        loading?.cancel()
        loading = nil
        player?.close()
        player = nil
        assets?.remove()
        assets = nil
        lyrics = nil
        song = nil
        state = .idle
        if !keepStage { stagePresented = false }
    }
}
