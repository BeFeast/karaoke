import ClerkKit
import Foundation

struct Identity: Decodable {
    let subject: String
    let email: String?
    let displayName: String?
    let state: String

    var label: String { displayName ?? email ?? "Sonereli" }
}

enum JobStatus: String, Decodable {
    case queued, downloading, separating, transcribing, finalizing, completed, failed, cancelled

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        // An unknown in-flight status must not break the whole list.
        self = JobStatus(rawValue: raw) ?? .queued
    }

    var isTerminal: Bool { self == .completed || self == .failed || self == .cancelled }
    var isActive: Bool { !isTerminal }
    var title: String { L10n.t("status.\(rawValue)") }
}

struct SongJob: Decodable, Identifiable, Equatable {
    let id: Int
    let jobToken: String
    let sourceUrl: String
    let title: String?
    let artist: String?
    let track: String?
    var status: JobStatus
    var progress: Int
    var error: String?
    var duration: Double?
    var completedAt: String?
    var createdAt: String?

    var name: String { track ?? title ?? L10n.t("song.untitled", ["id": id]) }

    /// Uploads (`upload://…`) cannot be resubmitted by URL.
    var retryURL: URL? {
        guard !sourceUrl.hasPrefix("upload://") else { return nil }
        return URL(string: sourceUrl)
    }

    var song: SongRef {
        SongRef(token: jobToken, title: name, artist: artist, sourceUrl: sourceUrl, jobId: id, duration: duration)
    }

    mutating func update(from event: JobEvent) {
        guard !status.isTerminal, event.jobId == id, let next = event.status,
              let progress = event.progress else { return }
        if event.type == "stage_change" || (event.type == "heartbeat" && next == status) {
            status = next
            self.progress = progress
            error = event.error
        }
    }
}

/// What the player plays: an owner job or a catalog asset (`cat.N` token).
struct SongRef: Codable, Equatable, Hashable {
    let token: String
    let title: String
    let artist: String?
    let sourceUrl: String?
    let jobId: Int?
    var duration: Double?

    var isCatalogAsset: Bool { token.hasPrefix("cat.") }
    var coverURL: URL? { sourceUrl.flatMap(YouTube.thumbnail(for:)) }
}

enum YouTube {
    static func id(of text: String) -> String? {
        guard let url = SharedVideo.validatedURL(from: text),
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        return parts.queryItems?.first(where: { $0.name == "v" })?.value
    }

    static func thumbnail(for source: String) -> URL? {
        #if DEBUG
        // Fixture covers use the brand placeholder; no network in screenshots.
        if ProcessInfo.processInfo.arguments.contains("--ui-test-fixture") { return nil }
        #endif
        return id(of: source).flatMap { URL(string: "https://i.ytimg.com/vi/\($0)/mqdefault.jpg") }
    }
}

struct JobEvent: Decodable {
    let type: String
    let jobId: Int
    let status: JobStatus?
    let progress: Int?
    let error: String?
}

struct Lyrics: Decodable {
    struct Word: Decodable {
        let t: Double
        let d: Double?
        let text: String
    }

    struct Line: Decodable {
        let t: Double
        let text: String
        let speech: Bool?
        var words: [Word]?
        var end: Double?

        init(t: Double, text: String, speech: Bool? = nil, words: [Word]? = nil, end: Double? = nil) {
            self.t = t
            self.text = text
            self.speech = speech
            self.words = words
            self.end = end
        }
    }

    let synced: Bool
    let lines: [Line]?
    let plain: String?

    /// Every synced line, speech included (speech is shown dimmed, never active).
    var timedLines: [Line] {
        guard synced else { return [] }
        return lines ?? []
    }

    var plainLines: [String] {
        (plain ?? "").components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }
}

struct SongAssets {
    let folder: URL
    let backing: URL
    let vocals: URL
    let lyrics: Lyrics

    func remove() { try? FileManager.default.removeItem(at: folder) }

    static func temporaryFolder() throws -> URL {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("SonereliStage", isDirectory: true)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder
    }

    static func clearPreviousRun() {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("SonereliStage", isDirectory: true)
        try? FileManager.default.removeItem(at: folder)
    }
}

struct CatalogSong: Decodable, Identifiable, Equatable {
    let id: Int
    let title: String
    let artist: String
    let version: String
    let language: String
    let genres: [String]
    let collections: [String]
    let decade: Int?
    let sourceUrl: String
    let thumbnailUrl: String?
    let stageToken: String?

    var song: SongRef? {
        stageToken.map { SongRef(token: $0, title: title, artist: artist, sourceUrl: sourceUrl, jobId: nil) }
    }

    var coverURL: URL? {
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--ui-test-fixture") { return nil }
        #endif
        return thumbnailUrl.flatMap(URL.init(string:))
    }
}

struct CatalogPage: Decodable {
    let songs: [CatalogSong]
    let facets: [String: [String: Int]]
}

/// One report for the pomoi feedback contract (#344).
struct FeedbackReport {
    var text: String
    var kind: String
    var installId: String
    var meta: [String: Any]
    var screenshot: Data?
}

enum CoordinatorError: LocalizedError {
    case missingSession, invalidResponse, http(Int, String?), fileTooLarge, invalidAudio

    var errorDescription: String? {
        switch self {
        case .missingSession, .http(401, _): L10n.t("err.session")
        case .invalidResponse: L10n.t("err.generic")
        case let .http(code, message):
            [400, 404, 409, 422].contains(code) ? L10n.t("err.source") : (message ?? L10n.t("err.generic"))
        case .fileTooLarge: L10n.t("err.tooLarge")
        case .invalidAudio: L10n.t("stage.audioError")
        }
    }
}

@MainActor
protocol SongsAPI {
    func identity() async throws -> Identity
    func jobs(before: Int?) async throws -> [SongJob]
    func submit(_ url: URL) async throws -> SongJob
    func upload(_ file: URL) async throws -> SongJob
    func status(_ id: Int) async throws -> SongJob
    func cancel(_ id: Int) async throws -> SongJob
    func delete(_ id: Int) async throws
    func subscribe(to id: Int, receive: (JobEvent) -> Bool) async throws
    func assets(for song: SongRef) async throws -> SongAssets
    func catalog(query: String, axis: String?, value: String?) async throws -> CatalogPage
    func feedback(_ report: FeedbackReport) async throws -> Int
    var shareBase: URL { get }
}

@MainActor
struct Coordinator: SongsAPI {
    static let origin = URL(string: "https://sonereli.oklabs.uk")!
    static let pageSize = 50
    static var feedbackBase = URL(string: "https://pomoi.co")!

    var shareBase: URL { Self.origin }

    private static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil
        config.httpShouldSetCookies = false
        config.timeoutIntervalForRequest = 30
        return URLSession(configuration: config)
    }()

    static func publishableKey() async throws -> String {
        struct Config: Decodable {
            let clerkPublishableKey: String?
            let feedbackBaseUrl: String?
        }
        let (data, response) = try await session.data(from: origin.appendingPathComponent("config"))
        try check(response, data: data)
        let config = try decoder().decode(Config.self, from: data)
        if let base = config.feedbackBaseUrl.flatMap(URL.init(string:)), base.scheme == "https" {
            feedbackBase = base
        }
        guard let key = config.clerkPublishableKey,
              key.hasPrefix("pk_live_") || key.hasPrefix("pk_test_") else {
            throw CoordinatorError.invalidResponse
        }
        return key
    }

    func identity() async throws -> Identity { try await get(path: "me") }

    func jobs(before: Int?) async throws -> [SongJob] {
        let query = [URLQueryItem(name: "limit", value: String(Self.pageSize))] +
            (before.map { [URLQueryItem(name: "before_id", value: String($0))] } ?? [])
        return try await get(path: "jobs", query: query)
    }

    func submit(_ url: URL) async throws -> SongJob {
        struct Payload: Encodable { let url: URL }
        var request = try await request(to: Self.origin.appendingPathComponent("jobs"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(Payload(url: url))
        // An ambiguous transport error must not automatically submit a second paid job.
        return try await decode(request)
    }

    func upload(_ file: URL) async throws -> SongJob {
        let boundary = "sonereli-\(UUID().uuidString)"
        let body = try SongAssets.temporaryFolder().appendingPathComponent("upload.multipart")
        defer { try? FileManager.default.removeItem(at: body.deletingLastPathComponent()) }
        try Multipart.write(file: file, field: "file", boundary: boundary, to: body)
        var request = try await request(to: Self.origin.appendingPathComponent("jobs/upload"))
        request.httpMethod = "POST"
        request.timeoutInterval = 600
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await Self.session.upload(for: request, fromFile: body)
        try Self.check(response, data: data)
        return try Self.decoder().decode(SongJob.self, from: data)
    }

    func status(_ id: Int) async throws -> SongJob { try await get(path: "jobs/\(id)/status") }

    func cancel(_ id: Int) async throws -> SongJob {
        var request = try await request(to: Self.origin.appendingPathComponent("jobs/\(id)/cancel"))
        request.httpMethod = "POST"
        return try await decode(request)
    }

    func delete(_ id: Int) async throws {
        var request = try await request(to: Self.origin.appendingPathComponent("jobs/\(id)"))
        request.httpMethod = "DELETE"
        let (data, response) = try await Self.session.data(for: request)
        try Self.check(response, data: data)
    }

    func subscribe(to id: Int, receive: (JobEvent) -> Bool) async throws {
        var endpoint = URLComponents(url: Self.origin.appendingPathComponent("ws/\(id)"), resolvingAgainstBaseURL: false)!
        endpoint.scheme = "wss"
        let handshake = try await request(to: endpoint.url!)
        let socket = Self.session.webSocketTask(with: handshake)
        socket.resume()
        defer { socket.cancel(with: .goingAway, reason: nil) }
        try await withTaskCancellationHandler {
            while !Task.isCancelled {
                let message = try await socket.receive()
                let data: Data
                switch message {
                case let .string(text): data = Data(text.utf8)
                case let .data(bytes): data = bytes
                @unknown default: continue
                }
                if let event = try? Self.decoder().decode(JobEvent.self, from: data), !receive(event) {
                    return
                }
            }
        } onCancel: {
            socket.cancel(with: .goingAway, reason: nil)
        }
    }

    func assets(for song: SongRef) async throws -> SongAssets {
        let path = "share/\(song.token)"
        let folder = try SongAssets.temporaryFolder()
        let backing = folder.appendingPathComponent("karaoke.mp3")
        let vocals = folder.appendingPathComponent("vocals.mp3")
        do {
            async let backingFile: Void = download("\(path)/karaoke.mp3", to: backing)
            async let vocalsFile: Void = download("\(path)/vocals.mp3", to: vocals)
            async let lyrics: Lyrics = get(path: "\(path)/lyrics")
            let (_, _, document) = try await (backingFile, vocalsFile, lyrics)
            return SongAssets(folder: folder, backing: backing, vocals: vocals, lyrics: document)
        } catch {
            try? FileManager.default.removeItem(at: folder)
            throw error
        }
    }

    func catalog(query: String, axis: String?, value: String?) async throws -> CatalogPage {
        var items: [URLQueryItem] = []
        if !query.isEmpty { items.append(URLQueryItem(name: "q", value: query)) }
        if let axis, let value { items.append(URLQueryItem(name: axis, value: value)) }
        return try await get(path: "catalog", query: items)
    }

    func feedback(_ report: FeedbackReport) async throws -> Int {
        let boundary = "sonereli-\(UUID().uuidString)"
        var fields: [(String, String)] = [
            ("text", report.text), ("kind", report.kind), ("install_id", report.installId),
        ]
        let meta = try JSONSerialization.data(withJSONObject: report.meta, options: [.sortedKeys])
        fields.append(("meta", String(decoding: meta, as: UTF8.self)))
        var request = URLRequest(url: Self.feedbackBase.appendingPathComponent("api/feedback/karaoke"))
        request.httpMethod = "POST"
        request.timeoutInterval = 40
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = Multipart.body(fields: fields, png: report.screenshot, boundary: boundary)
        let (data, response) = try await Self.session.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 201 else { throw CoordinatorError.invalidResponse }
        struct Receipt: Decodable { let issueNumber: Int }
        let receipt = try Self.decoder().decode(Receipt.self, from: data)
        guard receipt.issueNumber > 0 else { throw CoordinatorError.invalidResponse }
        return receipt.issueNumber
    }

    private func get<T: Decodable>(path: String, query: [URLQueryItem] = []) async throws -> T {
        var endpoint = URLComponents(url: Self.origin.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { endpoint.queryItems = query }
        return try await decode(request(to: endpoint.url!))
    }

    private func request(to url: URL) async throws -> URLRequest {
        guard let token = try await Clerk.shared.auth.getToken() else {
            throw CoordinatorError.missingSession
        }
        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return request
    }

    private func decode<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await Self.session.data(for: request)
        try Self.check(response, data: data)
        return try Self.decoder().decode(T.self, from: data)
    }

    private func download(_ path: String, to target: URL) async throws {
        let request = try await request(to: Self.origin.appendingPathComponent(path))
        let limit = StemDownloadLimit()
        let (local, response) = try await Self.session.download(for: request, delegate: limit)
        try Self.check(response)
        let size = try local.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size <= limit.maxBytes else { throw CoordinatorError.fileTooLarge }
        try Task.checkCancellation()
        try FileManager.default.moveItem(at: local, to: target)
    }

    static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    private static func check(_ response: URLResponse, data: Data? = nil) throws {
        guard let response = response as? HTTPURLResponse else { throw CoordinatorError.invalidResponse }
        guard (200..<300).contains(response.statusCode) else {
            struct Detail: Decodable { let detail: String }
            let detail = data.flatMap { try? JSONDecoder().decode(Detail.self, from: $0).detail }
            throw CoordinatorError.http(response.statusCode, detail)
        }
    }
}

enum Multipart {
    static func body(fields: [(String, String)], png: Data?, boundary: String) -> Data {
        var body = Data()
        for (name, value) in fields {
            body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n".utf8))
            body.append(Data(value.utf8))
            body.append(Data("\r\n".utf8))
        }
        if let png {
            body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"screenshot\"; filename=\"feedback.png\"\r\nContent-Type: image/png\r\n\r\n".utf8))
            body.append(png)
            body.append(Data("\r\n".utf8))
        }
        body.append(Data("--\(boundary)--\r\n".utf8))
        return body
    }

    /// Streams a picked file into a multipart body on disk (uploads can be large).
    static func write(file: URL, field: String, boundary: String, to target: URL) throws {
        let name = file.lastPathComponent.replacingOccurrences(of: "\"", with: "")
        FileManager.default.createFile(atPath: target.path, contents: nil)
        let out = try FileHandle(forWritingTo: target)
        defer { try? out.close() }
        let scoped = file.startAccessingSecurityScopedResource()
        defer { if scoped { file.stopAccessingSecurityScopedResource() } }
        let input = try FileHandle(forReadingFrom: file)
        defer { try? input.close() }
        try out.write(contentsOf: Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(field)\"; filename=\"\(name)\"\r\nContent-Type: application/octet-stream\r\n\r\n".utf8))
        var total = 0
        while let chunk = try input.read(upToCount: 1 << 20), !chunk.isEmpty {
            total += chunk.count
            if total > 200 * 1024 * 1024 { throw CoordinatorError.fileTooLarge }
            try out.write(contentsOf: chunk)
        }
        try out.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
    }
}

private final class StemDownloadLimit: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    let maxBytes = 64 * 1024 * 1024

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {}

    func urlSession(
        _ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
        totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64
    ) {
        if totalBytesWritten > maxBytes || totalBytesExpectedToWrite > maxBytes { downloadTask.cancel() }
    }
}
