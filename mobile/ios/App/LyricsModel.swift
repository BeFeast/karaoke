import SwiftUI

/// Pure lyric timing for the Stage (port of web stageLyricsModel / stage-core).
enum LyricsModel {
    enum Role { case active, next, ahead, past, speech }

    struct Cue: Equatable {
        enum Kind { case intro, instrumental }
        let kind: Kind
        /// Index of the line the cue sits above.
        let before: Int
        let start: Double
        let end: Double
    }

    /// A music break this long gets its own "Instrumental · 0:15" cue.
    static let instrumentalMin = 8.0
    /// The active line's top sits at this share of the lyrics viewport.
    static let anchor = 0.28
    /// A scrub release this close to a line start lands on the line start.
    static let snap = 0.6

    /// Index of the sung line playing at `time` (speech lines never become active).
    static func activeIndex(at time: Double, in lines: [Lyrics.Line]) -> Int? {
        var first = 0
        var end = lines.count
        while first < end {
            let mid = first + (end - first) / 2
            if lines[mid].t <= time { first = mid + 1 } else { end = mid }
        }
        var index = first - 1
        while index >= 0, lines[index].speech == true { index -= 1 }
        return index >= 0 ? index : nil
    }

    static func roles(_ lines: [Lyrics.Line], active: Int?) -> [Role] {
        let current = active ?? -1
        let next = lines.indices.first { $0 > current && lines[$0].speech != true }
        return lines.indices.map { index in
            if lines[index].speech == true { return .speech }
            if index == current { return .active }
            if index == next { return .next }
            return index < current ? .past : .ahead
        }
    }

    /// When a line stops being sung: its end tag, else the next line, capped
    /// at a typical sung length across long breaks (web timeLines).
    static func sungEnd(of index: Int, in lines: [Lyrics.Line], duration: Double) -> Double {
        let line = lines[index]
        let next = lines.indices.first { $0 > index }.map { lines[$0].t }
        let interval = (next ?? duration) - line.t
        if let end = line.end { return line.t + max(0.5, min(end - line.t, interval)) }
        if let last = line.words?.last, let d = last.d { return line.t + max(0.5, min(last.t + d - line.t, interval)) }
        return line.t + (interval > 10 ? 5 : max(0.5, interval))
    }

    static func cues(_ lines: [Lyrics.Line], duration: Double) -> [Cue] {
        var cues: [Cue] = []
        var previous: Int?
        for index in lines.indices where lines[index].speech != true {
            if let previous {
                let start = sungEnd(of: previous, in: lines, duration: duration)
                if lines[index].t - start >= instrumentalMin {
                    cues.append(Cue(kind: .instrumental, before: index, start: start, end: lines[index].t))
                }
            } else if lines[index].t >= 1 {
                cues.append(Cue(kind: .intro, before: index, start: 0, end: lines[index].t))
            }
            previous = index
        }
        return cues
    }

    /// Three dots light up cyan over the last 3 s of a cue.
    static func countdown(_ cue: Cue, at now: Double) -> (active: Bool, remaining: Double, lit: Int) {
        let active = now >= cue.start && now < cue.end
        let remaining = max(0, cue.end - now)
        let lit = active && remaining <= 3 ? min(3, max(0, 4 - Int(remaining.rounded(.up)))) : 0
        return (active, remaining, lit)
    }

    static func currentWord(in line: Lyrics.Line, at time: Double) -> Int? {
        guard let words = line.words, !words.isEmpty else { return nil }
        return words.lastIndex { $0.t <= time }
    }

    static func snapToLine(_ time: Double, lines: [Lyrics.Line]) -> Double {
        var best = time
        var distance = snap
        for line in lines where abs(line.t - time) < distance {
            distance = abs(line.t - time)
            best = line.t
        }
        return best
    }

    static func direction(of text: String) -> LayoutDirection {
        let firstLetter = text.unicodeScalars.first(where: { CharacterSet.letters.contains($0) })
        guard let code = firstLetter?.value else { return .leftToRight }
        return (0x0590...0x08FF).contains(code) || (0xFB1D...0xFEFC).contains(code)
            ? .rightToLeft : .leftToRight
    }

    /// The song's direction: right-to-left when most lines start in Hebrew/Arabic.
    static func songDirection(_ texts: [String]) -> LayoutDirection {
        let rtl = texts.filter { direction(of: $0) == .rightToLeft }.count
        return rtl * 2 > texts.count ? .rightToLeft : .leftToRight
    }

    /// Point sizes per role and size preset (spec table: active/next/rest).
    static func fontSize(_ role: Role, size: LyricsSize, performance: Bool) -> (size: CGFloat, weight: Font.Weight) {
        let table: (CGFloat, CGFloat, CGFloat)
        if performance {
            table = (34, 26, 22)
        } else {
            switch size {
            case .s: table = (24, 20, 18)
            case .m: table = (28, 22, 20)
            case .l: table = (34, 26, 24)
            }
        }
        switch role {
        case .active: return (table.0, .bold)
        case .next: return (table.1, .semibold)
        default: return (table.2, .semibold)
        }
    }

    static func color(_ role: Role) -> Color {
        switch role {
        case .active: .snPink
        case .next: .snText
        case .ahead: .snMuted
        case .past, .speech: .snPast
        }
    }
}
