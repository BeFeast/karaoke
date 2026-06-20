// Unit tests for the preflight-driven toolbar submit decision (issue #181):
// the pure classifier and the timeout-bounded GET /preflight fetch helper.
// Run with: bun test  (from extension/chrome)

import { describe, expect, test } from "bun:test";
import {
  PREFLIGHT_TIMEOUT_MS,
  classifySubmit,
  fetchPreflight,
} from "./preflight.js";

const BASE_HOST = "karaoke.oklabs.uk";
// Single-media: a confident single track — the only auto-submit path (#192).
const SUPPORTED = {
  supported: true,
  extractor: "youtube",
  generic_only: false,
  single_media: true,
};
// A dedicated extractor matched, but it returns a feed/playlist/channel/search
// container, not a single video (#192).
const CONTAINER = {
  supported: true,
  extractor: "youtube:tab",
  generic_only: false,
  single_media: false,
};
// An older booth that predates #192 omits the single_media key entirely.
const SUPPORTED_NO_SINGLE_MEDIA_KEY = {
  supported: true,
  extractor: "youtube",
  generic_only: false,
};
const GENERIC_ONLY = { supported: false, extractor: null, generic_only: true };
const UNSUPPORTED = { supported: false, extractor: null, generic_only: false };

describe("classifySubmit", () => {
  test("single-media extractor match → submit (one click, no friction)", () => {
    expect(classifySubmit("https://www.youtube.com/watch?v=dQw4w9WgXcQ", BASE_HOST, SUPPORTED)).toBe(
      "submit",
    );
    expect(classifySubmit("http://example.com/clip", BASE_HOST, SUPPORTED)).toBe("submit");
  });

  test("container extractor match (feed/playlist/channel) → confirm, never silent submit (#192)", () => {
    expect(classifySubmit("https://www.youtube.com/", BASE_HOST, CONTAINER)).toBe("confirm");
    expect(
      classifySubmit("https://www.youtube.com/feed/recommended", BASE_HOST, CONTAINER),
    ).toBe("confirm");
  });

  test("supported but no single_media key (older booth) → confirm, never silent submit (#192)", () => {
    expect(
      classifySubmit(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        BASE_HOST,
        SUPPORTED_NO_SINGLE_MEDIA_KEY,
      ),
    ).toBe("confirm");
  });

  test("generic-only match → confirm (no auto-submit)", () => {
    expect(classifySubmit("https://some.blog/post", BASE_HOST, GENERIC_ONLY)).toBe("confirm");
  });

  test("no verdict at all (preflight error/timeout) → confirm fallback, never a hard block", () => {
    expect(classifySubmit("https://some.blog/post", BASE_HOST, null)).toBe("confirm");
    expect(classifySubmit("https://some.blog/post", BASE_HOST, undefined)).toBe("confirm");
  });

  test("supported=false, generic_only=false → refuse (message only, no button)", () => {
    expect(classifySubmit("https://some.blog/post", BASE_HOST, UNSUPPORTED)).toBe("refuse");
  });

  test("non-http(s) schemes refuse locally, whatever the preflight says", () => {
    for (const url of ["chrome://extensions", "about:blank", "file:///tmp/song.mp3"]) {
      expect(classifySubmit(url, BASE_HOST, SUPPORTED)).toBe("refuse");
      expect(classifySubmit(url, BASE_HOST, null)).toBe("refuse");
    }
  });

  test("empty/garbage URLs refuse locally", () => {
    expect(classifySubmit("", BASE_HOST, SUPPORTED)).toBe("refuse");
    expect(classifySubmit("not a url", BASE_HOST, null)).toBe("refuse");
    expect(classifySubmit(undefined, BASE_HOST, SUPPORTED)).toBe("refuse");
  });

  test("the booth's own host refuses locally, whatever the preflight says", () => {
    expect(classifySubmit("https://karaoke.oklabs.uk/app/#/settings", BASE_HOST, SUPPORTED)).toBe(
      "refuse",
    );
    expect(classifySubmit("https://karaoke.oklabs.uk/jobs/79", BASE_HOST, null)).toBe("refuse");
  });

  test("LAN base with port: same host:port refused, other ports flow through", () => {
    const lanHost = "10.10.0.13:13140";
    expect(classifySubmit("http://10.10.0.13:13140/app/", lanHost, SUPPORTED)).toBe("refuse");
    expect(classifySubmit("http://10.10.0.13:8080/video", lanHost, SUPPORTED)).toBe("submit");
  });

  test("a missing base host disables only the self-submit check", () => {
    expect(classifySubmit("https://youtu.be/abc", "", SUPPORTED)).toBe("submit");
    expect(classifySubmit("chrome://extensions", "", SUPPORTED)).toBe("refuse");
  });
});

describe("fetchPreflight", () => {
  const BASE = "https://karaoke.oklabs.uk";

  function jsonResponse(body, { ok = true, status = 200 } = {}) {
    return { ok, status, json: async () => body };
  }

  test("parses a 200 verdict and normalizes the fields", async () => {
    const fetchImpl = async () =>
      jsonResponse({
        supported: true,
        extractor: "youtube",
        generic_only: false,
        single_media: true,
      });
    const result = await fetchPreflight(BASE, "https://youtu.be/abc", { fetchImpl });
    expect(result).toEqual({
      supported: true,
      extractor: "youtube",
      generic_only: false,
      single_media: true,
    });
  });

  test("builds the /preflight URL with the tab URL encoded and sends the auth headers", async () => {
    let seenUrl = null;
    let seenInit = null;
    const fetchImpl = async (url, init) => {
      seenUrl = url;
      seenInit = init;
      return jsonResponse({ supported: false, extractor: null, generic_only: true });
    };
    await fetchPreflight(BASE, "https://example.com/watch?v=1&t=2", {
      headers: { Authorization: "Bearer ktx_test" },
      fetchImpl,
    });
    expect(seenUrl).toBe(
      `${BASE}/preflight?url=${encodeURIComponent("https://example.com/watch?v=1&t=2")}`,
    );
    expect(seenInit.headers).toEqual({ Authorization: "Bearer ktx_test" });
    expect(seenInit.signal).toBeInstanceOf(AbortSignal);
  });

  test("missing extractor/flags normalize to null/false (older booth → single_media false)", async () => {
    const fetchImpl = async () => jsonResponse({ supported: false });
    const result = await fetchPreflight(BASE, "https://example.com/x", { fetchImpl });
    expect(result).toEqual({
      supported: false,
      extractor: null,
      generic_only: false,
      single_media: false,
    });
  });

  test("non-2xx answer → null (caller falls back to confirm)", async () => {
    const fetchImpl = async () => jsonResponse({ detail: "nope" }, { ok: false, status: 401 });
    expect(await fetchPreflight(BASE, "https://example.com/x", { fetchImpl })).toBeNull();
  });

  test("network error → null", async () => {
    const fetchImpl = async () => {
      throw new TypeError("Failed to fetch");
    };
    expect(await fetchPreflight(BASE, "https://example.com/x", { fetchImpl })).toBeNull();
  });

  test("unparseable body → null", async () => {
    const fetchImpl = async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
    });
    expect(await fetchPreflight(BASE, "https://example.com/x", { fetchImpl })).toBeNull();
  });

  test("hangs are cut by the AbortController timeout → null", async () => {
    let aborted = false;
    const hangingFetch = (_url, { signal }) =>
      new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => {
          aborted = true;
          reject(new DOMException("The operation was aborted.", "AbortError"));
        });
      });
    const result = await fetchPreflight(BASE, "https://example.com/x", {
      timeoutMs: 20,
      fetchImpl: hangingFetch,
    });
    expect(result).toBeNull();
    expect(aborted).toBe(true);
  });

  test("the default timeout is the locked 2 s", () => {
    expect(PREFLIGHT_TIMEOUT_MS).toBe(2000);
  });
});
