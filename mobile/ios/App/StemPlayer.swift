import AVFoundation
import MediaPlayer
import Observation
import UIKit

@MainActor
@Observable
final class StemPlayer {
    private let engine = AVAudioEngine()
    private let backing = AVAudioPlayerNode()
    private let vocals = AVAudioPlayerNode()
    private let backingFile: AVAudioFile
    private let vocalsFile: AVAudioFile
    private let audioSession = AVAudioSession.sharedInstance()
    private let title: String
    private let artist: String?
    private let coverURL: URL?
    private var artwork: MPMediaItemArtwork?

    private(set) var duration: Double
    private(set) var position = 0.0
    private(set) var isPlaying = false
    private(set) var errorMessage: String?
    /// The song played to its end (the Stage shows "One more song?").
    private(set) var ended = false
    /// Original vocals, 0–100. The backing track always plays at full gain.
    var vocalsPct = 50 { didSet { vocals.volume = Self.gain(forPct: vocalsPct) } }

    /// Perceptual (dB) curve shared with the web player: 30 dB between 1% and
    /// 100%, so 25% is a faint guide voice; 0 mutes exactly.
    static let vocalRangeDB = 30.0
    static func gain(forPct pct: Int) -> Float {
        let level = Double(min(100, max(0, pct))) / 100
        guard level > 0 else { return 0 }
        return Float(pow(10, vocalRangeDB * (level - 1) / 20))
    }

    private var startPosition = 0.0
    private var playbackGeneration = UUID()
    private var remoteTargets: [(MPRemoteCommand, Any)] = []
    private var audioObservers: [NSObjectProtocol] = []
    private var resumeAfterInterruption = false

    init(assets: SongAssets, title: String, artist: String?, coverURL: URL? = nil, vocalsPct: Int = 50) throws {
        backingFile = try AVAudioFile(forReading: assets.backing)
        vocalsFile = try AVAudioFile(forReading: assets.vocals)
        self.title = title
        self.artist = artist
        self.coverURL = coverURL
        self.vocalsPct = vocalsPct
        duration = min(
            Double(backingFile.length) / backingFile.processingFormat.sampleRate,
            Double(vocalsFile.length) / vocalsFile.processingFormat.sampleRate
        )
        guard duration.isFinite, duration > 0 else { throw CoordinatorError.invalidAudio }

        // The main mixer resamples both decoded PCM streams to its output format.
        // Frame ranges use each file's rate, and both nodes use one host-time start.
        engine.attach(backing)
        engine.attach(vocals)
        engine.connect(backing, to: engine.mainMixerNode, format: backingFile.processingFormat)
        engine.connect(vocals, to: engine.mainMixerNode, format: vocalsFile.processingFormat)
        backing.volume = 1
        vocals.volume = Self.gain(forPct: vocalsPct)
        installAudioObservers()
    }

    func play() {
        guard !isPlaying else { return }
        if position >= duration - 0.05 { position = 0 }
        ended = false
        do {
            try audioSession.setCategory(.playback, mode: .default)
            try audioSession.setActive(true)
            if !engine.isRunning { try engine.start() }
            startPosition = position
            let generation = UUID()
            playbackGeneration = generation
            let start = AVAudioTime(hostTime: mach_absolute_time() + AVAudioTime.hostTime(forSeconds: 0.08))
            schedule(vocals, file: vocalsFile, from: position)
            schedule(backing, file: backingFile, from: position) { [weak self] _ in
                Task { @MainActor in
                    guard let self, self.playbackGeneration == generation else { return }
                    self.halt()
                    self.position = self.duration
                    self.ended = true
                    self.updateNowPlaying()
                }
            }
            backing.play(at: start)
            vocals.play(at: start)
            isPlaying = true
            errorMessage = nil
            if remoteTargets.isEmpty {
                installRemoteCommands()
                loadArtwork()
            }
            updateNowPlaying()
        } catch {
            halt()
            errorMessage = L10n.t("stage.audioError")
            updateNowPlaying()
        }
    }

    func pause() {
        resumeAfterInterruption = false
        guard isPlaying else { return }
        updatePosition()
        halt()
        updateNowPlaying()
    }

    func seek(to time: Double) {
        guard time.isFinite else { return }
        let restart = isPlaying
        if restart { halt() }
        position = min(max(0, time), duration)
        ended = false
        if restart && position < duration { play() }
        else { updateNowPlaying() }
    }

    func toggle() {
        if isPlaying { pause() } else { play() }
    }

    func skip(by seconds: Double) {
        seek(to: min(duration - 0.1, max(0, position + seconds)))
    }

    func updatePosition() {
        guard isPlaying, let renderTime = backing.lastRenderTime,
              let playbackTime = backing.playerTime(forNodeTime: renderTime) else { return }
        let seconds = Double(playbackTime.sampleTime) / playbackTime.sampleRate
        position = min(duration, startPosition + max(0, seconds - backing.outputPresentationLatency))
    }

    func close() {
        pause()
        engine.stop()
        for (command, token) in remoteTargets { command.removeTarget(token) }
        if !remoteTargets.isEmpty {
            remoteTargets.removeAll()
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            try? audioSession.setActive(false, options: .notifyOthersOnDeactivation)
        }
        for observer in audioObservers { NotificationCenter.default.removeObserver(observer) }
        audioObservers.removeAll()
    }

    private func schedule(
        _ node: AVAudioPlayerNode, file: AVAudioFile, from seconds: Double,
        completion: AVAudioPlayerNodeCompletionHandler? = nil
    ) {
        let rate = file.processingFormat.sampleRate
        let from = min(file.length - 1, AVAudioFramePosition((seconds * rate).rounded()))
        let until = min(file.length, AVAudioFramePosition((duration * rate).rounded()))
        let frames = AVAudioFrameCount(clamping: max(1, until - from))
        node.scheduleSegment(file, startingFrame: from, frameCount: frames, at: nil,
                             completionCallbackType: .dataPlayedBack, completionHandler: completion)
    }

    private func halt() {
        playbackGeneration = UUID()
        backing.stop()
        vocals.stop()
        isPlaying = false
    }

    private func updateNowPlaying() {
        guard !remoteTargets.isEmpty else { return }
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: title,
            MPMediaItemPropertyArtist: [artist, "Sonereli"].compactMap { $0 }.joined(separator: " · "),
            MPMediaItemPropertyPlaybackDuration: duration,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: position,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? 1.0 : 0.0,
        ]
        if let artwork { info[MPMediaItemPropertyArtwork] = artwork }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func installRemoteCommands() {
        let center = MPRemoteCommandCenter.shared()
        let playTarget = center.playCommand.addTarget { [weak self] _ in
            Task { @MainActor in self?.play() }
            return .success
        }
        let pauseTarget = center.pauseCommand.addTarget { [weak self] _ in
            Task { @MainActor in self?.pause() }
            return .success
        }
        let toggleTarget = center.togglePlayPauseCommand.addTarget { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                if self.isPlaying { self.pause() } else { self.play() }
            }
            return .success
        }
        let seekTarget = center.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let time = (event as? MPChangePlaybackPositionCommandEvent)?.positionTime else {
                return .commandFailed
            }
            Task { @MainActor in self?.seek(to: time) }
            return .success
        }
        center.skipBackwardCommand.preferredIntervals = [5]
        center.skipForwardCommand.preferredIntervals = [5]
        let backTarget = center.skipBackwardCommand.addTarget { [weak self] _ in
            Task { @MainActor in self?.skip(by: -5) }
            return .success
        }
        let forwardTarget = center.skipForwardCommand.addTarget { [weak self] _ in
            Task { @MainActor in self?.skip(by: 5) }
            return .success
        }
        remoteTargets = [
            (center.playCommand, playTarget), (center.pauseCommand, pauseTarget),
            (center.togglePlayPauseCommand, toggleTarget), (center.changePlaybackPositionCommand, seekTarget),
            (center.skipBackwardCommand, backTarget), (center.skipForwardCommand, forwardTarget),
        ]
        for (command, _) in remoteTargets { command.isEnabled = true }
    }

    private func loadArtwork() {
        guard let coverURL else { return }
        Task { [weak self] in
            guard let result = try? await URLSession.shared.data(from: coverURL),
                  let image = UIImage(data: result.0) else { return }
            guard let self, !self.remoteTargets.isEmpty else { return }
            self.artwork = MPMediaItemArtwork(boundsSize: image.size) { _ in image }
            self.updateNowPlaying()
        }
    }

    private func installAudioObservers() {
        let center = NotificationCenter.default
        audioObservers = [
            center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] note in
                let type = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
                let options = note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
                Task { @MainActor in
                    guard let self else { return }
                    if type == AVAudioSession.InterruptionType.began.rawValue {
                        let wasPlaying = self.isPlaying
                        self.pause()
                        self.resumeAfterInterruption = wasPlaying
                    } else if type == AVAudioSession.InterruptionType.ended.rawValue {
                        let shouldResume = self.resumeAfterInterruption &&
                            AVAudioSession.InterruptionOptions(rawValue: options).contains(.shouldResume)
                        self.resumeAfterInterruption = false
                        if shouldResume { self.play() }
                    }
                }
            },
            center.addObserver(forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main) { [weak self] note in
                if note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt ==
                    AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue {
                    Task { @MainActor in self?.pause() }
                }
            },
            center.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { [weak self] _ in
                Task { @MainActor in
                    guard let self, !self.engine.isRunning else { return }
                    self.pause()
                }
            },
        ]
    }
}
