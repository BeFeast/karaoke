import XCTest

final class SonereliUITests: XCTestCase {
    /// Stage with mixed Hebrew lyrics: play, Original vocals, active line,
    /// scrub, background playback, then the mini-player across tabs.
    @MainActor
    func testStagePlaybackAndHebrewLyrics() throws {
        let app = signedIn()
        let job = element(app, "songs.job.1")
        XCTAssertTrue(job.waitForExistence(timeout: 10))
        job.tap()
        let play = waitForStage(app)

        let vocals = app.sliders["stage.vocals"]
        XCTAssertTrue(vocals.isHittable)
        let beforeVocals = vocals.value as? String
        play.tap()
        vocals.adjust(toNormalizedSliderPosition: 0.8)
        XCTAssertNotEqual(vocals.value as? String, beforeVocals)

        let lyric = element(app, "stage.lyric.1")
        XCTAssertTrue(wait(timeout: 6) { lyric.exists && lyric.isSelected })
        XCTAssertTrue(lyric.isHittable)
        XCTAssertTrue(lyric.label.contains("12:30"))
        XCTAssertGreaterThan(lyric.frame.height, 40, "The long lyric should wrap at the active font size.")
        XCTAssertLessThanOrEqual(lyric.frame.width, app.frame.width)
        let elapsed = app.staticTexts["stage.elapsed"]
        XCTAssertNotEqual(elapsed.label, "0:00")
        Thread.sleep(forTimeInterval: 0.6)
        capture(app, name: "ios-stage-hebrew")

        play.tap()
        XCTAssertEqual(play.label, "Играть")
        let paused = elapsed.label
        tapSeek(app, at: 0.75)
        let sought = elapsed.label
        XCTAssertNotEqual(sought, paused)
        XCTAssertTrue(["0:14", "0:15", "0:16"].contains(sought), "Expected a seek to about 15 s, observed \(sought).")
        let afterSeek = element(app, "stage.lyric.4")
        let selected = wait(timeout: 4) { afterSeek.exists && afterSeek.isSelected }
        capture(app, name: "ios-stage-after-seek")
        XCTAssertTrue(selected, "Expected the Hebrew line at 14 s, observed \(sought).")

        tapSeek(app, at: 0.2)
        play.tap()
        XCTAssertEqual(play.label, "Пауза")
        let beforeBackground = try XCTUnwrap(Self.seconds(from: elapsed.label))
        XCUIDevice.shared.press(.home)
        Thread.sleep(forTimeInterval: 4)
        app.activate()
        XCTAssertEqual(play.label, "Пауза")
        XCTAssertTrue(wait(timeout: 1.5) { (Self.seconds(from: elapsed.label) ?? 0) >= beforeBackground + 3 },
                      "Elapsed after foregrounding was \(elapsed.label).")

        // Minimize: the song keeps playing in the mini-player on every tab.
        app.buttons["stage.minimize"].tap()
        let mini = element(app, "mini.player")
        XCTAssertTrue(mini.waitForExistence(timeout: 5))
        app.tabBars.buttons["Каталог"].tap()
        XCTAssertTrue(mini.waitForExistence(timeout: 5))
        XCTAssertEqual(app.buttons["mini.playPause"].label, "Пауза")
        capture(app, name: "ios-mini-player-catalog")
        mini.tap()
        XCTAssertTrue(app.buttons["stage.playPause"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.buttons["stage.playPause"].label, "Пауза")
    }

    /// YouTube Share → Add song (prefilled) → Processing over WS → Sing.
    @MainActor
    func testShareConfirmationProgressAndSing() {
        let app = signedIn(share: true)
        let confirm = app.buttons["add.submit"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 10))
        XCTAssertTrue(confirm.isEnabled)
        capture(app, name: "ios-share-confirm")
        confirm.tap()
        XCTAssertTrue(element(app, "job.progress").waitForExistence(timeout: 3))
        capture(app, name: "ios-share-progress")
        let sing = app.buttons["job.sing"]
        XCTAssertTrue(sing.waitForExistence(timeout: 15))
        capture(app, name: "ios-share-ready")
        sing.tap()
        waitForStage(app)
        XCTAssertTrue(app.sliders["stage.vocals"].isHittable)
        capture(app, name: "ios-share-stage")
    }

    /// Every main screen in English, for the delivery receipt.
    @MainActor
    func testMainScreensGallery() {
        let app = signedIn(language: "en", extra: ["--ui-test-preparing", "--ui-test-continue"])
        XCTAssertTrue(element(app, "home.continue").waitForExistence(timeout: 10))
        XCTAssertTrue(element(app, "songs.job.5").waitForExistence(timeout: 5))
        capture(app, name: "01-my-songs")

        app.buttons["songs.add"].tap()
        let field = app.textFields["paste.url"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        capture(app, name: "02-add-song")
        field.tap()
        field.typeText("https://youtu.be/ccccccccc01")
        XCTAssertTrue(element(app, "add.alreadyReady").waitForExistence(timeout: 5))
        capture(app, name: "03-add-already-ready")
        app.buttons["add.close"].tap()

        element(app, "songs.job.5").tap()
        XCTAssertTrue(element(app, "job.progress").waitForExistence(timeout: 5))
        capture(app, name: "04-processing")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        app.tabBars.buttons["Catalog"].tap()
        XCTAssertTrue(element(app, "catalog.song.1").waitForExistence(timeout: 10))
        capture(app, name: "05-catalog")
        element(app, "catalog.song.2").tap()
        XCTAssertTrue(wait(timeout: 5) { Self.element(app, "catalog.song.2").label.contains("Preparing") || Self.element(app, "catalog.song.2").label.contains("Готовится") })
        capture(app, name: "06-catalog-prepare-one-tap")
        let search = app.textFields["catalog.search"]
        search.tap()
        search.typeText("glow\n")
        XCTAssertTrue(element(app, "catalog.song.4").waitForExistence(timeout: 5))
        capture(app, name: "07-catalog-search")

        app.tabBars.buttons["Settings"].tap()
        XCTAssertTrue(app.buttons["settings.feedback"].waitForExistence(timeout: 5))
        capture(app, name: "08-settings")
        app.buttons["settings.feedback"].tap()
        XCTAssertTrue(app.buttons["feedback.send"].waitForExistence(timeout: 5))
        capture(app, name: "09-feedback")

        app.tabBars.buttons["My songs"].tap()
        element(app, "songs.job.3").tap()
        let play = waitForStage(app)
        tapSeek(app, at: 13.3 / 40)
        play.tap()
        XCTAssertTrue(wait(timeout: 4) { Self.element(app, "stage.lyric.3").isSelected })
        Thread.sleep(forTimeInterval: 0.8)
        capture(app, name: "10-stage-english")
        tapSeek(app, at: 24.5 / 40)
        XCTAssertTrue(element(app, "stage.cue.instrumental").waitForExistence(timeout: 4))
        Thread.sleep(forTimeInterval: 0.5)
        capture(app, name: "11-stage-instrumental-cue")
        app.buttons["stage.more"].tap()
        Thread.sleep(forTimeInterval: 0.8)
        capture(app, name: "12-stage-options")
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.1)).tap()

        app.buttons["stage.minimize"].tap()
        XCTAssertTrue(element(app, "mini.player").waitForExistence(timeout: 5))
        capture(app, name: "13-mini-player")

        element(app, "songs.job.1").tap()
        let hebrewPlay = waitForStage(app)
        hebrewPlay.tap()
        XCTAssertTrue(wait(timeout: 6) { Self.element(app, "stage.lyric.1").isSelected })
        Thread.sleep(forTimeInterval: 0.6)
        capture(app, name: "14-stage-hebrew-rtl")
    }

    /// The Russian interface: My songs and the Processing language.
    @MainActor
    func testRussianInterface() {
        let app = signedIn(extra: ["--ui-test-preparing", "--ui-test-continue"])
        XCTAssertTrue(element(app, "songs.job.5").waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Можно петь"].exists || app.staticTexts["МОИ ПЕСНИ"].exists)
        capture(app, name: "15-my-songs-ru")
        app.tabBars.buttons["Настройки"].tap()
        XCTAssertTrue(app.buttons["settings.feedback"].waitForExistence(timeout: 5))
        capture(app, name: "16-settings-ru")
    }

    // MARK: - Helpers

    private static func seconds(from clock: String) -> Int? {
        let parts = clock.split(separator: ":").compactMap { Int($0) }
        guard parts.count == 2 else { return nil }
        return parts[0] * 60 + parts[1]
    }

    @MainActor
    private func signedIn(share: Bool = false, language: String = "ru", extra: [String] = []) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        let locale = language == "ru" ? "ru_RU" : "en_US"
        app.launchArguments = ["--ui-test-fixture", "-AppleLanguages", "(\(language))", "-AppleLocale", locale] + extra
        if share { app.launchArguments.append("--ui-test-share") }
        app.launch()
        XCTAssertTrue(app.buttons["auth.fixtureSignIn"].waitForExistence(timeout: 10))
        app.buttons["auth.fixtureSignIn"].tap()
        return app
    }

    @MainActor
    @discardableResult
    private func waitForStage(_ app: XCUIApplication) -> XCUIElement {
        let play = app.buttons["stage.playPause"]
        XCTAssertTrue(play.waitForExistence(timeout: 15))
        XCTAssertTrue(wait(timeout: 15) { play.isEnabled }, "Both stems and lyrics did not load.")
        return play
    }

    @MainActor
    private func element(_ app: XCUIApplication, _ identifier: String) -> XCUIElement {
        Self.element(app, identifier)
    }

    @MainActor
    private static func element(_ app: XCUIApplication, _ identifier: String) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: identifier).firstMatch
    }

    @MainActor
    private func tapSeek(_ app: XCUIApplication, at fraction: Double) {
        let seek = element(app, "stage.seek")
        XCTAssertTrue(seek.waitForExistence(timeout: 5))
        seek.coordinate(withNormalizedOffset: CGVector(dx: fraction, dy: 0.5)).tap()
        Thread.sleep(forTimeInterval: 0.4)
    }

    @MainActor
    private func wait(timeout: TimeInterval, _ condition: @escaping () -> Bool) -> Bool {
        let expectation = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in condition() }, object: nil)
        return XCTWaiter.wait(for: [expectation], timeout: timeout) == .completed
    }

    @MainActor
    private func capture(_ app: XCUIApplication, name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
