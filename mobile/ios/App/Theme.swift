import SwiftUI

// Sonereli brand v3 tokens (UI/UX v1 spec, "Design tokens"). Dark theme only.
extension Color {
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }

    static let snBlackout = Color(hex: 0x08090D)
    static let snSurface = Color(hex: 0x12151B)
    static let snRaised = Color(hex: 0x1B1F27)
    static let snDivider = Color(hex: 0x2A2E39)
    static let snTrack = Color(hex: 0x3A3F4C)
    static let snBoundary = Color(hex: 0x707988)
    static let snText = Color(hex: 0xF5F7FA)
    static let snMuted = Color(hex: 0xAAB0BC)
    static let snPast = Color(hex: 0x858B97)
    static let snPink = Color(hex: 0xFF2DAA)
    static let snCyan = Color(hex: 0x2DDFF5)
    static let snLime = Color(hex: 0xDFFF00)
    static let snViolet = Color(hex: 0x8057FF)
    static let snError = Color(hex: 0xFF808C)
    static let snSuccess = Color(hex: 0xACED61)
    static let snWarning = Color(hex: 0xFFC14B)
}

/// Kept for the sign-in cover and older call sites.
enum SonereliColor {
    static let background = Color.snBlackout
    static let pink = Color.snPink
}

enum SnFont {
    /// Inter at a fixed design size that still follows Dynamic Type.
    static func inter(_ size: CGFloat, _ weight: Font.Weight = .regular, relativeTo style: Font.TextStyle = .body) -> Font {
        let name: String
        switch weight {
        case .bold, .heavy, .black: name = "Inter-Bold"
        case .semibold: name = "Inter-SemiBold"
        case .medium: name = "Inter-Medium"
        default: name = "Inter-Regular"
        }
        return .custom(name, size: size, relativeTo: style)
    }

    /// Anton covers Latin only. Cyrillic display text falls back to Inter
    /// Bold, the same fallback the web uses.
    static func display(_ size: CGFloat, for text: String, relativeTo style: Font.TextStyle = .largeTitle) -> Font {
        let latinOnly = text.unicodeScalars.allSatisfy { $0.value < 0x0250 }
        return latinOnly ? .custom("Anton-Regular", size: size, relativeTo: style) : inter(size * 0.82, .bold, relativeTo: style)
    }

    static let title = inter(20, .bold, relativeTo: .title3)
    static let headline = inter(17, .semibold, relativeTo: .headline)
    static let body = inter(16, .regular, relativeTo: .body)
    static let label = inter(15, .semibold, relativeTo: .subheadline)
    static let caption = inter(13, .medium, relativeTo: .caption)
    static let eyebrow = inter(12, .semibold, relativeTo: .caption2)
}

/// Screen title in Anton, uppercase.
struct SnDisplay: View {
    let text: String
    var size: CGFloat = 34
    var mic = false

    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            if mic {
                Image(systemName: "music.mic")
                    .font(.system(size: size * 0.62, weight: .bold))
                    .foregroundStyle(Color.snPink)
                    .accessibilityHidden(true)
            }
            Text(text.uppercased(with: Locale.current))
                .font(SnFont.display(size, for: text))
                .foregroundStyle(Color.snText)
                .lineLimit(2)
                .minimumScaleFactor(0.7)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

/// One lime primary action per screen: blackout text on lime, 54 pt.
struct SnPrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    init() {}

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(SnFont.inter(17, .semibold, relativeTo: .headline))
            .foregroundStyle(Color.snBlackout)
            .frame(maxWidth: .infinity, minHeight: 54)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(isEnabled ? Color.snLime : Color.snTrack)
            )
            .opacity(configuration.isPressed ? 0.85 : 1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

/// Secondary action: outlined with the boundary colour.
struct SnSecondaryButtonStyle: ButtonStyle {
    var height: CGFloat
    @Environment(\.isEnabled) private var isEnabled

    init(height: CGFloat = 54) {
        self.height = height
    }

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(SnFont.inter(16, .semibold, relativeTo: .headline))
            .foregroundStyle(isEnabled ? Color.snText : Color.snPast)
            .frame(maxWidth: .infinity, minHeight: height)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(Color.snBoundary, lineWidth: 1)
            )
            .contentShape(Rectangle())
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

extension ButtonStyle where Self == SnPrimaryButtonStyle {
    static var snPrimary: SnPrimaryButtonStyle { SnPrimaryButtonStyle() }
}

extension ButtonStyle where Self == SnSecondaryButtonStyle {
    static var snSecondary: SnSecondaryButtonStyle { SnSecondaryButtonStyle() }
}

/// A status note: icon + text on surface, tinted by tone.
struct SnNote: View {
    enum Tone { case error, warning, success, info }
    let text: String
    var tone: Tone = .error

    private var color: Color {
        switch tone {
        case .error: .snError
        case .warning: .snWarning
        case .success: .snSuccess
        case .info: .snMuted
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: tone == .success ? "checkmark.circle" : tone == .info ? "bell" : "exclamationmark.circle")
                .foregroundStyle(color)
                .accessibilityHidden(true)
            Text(text)
                .font(SnFont.inter(14, .medium, relativeTo: .subheadline))
                .foregroundStyle(tone == .info ? Color.snMuted : color)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.snSurface))
    }
}

/// Song cover: the YouTube thumbnail when there is one, else a brand
/// gradient with the title's first letter (as in the mockup).
struct SongCover: View {
    let title: String
    let url: URL?
    var size: CGFloat = 48
    var radius: CGFloat = 6

    var body: some View {
        ZStack {
            placeholder
            if let url {
                AsyncImage(url: url) { phase in
                    if let image = phase.image {
                        image.resizable().scaledToFill()
                    }
                }
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
        .accessibilityHidden(true)
    }

    private var placeholder: some View {
        let seed = title.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) & 0xFFFF }
        let accents: [Color] = [.snPink, .snCyan, .snViolet, .snLime]
        let accent = accents[seed % accents.count]
        let initial = title.trimmingCharacters(in: .whitespacesAndNewlines).first.map { String($0).uppercased() } ?? "♪"
        return ZStack {
            LinearGradient(colors: [accent.opacity(0.9), Color.snBlackout, Color.snViolet.opacity(0.8)],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            Text(initial)
                .font(SnFont.display(size * 0.55, for: initial))
                .foregroundStyle(Color.snText)
        }
    }
}

extension View {
    /// Screen background: blackout behind everything, dark scheme.
    func snScreen() -> some View {
        background(Color.snBlackout.ignoresSafeArea())
    }
}

enum Clock {
    static func label(_ interval: Double) -> String {
        guard interval.isFinite else { return "0:00" }
        let seconds = Int(max(0, interval))
        return String(format: "%d:%02d", seconds / 60, seconds % 60)
    }
}
