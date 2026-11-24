import Foundation

struct SharedVideo: Codable, Identifiable, Equatable {
    let id: UUID
    let url: URL

    init(url: URL) {
        id = UUID()
        self.url = url
    }

    static func validatedURL(from text: String) -> URL? {
        let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue)
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        return detector?.matches(in: text, range: range).compactMap { match -> URL? in
            guard let url = match.url,
                  let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
                  ["https", "http"].contains(parts.scheme?.lowercased() ?? ""),
                  parts.user == nil, parts.password == nil, parts.port == nil,
                  let host = parts.host?.lowercased() else { return nil }

            let path = parts.path.split(separator: "/")
            let id: String?
            switch host {
            case "youtu.be", "www.youtu.be":
                id = path.count == 1 ? path.first.map(String.init) : nil
            case "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com":
                if parts.path == "/watch" {
                    id = parts.queryItems?.first(where: { $0.name == "v" })?.value
                } else if path.count == 2, ["shorts", "live", "embed"].contains(path[0]) {
                    id = String(path[1])
                } else {
                    id = nil
                }
            default:
                id = nil
            }

            guard let id, id.range(of: #"^[A-Za-z0-9_-]{11}$"#, options: .regularExpression) != nil else {
                return nil
            }
            return URL(string: "https://www.youtube.com/watch?v=\(id)")
        }.first
    }
}

enum ShareInbox {
    private static let group = "group.com.befeast.sonereli"

    static func push(_ video: SharedVideo) throws {
        let folder = try directory()
        let file = folder.appendingPathComponent(video.id.uuidString).appendingPathExtension("json")
        try JSONEncoder().encode(video).write(to: file, options: .atomic)
    }

    static func pending() -> SharedVideo? {
        guard let folder = try? directory(),
              let files = try? FileManager.default.contentsOfDirectory(
                at: folder, includingPropertiesForKeys: [.creationDateKey], options: .skipsHiddenFiles
              ) else { return nil }
        return files.filter { $0.pathExtension == "json" }.sorted {
            let first = (try? $0.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? .distantPast
            let second = (try? $1.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? .distantPast
            return first == second ? $0.lastPathComponent < $1.lastPathComponent : first < second
        }.lazy.compactMap { file in
            guard let data = try? Data(contentsOf: file),
                  let video = try? JSONDecoder().decode(SharedVideo.self, from: data),
                  file.deletingPathExtension().lastPathComponent == video.id.uuidString,
                  SharedVideo.validatedURL(from: video.url.absoluteString) == video.url else { return nil }
            return video
        }.first
    }

    #if DEBUG
    static func resetFixture() {
        guard ProcessInfo.processInfo.arguments.contains("--ui-test-fixture"),
              let folder = try? directory() else { return }
        try? FileManager.default.removeItem(at: folder)
    }
    #endif

    static func remove(_ video: SharedVideo) {
        guard let folder = try? directory() else { return }
        try? FileManager.default.removeItem(
            at: folder.appendingPathComponent(video.id.uuidString).appendingPathExtension("json")
        )
    }

    private static func directory() throws -> URL {
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--ui-test-fixture") {
            let folder = FileManager.default.temporaryDirectory.appendingPathComponent("FixtureShareInbox", isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            return folder
        }
        #endif
        guard let container = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group) else {
            throw CocoaError(.fileNoSuchFile)
        }
        let folder = container.appendingPathComponent("ShareInbox", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder
    }
}
