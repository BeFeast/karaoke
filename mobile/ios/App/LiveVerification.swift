#if DEBUG
import Foundation

enum LiveVerification {
    static let active = ProcessInfo.processInfo.arguments.contains("--ui-test-live")
    static let subject = ProcessInfo.processInfo.environment["SONERELI_QA_SUBJECT"]
    static let ticket = ProcessInfo.processInfo.environment["SONERELI_QA_TICKET"]
    static let video = ProcessInfo.processInfo.environment["SONERELI_QA_YOUTUBE_URL"]
        .flatMap(SharedVideo.validatedURL(from:))
}
#endif
