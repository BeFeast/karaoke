#if DEBUG
import AVFoundation
import Foundation

@MainActor
final class FixtureCoordinator: SongsAPI {
    static let youtube = URL(string: "https://www.youtube.com/watch?v=tlHn-L6_f6o")!

    func identity() async throws -> Identity {
        Identity(subject: "fixture-owner", email: nil, displayName: "Тестовый аккаунт", state: "clerk_user")
    }

    func jobs(before: Int?) async throws -> [SongJob] {
        before == nil ? [song(id: 1, state: .completed)] : []
    }

    func submit(_ url: URL) async throws -> SongJob { song(id: 2, state: .queued) }

    func status(_ id: Int) async throws -> SongJob { song(id: id, state: .completed) }

    func subscribe(to id: Int, receive: (JobEvent) -> Bool) async throws {
        for (status, progress) in [(JobStatus.downloading, 10), (.separating, 40), (.transcribing, 70), (.completed, 100)] {
            try await Task.sleep(for: .seconds(1))
            if !receive(JobEvent(type: "stage_change", jobId: id, status: status, progress: progress, error: nil)) {
                return
            }
        }
    }

    func assets(for job: SongJob) async throws -> SongAssets {
        try await Task.detached {
            let folder = try SongAssets.temporaryFolder()
            let backing = folder.appendingPathComponent("backing.caf")
            let vocals = folder.appendingPathComponent("vocals.caf")
            do {
                try Self.tone(frequency: 392, rate: 44_100, at: backing)
                try Self.tone(frequency: 262, rate: 48_000, at: vocals)
                let lyrics = Lyrics(synced: true, lines: [
                    .init(t: 0, text: "בוקר טוב", speech: nil),
                    .init(t: 2, text: "בשעה 12:30 נצא לשיר עם Sonereli ונמשיך יחד אל תוך הלילה, עד שיגיע הבוקר ונשיר שוב את כל המילים.", speech: nil),
                    .init(t: 6, text: "Sing this line from left to right", speech: nil),
                    .init(t: 10, text: "עכשיו נשיר שוב", speech: nil),
                    .init(t: 14, text: "השיר הזה הוא שלנו", speech: nil),
                    .init(t: 17, text: "ניפגש מחר", speech: nil),
                ], plain: nil)
                return SongAssets(folder: folder, backing: backing, vocals: vocals, lyrics: lyrics)
            } catch {
                try? FileManager.default.removeItem(at: folder)
                throw error
            }
        }.value
    }

    private func song(id: Int, state: JobStatus) -> SongJob {
        SongJob(
            id: id, jobToken: "fixture", sourceUrl: Self.youtube.absoluteString,
            title: id == 1 ? "שיר לבדיקה" : "Песня из YouTube", artist: "Sonereli",
            track: nil, status: state, progress: state == .completed ? 100 : 0, error: nil
        )
    }

    private nonisolated static func tone(frequency: Double, rate: Double, at path: URL) throws {
        let frames = AVAudioFrameCount(rate * 20)
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
