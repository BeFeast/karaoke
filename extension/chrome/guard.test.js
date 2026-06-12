// Unit tests for the submit policy (issue #177): the context-menu registry
// invariant (one registration per distinct action) and the URL guard that
// fronts every submit path. Run with: bun test  (from extension/chrome)

import { describe, expect, test } from "bun:test";
import { MENU_SPEC, OPEN_BOOTH_MENU_ID, submitRefusal } from "./guard.js";

const BASE = "https://karaoke.oklabs.uk";

describe("MENU_SPEC", () => {
  test("has at least one action and every entry is fully specified", () => {
    expect(MENU_SPEC.length).toBeGreaterThan(0);
    for (const item of MENU_SPEC) {
      expect(typeof item.id).toBe("string");
      expect(item.id.length).toBeGreaterThan(0);
      expect(typeof item.title).toBe("string");
      expect(item.title.length).toBeGreaterThan(0);
      expect(Array.isArray(item.contexts)).toBe(true);
      expect(item.contexts.length).toBeGreaterThan(0);
    }
  });

  test("one registration per distinct action — unique ids and titles", () => {
    const ids = MENU_SPEC.map((item) => item.id);
    const titles = MENU_SPEC.map((item) => item.title);
    expect(new Set(ids).size).toBe(ids.length);
    expect(new Set(titles).size).toBe(titles.length);
  });

  test("no two entries share a context, so one right-click target never shows two entries", () => {
    const contexts = MENU_SPEC.flatMap((item) => item.contexts);
    expect(new Set(contexts).size).toBe(contexts.length);
  });

  test("a single merged entry covers both page and link targets", () => {
    const covering = MENU_SPEC.filter(
      (item) => item.contexts.includes("page") || item.contexts.includes("link"),
    );
    expect(covering).toHaveLength(1);
    expect(covering[0].contexts).toContain("page");
    expect(covering[0].contexts).toContain("link");
  });

  test("exactly one toolbar-icon entry — Open Karaoke booth on contexts:[action] (#181)", () => {
    const actions = MENU_SPEC.filter((item) => item.contexts.includes("action"));
    expect(actions).toHaveLength(1);
    expect(actions[0].id).toBe(OPEN_BOOTH_MENU_ID);
    expect(actions[0].contexts).toEqual(["action"]);
    expect(actions[0].title).toBe("Open Karaoke booth");
  });
});

describe("submitRefusal", () => {
  test("youtube watch URL is allowed", () => {
    expect(submitRefusal("https://www.youtube.com/watch?v=dQw4w9WgXcQ", BASE)).toBeNull();
  });

  test("short links and plain http are allowed", () => {
    expect(submitRefusal("https://youtu.be/dQw4w9WgXcQ", BASE)).toBeNull();
    expect(submitRefusal("http://example.com/video", BASE)).toBeNull();
  });

  test("the configured booth itself is refused (self-submit, doomed job #79)", () => {
    const refusal = submitRefusal("https://karaoke.oklabs.uk/app/#/settings", BASE);
    expect(refusal?.reason).toBe("own-booth");
    expect(refusal.message).toContain("karaoke booth");
  });

  test("self-submit is refused regardless of scheme and path", () => {
    expect(submitRefusal("http://karaoke.oklabs.uk/", BASE)?.reason).toBe("own-booth");
    expect(submitRefusal("https://karaoke.oklabs.uk/jobs/79", BASE)?.reason).toBe("own-booth");
  });

  test("LAN base with port: same host:port refused, other ports allowed", () => {
    const lan = "http://10.10.0.13:13140";
    expect(submitRefusal("http://10.10.0.13:13140/app/", lan)?.reason).toBe("own-booth");
    expect(submitRefusal("http://10.10.0.13:8080/video", lan)).toBeNull();
  });

  test("other hosts — including lookalike subdomains — are allowed", () => {
    expect(submitRefusal("https://www.karaoke.oklabs.uk/x", BASE)).toBeNull();
    expect(submitRefusal("https://other.oklabs.uk/x", BASE)).toBeNull();
  });

  test("chrome:// and other non-http(s) schemes are refused", () => {
    for (const url of [
      "chrome://extensions",
      "chrome-extension://abcdef/popup.html",
      "about:blank",
      "file:///tmp/song.mp3",
      "ftp://example.com/file",
    ]) {
      expect(submitRefusal(url, BASE)?.reason).toBe("not-http");
    }
  });

  test("empty, garbage, and missing URLs are refused", () => {
    expect(submitRefusal("", BASE)?.reason).toBe("not-http");
    expect(submitRefusal("not a url", BASE)?.reason).toBe("not-http");
    expect(submitRefusal(undefined, BASE)?.reason).toBe("not-http");
  });

  test("a missing/garbled base URL disables only the self-submit check", () => {
    expect(submitRefusal("https://youtu.be/abc", "")).toBeNull();
    expect(submitRefusal("https://youtu.be/abc", "not a url")).toBeNull();
    expect(submitRefusal("chrome://extensions", "")?.reason).toBe("not-http");
  });

  test("every refusal carries a human message", () => {
    for (const refusal of [
      submitRefusal("chrome://extensions", BASE),
      submitRefusal("https://karaoke.oklabs.uk/app/", BASE),
    ]) {
      expect(typeof refusal.message).toBe("string");
      expect(refusal.message.length).toBeGreaterThan(10);
    }
  });
});
