import SwiftUI
import UniformTypeIdentifiers

final class ShareViewController: UIViewController {
    private var loading: Task<Void, Never>?

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 8 / 255, green: 9 / 255, blue: 13 / 255, alpha: 1)
        overrideUserInterfaceStyle = .dark
        loading = Task { [weak self] in
            guard let self else { return }
            let providers = (extensionContext?.inputItems as? [NSExtensionItem] ?? []).flatMap {
                $0.attachments ?? []
            }
            do {
                guard let url = await Self.youtubeURL(from: providers) else {
                    presentMessage(Self.text("Choose a YouTube video", "Выбери видео YouTube"),
                                   detail: Self.text("Sonereli takes links to YouTube videos.", "Sonereli принимает ссылки на видео YouTube."),
                                   ok: false)
                    return
                }
                try ShareInbox.push(SharedVideo(url: url))
                presentMessage(Self.text("Sent to Sonereli", "Отправлено в Sonereli"),
                               detail: Self.text("Open Sonereli and tap Prepare song.", "Открой Sonereli и нажми «Подготовить песню»."),
                               ok: true)
            } catch {
                presentMessage(Self.text("The link wasn’t saved", "Ссылка не сохранилась"),
                               detail: Self.text("Try sharing again.", "Попробуй поделиться ещё раз."), ok: false)
            }
        }
    }

    private static func youtubeURL(from providers: [NSItemProvider]) async -> URL? {
        for type in [UTType.url, .plainText, .text] {
            for provider in providers where provider.hasItemConformingToTypeIdentifier(type.identifier) {
                let text: String? = await withCheckedContinuation { continuation in
                    provider.loadItem(forTypeIdentifier: type.identifier, options: nil) { item, _ in
                        let value = (item as? URL)?.absoluteString ?? (item as? String)
                        continuation.resume(returning: value)
                    }
                }
                if let text, let url = SharedVideo.validatedURL(from: text) { return url }
            }
        }
        return nil
    }

    private static let russian = (Locale.preferredLanguages.first ?? "en").lowercased().hasPrefix("ru")
    private static func text(_ english: String, _ russian: String) -> String { Self.russian ? russian : english }

    private func presentMessage(_ title: String, detail: String, ok: Bool) {
        let blackout = Color(red: 8 / 255, green: 9 / 255, blue: 13 / 255)
        let lime = Color(red: 223 / 255, green: 1, blue: 0)
        let child = UIHostingController(rootView: VStack(spacing: 16) {
            Image(systemName: ok ? "checkmark.circle.fill" : "exclamationmark.circle")
                .font(.system(size: 44))
                .foregroundStyle(ok ? Color(red: 172 / 255, green: 237 / 255, blue: 97 / 255) : Color(red: 1, green: 128 / 255, blue: 140 / 255))
            Text(title).font(.title2.bold()).foregroundStyle(.white)
            Text(detail).multilineTextAlignment(.center).foregroundStyle(Color(red: 170 / 255, green: 176 / 255, blue: 188 / 255))
            Button { [weak self] in
                self?.extensionContext?.completeRequest(returningItems: nil, completionHandler: nil)
            } label: {
                Text(Self.text("Done", "Готово"))
                    .font(.headline)
                    .foregroundStyle(blackout)
                    .frame(maxWidth: .infinity, minHeight: 54)
                    .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(lime))
            }
            .padding(.top, 8)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(blackout)
        .preferredColorScheme(.dark))
        addChild(child)
        view.addSubview(child.view)
        child.view.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            child.view.topAnchor.constraint(equalTo: view.topAnchor),
            child.view.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            child.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            child.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])
        child.didMove(toParent: self)
    }
}
