import Foundation
import Observation

enum LyricsSize: String, CaseIterable, Identifiable {
    case s, m, l
    var id: String { rawValue }
    var label: String { rawValue.uppercased() }
}

struct LastPlayed: Codable, Equatable {
    let song: SongRef
    var position: Double
    var duration: Double
    var at: Date
}

/// Singing preferences and listening memory. Per device, UserDefaults only;
/// losing them just hides Continue singing and resets the defaults.
@MainActor
@Observable
final class Prefs {
    /// The first song on a device starts Original vocals at 50%; after that
    /// every song starts at the level last chosen on this device, on the Stage
    /// slider or in Settings (Oleg 2026-11-24; web #380). Mute never changes it.
    static let defaultVocalPct = 50

    @ObservationIgnored private let store: UserDefaults

    var lyricsSize: LyricsSize { didSet { store.set(lyricsSize.rawValue, forKey: "lyricsSize") } }
    var vocalsDefault: Int { didSet { store.set(vocalsDefault, forKey: "vocalsDefault") } }
    var keepAwake: Bool { didSet { store.set(keepAwake, forKey: "keepAwake") } }
    var countIn: Bool { didSet { store.set(countIn, forKey: "countIn") } }
    var haptics: Bool { didSet { store.set(haptics, forKey: "haptics") } }
    private(set) var lastPlayed: LastPlayed?
    private(set) var playedAt: [String: Date]

    init(store: UserDefaults = .standard) {
        self.store = store
        lyricsSize = LyricsSize(rawValue: store.string(forKey: "lyricsSize") ?? "") ?? .m
        vocalsDefault = store.object(forKey: "vocalsDefault") as? Int ?? Self.defaultVocalPct
        keepAwake = store.object(forKey: "keepAwake") as? Bool ?? true
        countIn = store.object(forKey: "countIn") as? Bool ?? true
        haptics = store.object(forKey: "haptics") as? Bool ?? true
        store.removeObject(forKey: "vocalsBySong")
        lastPlayed = store.data(forKey: "lastPlayed").flatMap { try? JSONDecoder().decode(LastPlayed.self, from: $0) }
        playedAt = (store.dictionary(forKey: "playedAt") as? [String: Date]) ?? [:]
    }

    func remember(_ song: SongRef, position: Double, duration: Double) {
        let now = Date()
        lastPlayed = LastPlayed(song: song, position: position, duration: duration, at: now)
        store.set(try? JSONEncoder().encode(lastPlayed), forKey: "lastPlayed")
        if playedAt[song.token] == nil || now.timeIntervalSince(playedAt[song.token]!) > 30 {
            playedAt[song.token] = now
            if playedAt.count > 200, let oldest = playedAt.min(by: { $0.value < $1.value })?.key {
                playedAt.removeValue(forKey: oldest)
            }
            store.set(playedAt, forKey: "playedAt")
        }
    }

    func forget(token: String) {
        if lastPlayed?.song.token == token {
            lastPlayed = nil
            store.removeObject(forKey: "lastPlayed")
        }
    }

    var installId: String {
        if let saved = store.string(forKey: "feedbackInstallId"), UUID(uuidString: saved) != nil { return saved }
        let fresh = UUID().uuidString.lowercased()
        store.set(fresh, forKey: "feedbackInstallId")
        return fresh
    }

    static func clamp(_ pct: Int) -> Int { min(100, max(0, pct)) }

    #if DEBUG
    /// UI tests run on a clean, separate store.
    static func fixture() -> Prefs {
        let name = "com.befeast.sonereli.fixture"
        UserDefaults().removePersistentDomain(forName: name)
        let store = UserDefaults(suiteName: name)!
        let prefs = Prefs(store: store)
        if ProcessInfo.processInfo.arguments.contains("--ui-test-continue") {
            let song = SongRef(token: "fixture-3", title: "Borrowed Glow", artist: "Mira Vale",
                               sourceUrl: "https://www.youtube.com/watch?v=aaaaaaaaaa3", jobId: 3, duration: 40)
            prefs.remember(song, position: 12, duration: 40)
        }
        return prefs
    }
    #endif
}
