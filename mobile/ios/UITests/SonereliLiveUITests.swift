import XCTest

final class SonereliLiveUITests: XCTestCase {
    @MainActor
    func testClerkOwnerSubmitsARealJobAndPlaysBothStems() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-test-live", "-AppleLanguages", "(ru)", "-AppleLocale", "ru_RU"]
        let environment = ProcessInfo.processInfo.environment
        for key in ["SONERELI_QA_SUBJECT", "SONERELI_QA_TICKET", "SONERELI_QA_YOUTUBE_URL"] {
            let value = try XCTUnwrap(environment[key], "Missing live verification input \(key)")
            app.launchEnvironment[key] = value
        }
        app.launch()
        let signIn = app.buttons["auth.liveSignIn"]
        if signIn.waitForExistence(timeout: 10) { signIn.tap() }

        let confirm = app.buttons["add.submit"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 30), "Clerk owner and share inbox did not load.")
        confirm.tap()
        XCTAssertTrue(app.descendants(matching: .any)["job.progress"].waitForExistence(timeout: 15))

        let sing = app.buttons["job.sing.websocket"]
        XCTAssertTrue(sing.waitForExistence(timeout: 900), "The real job did not complete over WebSocket.")
        capture(app, name: "ios-live-completed")
        sing.tap()

        let play = app.buttons["stage.playPause"]
        XCTAssertTrue(play.waitForExistence(timeout: 30))
        let loaded = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in play.isEnabled }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [loaded], timeout: 90), .completed, "Both stems and lyrics did not load.")
        let vocals = app.sliders["stage.vocals"]
        vocals.adjust(toNormalizedSliderPosition: 0.5)
        play.tap()
        let elapsed = app.staticTexts["stage.elapsed"]
        let advanced = XCTNSPredicateExpectation(
            predicate: NSPredicate { _, _ in elapsed.exists && elapsed.label != "0:00" }, object: nil
        )
        XCTAssertEqual(XCTWaiter.wait(for: [advanced], timeout: 10), .completed)
        capture(app, name: "ios-live-stage")
    }

    @MainActor
    private func capture(_ app: XCUIApplication, name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
