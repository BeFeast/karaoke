import AVFoundation
import MediaPlayer
import Observation

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

    private(set) var duration: Double
    private(set) var position = 0.0
    private(set) var isPlaying = false
    private(set) var errorMessage: String?
    var backingLevel = 1.0 { didSet { backing.volume = Float(backingLevel) } }
    var vocalsLevel = 0.0 { didSet { vocals.volume = Float(vocalsLevel) } }

    private var startPosition = 0.0
    private var playbackGeneration = UUID()
    private var remoteTargets: [(MPRemoteCommand, Any)] = []
    private var audioObservers: [NSObjectProtocol] = []
    private var resumeAfterInterruption = false

    init(assets: SongAssets, title: String, artist: String?) throws {
        backingFile = try AVAudioFile(forReading: assets.backing)
        vocalsFile = try AVAudioFile(forReading: assets.vocals)
        self.title = title
        self.artist = artist
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
        backing.volume = Float(backingLevel)
        vocals.volume = Float(vocalsLevel)
        installAudioObservers()
    }

    func play() {
        guard !isPlaying else { return }
        if position >= duration { position = 0 }
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
                    self.updateNowPlaying()
                }
            }
            backing.play(at: start)
            vocals.play(at: start)
            isPlaying = true
            errorMessage = nil
            if remoteTargets.isEmpty { installRemoteCommands() }
            updateNowPlaying()
        } catch {
            halt()
            errorMessage = "Не удалось включить звук. Попробуйте ещё раз."
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
        if restart && position < duration { play() }
        else { updateNowPlaying() }
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
            MPMediaItemPropertyPlaybackDuration: duration,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: position,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? 1.0 : 0.0,
        ]
        if let artist { info[MPMediaItemPropertyArtist] = artist }
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
        remoteTargets = [
            (center.playCommand, playTarget), (center.pauseCommand, pauseTarget),
            (center.togglePlayPauseCommand, toggleTarget), (center.changePlaybackPositionCommand, seekTarget),
        ]
        for (command, _) in remoteTargets { command.isEnabled = true }
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
