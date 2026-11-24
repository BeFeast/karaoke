#if DEBUG
import AVFoundation
import Foundation

/// Offline coordinator for UI tests and screenshots: a small library, a
/// song being prepared, a catalog and generated stems with lyrics.
@MainActor
final class FixtureCoordinator: SongsAPI {
    static let youtube = URL(string: "https://www.youtube.com/watch?v=tlHn-L6_f6o")!
    var shareBase: URL { URL(string: "https://sonereli.example")! }

    private var library: [SongJob] = FixtureCoordinator.seed()

    private nonisolated static func seed() -> [SongJob] {
        let args = ProcessInfo.processInfo.arguments
        var jobs = [
            song(id: 1, title: "שיר לבדיקה", artist: "Sonereli", state: .completed, duration: 20),
            song(id: 3, title: "Borrowed Glow", artist: "Mira Vale", state: .completed, duration: 40),
            song(id: 4, title: "Neon After Midnight", artist: "The Low Lamps", state: .completed, duration: 40),
        ]
        if args.contains("--ui-test-preparing") {
            jobs.insert(song(id: 5, title: "A Pocket of Blue", artist: "Theo Rao", state: .separating, progress: 46, duration: nil), at: 0)
        }
        if args.contains("--ui-test-empty") { return [] }
        return jobs
    }

    func identity() async throws -> Identity {
        Identity(subject: "fixture-owner", email: nil, displayName: L10n.isRussian ? "Тестовый аккаунт" : "Test account",
                 state: "clerk_user")
    }

    func jobs(before: Int?) async throws -> [SongJob] { before == nil ? library : [] }

    func submit(_ url: URL) async throws -> SongJob {
        if let id = YouTube.id(of: url.absoluteString), id == "ccccccccc01" {
            // A catalog-ready source comes back completed (reuse, no GPU).
            let job = Self.song(id: 9, title: "אור בחלון", artist: "נועה הדר", state: .completed, duration: 20,
                                source: url.absoluteString)
            library.insert(job, at: 0)
            return job
        }
        let job = Self.song(id: 2, title: L10n.isRussian ? "Песня из YouTube" : "Song from YouTube", artist: "Sonereli",
                            state: .queued, duration: 20, source: url.absoluteString)
        library.insert(job, at: 0)
        return job
    }

    func upload(_ file: URL) async throws -> SongJob { try await submit(Self.youtube) }

    func status(_ id: Int) async throws -> SongJob {
        if id == 5 { return Self.song(id: 5, title: "A Pocket of Blue", artist: "Theo Rao", state: .separating, progress: 46, duration: nil) }
        var job = library.first { $0.id == id } ?? Self.song(id: id, title: "Song", artist: nil, state: .completed, duration: 20)
        job.status = .completed
        job.progress = 100
        return job
    }

    func cancel(_ id: Int) async throws -> SongJob {
        var job = try await status(id)
        job.status = .cancelled
        return job
    }

    func delete(_ id: Int) async throws { library.removeAll { $0.id == id } }

    func subscribe(to id: Int, receive: (JobEvent) -> Bool) async throws {
        if id == 5 {
            // The preparing row stays at "Separating vocals · 46%" for screenshots.
            _ = receive(JobEvent(type: "stage_change", jobId: id, status: .separating, progress: 46, error: nil))
            try await Task.sleep(for: .seconds(3600))
            return
        }
        for (status, progress) in [(JobStatus.downloading, 10), (.separating, 40), (.transcribing, 70), (.completed, 100)] {
            try await Task.sleep(for: .seconds(1))
            if !receive(JobEvent(type: "stage_change", jobId: id, status: status, progress: progress, error: nil)) {
                return
            }
        }
    }

    func assets(for song: SongRef) async throws -> SongAssets {
        let hebrew = song.jobId == 1 || song.jobId == 2 || song.jobId == 9 || song.isCatalogAsset
        let length = hebrew ? 20.0 : 40.0
        return try await Task.detached {
            let folder = try SongAssets.temporaryFolder()
            let backing = folder.appendingPathComponent("backing.caf")
            let vocals = folder.appendingPathComponent("vocals.caf")
            do {
                try Self.tone(frequency: 392, rate: 44_100, seconds: length, at: backing)
                try Self.tone(frequency: 262, rate: 48_000, seconds: length, at: vocals)
                return SongAssets(folder: folder, backing: backing, vocals: vocals,
                                  lyrics: hebrew ? Self.hebrewLyrics : Self.englishLyrics)
            } catch {
                try? FileManager.default.removeItem(at: folder)
                throw error
            }
        }.value
    }

    func catalog(query: String, axis: String?, value: String?) async throws -> CatalogPage {
        let all: [CatalogSong] = [
            Self.catalogSong(1, "אור בחלון", "נועה הדר", "he", "Hebrew hits", ready: true),
            Self.catalogSong(2, "גשם על הגג", "אור בן עמי", "he", "Hebrew hits", ready: false),
            Self.catalogSong(3, "שקט במדרגות", "דנה ושי", "he", "Hebrew hits", ready: true),
            Self.catalogSong(4, "Borrowed Glow", "Mira Vale", "en", "English hits", ready: true),
            Self.catalogSong(5, "Neon After Midnight", "The Low Lamps", "en", "English hits", ready: true),
            Self.catalogSong(6, "Two Quiet Rooms", "Nia & Sol", "en", "English hits", ready: false),
            Self.catalogSong(7, "Paper Moon Duet", "Ari & Lee", "en", "Duets", ready: false),
        ]
        let terms = query.lowercased().split(separator: " ").map(String.init)
        var songs = all.filter { song in
            terms.allSatisfy { "\(song.title) \(song.artist)".lowercased().contains($0) }
        }
        if axis == "collection", let value { songs = songs.filter { $0.collections.contains(value) } }
        var collections: [String: Int] = [:]
        for song in songs { for name in song.collections { collections[name, default: 0] += 1 } }
        return CatalogPage(songs: songs, facets: ["collection": collections, "genre": [:], "decade": [:], "language": [:]])
    }

    func feedback(_ report: FeedbackReport) async throws -> Int {
        try await Task.sleep(for: .milliseconds(400))
        return 42
    }

    private static func catalogSong(_ id: Int, _ title: String, _ artist: String, _ language: String,
                                    _ shelf: String, ready: Bool) -> CatalogSong {
        let video = "ccccccccc" + String(format: "%02d", id)
        return CatalogSong(id: id, title: title, artist: artist, version: "Studio", language: language, genres: [],
                           collections: [shelf], decade: nil, sourceUrl: "https://www.youtube.com/watch?v=\(video)",
                           thumbnailUrl: nil, stageToken: ready ? "cat.\(id)" : nil)
    }

    private nonisolated static func song(id: Int, title: String, artist: String?, state: JobStatus, progress: Int? = nil,
                             duration: Double?, source: String? = nil) -> SongJob {
        SongJob(
            id: id, jobToken: "fixture-\(id)", sourceUrl: source ?? "https://www.youtube.com/watch?v=aaaaaaaaaa\(id % 10)",
            title: title, artist: artist, track: nil, status: state,
            progress: progress ?? (state == .completed ? 100 : 0), error: nil, duration: duration
        )
    }

    nonisolated static let hebrewLyrics = Lyrics(synced: true, lines: [
        .init(t: 0, text: "בוקר טוב"),
        .init(t: 2, text: "בשעה 12:30 נצא לשיר עם Sonereli ונמשיך יחד אל תוך הלילה, עד שיגיע הבוקר ונשיר שוב את כל המילים."),
        .init(t: 6, text: "Sing this line from left to right"),
        .init(t: 10, text: "עכשיו נשיר שוב"),
        .init(t: 14, text: "השיר הזה הוא שלנו"),
        .init(t: 17, text: "ניפגש מחר"),
    ], plain: nil)

    nonisolated static let englishLyrics: Lyrics = {
        func line(_ t: Double, _ text: String) -> Lyrics.Line {
            let parts = text.split(separator: " ").map(String.init)
            let step = 2.4 / Double(max(1, parts.count))
            let words = parts.enumerated().map { Lyrics.Word(t: t + Double($0.offset) * step, d: step, text: $0.element) }
            return Lyrics.Line(t: t, text: text, words: words, end: t + 2.6)
        }
        return Lyrics(synced: true, lines: [
            line(4, "You leave your shoes beside the door"),
            line(7, "The city folds its paper wings"),
            line(10, "I’ll hold the night until you’re home"),
            line(13, "And leave a little light on"),
            line(16, "We can take the long way"),
            line(28, "The water keeps a quiet count of every little step we take on the long way back home tonight"),
            line(32, "And all the borrowed light is ours"),
            line(35, "So leave a little light on"),
        ], plain: nil)
    }()

    private nonisolated static func tone(frequency: Double, rate: Double, seconds: Double, at path: URL) throws {
        let frames = AVAudioFrameCount(rate * seconds)
        guard let format = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
              let samples = buffer.floatChannelData?[0] else { throw CoordinatorError.invalidAudio }
        buffer.frameLength = frames
        for index in 0..<Int(frames) {
            let time = Double(index) / rate
            let envelope = max(0, 1 - time.truncatingRemainder(dividingBy: 0.5) * 5)
            samples[index] = Float(sin(2 * .pi * frequency * time) * 0.12 * envelope)
        }
        let file = try AVAudioFile(forWriting: path, settings: format.settings)
        try file.write(from: buffer)
    }
}
#endif
