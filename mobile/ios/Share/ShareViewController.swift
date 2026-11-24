import SwiftUI
import UniformTypeIdentifiers

final class ShareViewController: UIViewController {
    private var loading: Task<Void, Never>?

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        loading = Task { [weak self] in
            guard let self else { return }
            let providers = (extensionContext?.inputItems as? [NSExtensionItem] ?? []).flatMap {
                $0.attachments ?? []
            }
            do {
                guard let url = await Self.youtubeURL(from: providers) else {
                    presentMessage("Выберите видео YouTube", detail: "Sonereli принимает ссылки на видео YouTube.")
                    return
                }
                try ShareInbox.push(SharedVideo(url: url))
                presentMessage("Ссылка сохранена", detail: "Откройте Sonereli, чтобы подтвердить подготовку песни.")
            } catch {
                presentMessage("Ссылка не сохранилась", detail: "Попробуйте поделиться ещё раз.")
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

    private func presentMessage(_ title: String, detail: String) {
        let child = UIHostingController(rootView: VStack(spacing: 16) {
            Text(title).font(.title2.bold())
            Text(detail).multilineTextAlignment(.center).foregroundStyle(.secondary)
            Button("Готово") { [weak self] in
                self?.extensionContext?.completeRequest(returningItems: nil, completionHandler: nil)
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity))
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
