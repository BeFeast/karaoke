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

    var isTerminal: Bool { self == .completed || self == .failed || self == .cancelled }

    var title: String {
        switch self {
        case .queued: "В очереди"
        case .downloading: "Загрузка видео"
        case .separating: "Разделение дорожек"
        case .transcribing: "Подготовка текста"
        case .finalizing: "Сохранение песни"
        case .completed: "Готово"
        case .failed: "Подготовка не удалась"
        case .cancelled: "Отменено"
        }
    }
}

struct SongJob: Decodable, Identifiable {
    let id: Int
    let jobToken: String
    let sourceUrl: String
    let title: String?
    let artist: String?
    let track: String?
    var status: JobStatus
    var progress: Int
    var error: String?

    var name: String { track ?? title ?? "Песня №\(id)" }

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

struct JobEvent: Decodable {
    let type: String
    let jobId: Int
    let status: JobStatus?
    let progress: Int?
    let error: String?
}

struct Lyrics: Decodable {
    struct Line: Decodable {
        let t: Double
        let text: String
        let speech: Bool?
    }

    let synced: Bool
    let lines: [Line]?
    let plain: String?

    var timedLines: [Line] {
        guard synced else { return [] }
        return (lines ?? []).filter { $0.speech != true }
    }

    func activeIndex(at time: Double, in lines: [Line]) -> Int? {
        var first = 0
        var end = lines.count
        while first < end {
            let mid = first + (end - first) / 2
            if lines[mid].t <= time { first = mid + 1 } else { end = mid }
        }
        return first > 0 ? first - 1 : nil
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

enum CoordinatorError: LocalizedError {
    case missingSession, invalidResponse, http(Int, String?), fileTooLarge, invalidAudio

    var errorDescription: String? {
        switch self {
        case .missingSession, .http(401, _): "Сессия недоступна. Войдите снова."
        case .invalidResponse: "Sonereli получил неожиданный ответ сервера."
        case let .http(code, message): message ?? "Ошибка запроса (HTTP \(code))."
        case .fileTooLarge: "Дорожка превышает допустимый размер."
        case .invalidAudio: "В этой песне не удалось открыть обе дорожки."
        }
    }
}

@MainActor
protocol SongsAPI {
    func identity() async throws -> Identity
    func jobs(before: Int?) async throws -> [SongJob]
    func submit(_ url: URL) async throws -> SongJob
    func status(_ id: Int) async throws -> SongJob
    func subscribe(to id: Int, receive: (JobEvent) -> Bool) async throws
    func assets(for job: SongJob) async throws -> SongAssets
}

@MainActor
struct Coordinator: SongsAPI {
    static let origin = URL(string: "https://sonereli.oklabs.uk")!
    static let pageSize = 50

    private static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil
        config.httpShouldSetCookies = false
        config.timeoutIntervalForRequest = 30
        return URLSession(configuration: config)
    }()

    static func publishableKey() async throws -> String {
        struct Config: Decodable { let clerkPublishableKey: String? }
        let (data, response) = try await session.data(from: origin.appendingPathComponent("config"))
        try check(response, data: data)
        guard let key = try decoder().decode(Config.self, from: data).clerkPublishableKey,
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

    func status(_ id: Int) async throws -> SongJob { try await get(path: "jobs/\(id)/status") }

    func subscribe(to id: Int, receive: (JobEvent) -> Bool) async throws {
        var endpoint = URLComponents(url: Self.origin.appendingPathComponent("ws/\(id)"), resolvingAgainstBaseURL: false)!
        endpoint.scheme = "wss"
        let socket = Self.session.webSocketTask(with: endpoint.url!)
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

    func assets(for job: SongJob) async throws -> SongAssets {
        let path = "share/\(job.jobToken)"
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

    private static func decoder() -> JSONDecoder {
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
