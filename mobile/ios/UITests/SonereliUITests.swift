import XCTest

final class SonereliUITests: XCTestCase {
    @MainActor
    func testStagePlaybackAndHebrewLyrics() {
        let app = signedIn()
        let job = app.descendants(matching: .any).matching(identifier: "songs.job.1").firstMatch
        XCTAssertTrue(job.waitForExistence(timeout: 10))
        job.tap()
        openStage(in: app)

        let play = app.buttons["stage.playPause"]
        let vocals = app.sliders["stage.vocals"]
        let backing = app.sliders["stage.backing"]
        XCTAssertTrue(vocals.isHittable)
        XCTAssertTrue(backing.isHittable)
        let beforeVocals = vocals.value as? String
        let beforeBacking = backing.value as? String
        play.tap()
        vocals.adjust(toNormalizedSliderPosition: 0.8)
        backing.adjust(toNormalizedSliderPosition: 0.6)
        XCTAssertNotEqual(vocals.value as? String, beforeVocals)
        XCTAssertNotEqual(backing.value as? String, beforeBacking)

        let lyric = app.staticTexts["stage.lyric.1"]
        let selected = XCTNSPredicateExpectation(
            predicate: NSPredicate { _, _ in lyric.exists && lyric.isSelected }, object: nil
        )
        XCTAssertEqual(XCTWaiter.wait(for: [selected], timeout: 6), .completed)
        XCTAssertTrue(lyric.isHittable)
        XCTAssertTrue(lyric.label.contains("12:30"))
        XCTAssertGreaterThan(lyric.frame.height, 40, "The long lyric should wrap at the active font size.")
        XCTAssertLessThanOrEqual(lyric.frame.width, app.frame.width)
        XCTAssertNotEqual(app.staticTexts["stage.elapsed"].label, "0:00")
        capture(app, name: "ios-stage-hebrew")

        play.tap()
        let paused = app.staticTexts["stage.elapsed"].label
        XCTAssertEqual(play.label, "Играть")
        app.sliders["stage.seek"].adjust(toNormalizedSliderPosition: 0.75)
        let sought = app.staticTexts["stage.elapsed"].label
        XCTAssertNotEqual(sought, paused)
        XCTAssertTrue(["0:14", "0:15", "0:16", "0:17"].contains(sought),
                      "Expected a seek to approximately 15 seconds, observed \(sought).")
        let afterSeek = app.staticTexts["stage.lyric.4"]
        let seekSelected = XCTNSPredicateExpectation(
            predicate: NSPredicate { _, _ in afterSeek.exists && afterSeek.isSelected }, object: nil
        )
        let result = XCTWaiter.wait(for: [seekSelected], timeout: 4)
        capture(app, name: "ios-stage-after-seek")
        XCTAssertEqual(result, .completed,
                       "Expected the Hebrew line at 14 seconds, observed time \(sought). \(app.debugDescription)")
    }

    @MainActor
    func testShareConfirmationProgressAndSing() {
        let app = signedIn(share: true)
        let confirm = app.buttons["confirm.submit"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 10))
        capture(app, name: "ios-share-confirm")
        confirm.tap()
        XCTAssertTrue(app.descendants(matching: .any)["job.progress"].waitForExistence(timeout: 3))
        capture(app, name: "ios-share-progress")
        openStage(in: app)
        XCTAssertTrue(app.sliders["stage.backing"].isHittable)
        capture(app, name: "ios-share-stage")
    }

    @MainActor
    private func signedIn(share: Bool = false) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-test-fixture", "-AppleLanguages", "(ru)", "-AppleLocale", "ru_RU"]
        if share { app.launchArguments.append("--ui-test-share") }
        app.launch()
        XCTAssertTrue(app.buttons["auth.fixtureSignIn"].waitForExistence(timeout: 10))
        app.buttons["auth.fixtureSignIn"].tap()
        return app
    }

    @MainActor
    private func openStage(in app: XCUIApplication) {
        let sing = app.buttons["job.sing"]
        XCTAssertTrue(sing.waitForExistence(timeout: 15))
        sing.tap()
        XCTAssertTrue(app.buttons["stage.playPause"].waitForExistence(timeout: 15))
    }

    @MainActor
    private func capture(_ app: XCUIApplication, name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
