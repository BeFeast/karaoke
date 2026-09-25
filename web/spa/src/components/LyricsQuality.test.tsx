import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import type { LyricsPayload, LyricsQuality } from "../api";
import { canSaveReview, LyricsQualityBanner, LyricsReview, LyricsReviewEditor, qualityMessage, reviewReducer, type ReviewDraft } from "./LyricsQuality";

const quality = (status: LyricsQuality["status"]): LyricsQuality => ({ schema_version: 1, status, issues: [] });
const lyrics: LyricsPayload = { synced: true, source: "forced_aligned", lrc: "[00:01.00]First line", lines: [{ t: 1, text: "First line" }], plain: "First line", revision: "revision-one", review_job_id: 23, quality: quality("needs_review") };
const props = { lyrics, onSaved: () => {}, onSeek: () => {}, onReload: async () => {} };

describe("lyrics quality status", () => {
  test("flags needs review, including in compact performance view", () => {
    for (const compact of [true, false]) {
      const html = renderToStaticMarkup(<LyricsQualityBanner quality={quality("needs_review")} compact={compact} />);
      expect(html).toContain("Lyrics need review");
      expect(html).toContain('role="status"');
    }
  });
  test("automatic checks never imply a listening review", () => {
    expect(qualityMessage(quality("checked")).title).toBe("Lyrics automatically checked");
    expect(qualityMessage(quality("checked")).detail).toContain("not a listening review");
    expect(qualityMessage(quality("reviewed")).title).toBe("Lyrics reviewed");
    expect(qualityMessage(quality("reviewed")).detail).toContain("owner confirmed");
  });
  test("legacy/null reports are explicitly unchecked", () => {
    for (const missing of [null, undefined]) {
      const html = renderToStaticMarkup(<LyricsQualityBanner quality={missing} />);
      expect(html).toContain("Lyrics not checked");
      expect(qualityMessage(missing).warning).toBe(true);
    }
  });
  test("public shares have no correction control", () => {
    const html = renderToStaticMarkup(<LyricsReview {...props} lyrics={{ ...lyrics, review_job_id: null }} />);
    expect(html).toBe("");
    expect(renderToStaticMarkup(<LyricsReview {...props} />)).toContain("Review / correct lyrics");
  });
  test("owner starts with an unchecked confirmation and disabled save", () => {
    const html = renderToStaticMarkup(<LyricsReviewEditor {...props} />);
    expect(html).toContain("First line");
    expect(html).toContain("I listened to the entire song");
    expect(html).toContain('disabled="">Save reviewed lyrics');
    expect(html).not.toContain('checked=""');
  });
  test("issue text and a listening point are available for review", () => {
    const html = renderToStaticMarkup(<LyricsReviewEditor {...props} lyrics={{ ...lyrics, quality: { ...quality("needs_review"), issues: [{ code: "missing_line", text: "Missing verse", start: 83.25 }] } }} />);
    expect(html).toContain("Missing verse");
    expect(html).toContain("Listen near 1:23");
  });
});

describe("review draft lifecycle", () => {
  const initial: ReviewDraft = { lrc: "[00:01.00]Words", confirmed: false, saving: false, error: null };
  test("requires explicit review, a revision, and nonempty lyrics before save", () => {
    expect(canSaveReview(initial, "r1")).toBe(false);
    const confirmed = reviewReducer(initial, { type: "confirm", confirmed: true });
    expect(canSaveReview(confirmed, "r1")).toBe(true);
    expect(canSaveReview(confirmed)).toBe(false);
    expect(canSaveReview({ ...confirmed, lrc: " " }, "r1")).toBe(false);
    expect(canSaveReview(reviewReducer(confirmed, { type: "saving" }), "r1")).toBe(false);
  });
  test("editing invalidates confirmation of the previous text", () => {
    const confirmed = reviewReducer(initial, { type: "confirm", confirmed: true });
    const edited = reviewReducer(confirmed, { type: "edit", lrc: "[00:02.00]Correct words" });
    expect(edited.lrc).toContain("Correct words");
    expect(edited.confirmed).toBe(false);
  });
  test("conflicts and validation errors preserve the draft and require review again", () => {
    for (const error of ["409 Conflict: Lyrics changed", "422 Invalid LRC"]) {
      const failed = reviewReducer({ ...initial, confirmed: true, saving: true }, { type: "error", error });
      expect(failed.lrc).toBe(initial.lrc);
      expect(failed.error).toBe(error);
      expect(failed.saving).toBe(false);
      expect(failed.confirmed).toBe(false);
    }
  });
  test("saving an unchanged revision still leaves the editor usable", () => {
    const saved = reviewReducer({ ...initial, confirmed: true, saving: true }, { type: "saved" });
    expect(saved.saving).toBe(false);
    expect(saved.confirmed).toBe(false);
  });
});
