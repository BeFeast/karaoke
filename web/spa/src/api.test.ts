// Unit tests for uploadJob (#173): the FormData body shape and the absence of
// an explicit Content-Type header (the browser must set the multipart
// boundary itself). Runs under `bun test src` (excluded from tsc).

import { afterEach, describe, expect, test } from "bun:test";
import { uploadJob } from "./api";

interface CapturedCall {
  path: string;
  init: RequestInit;
}

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

// Replace fetch with a recorder that answers 201 + a minimal JobOut-ish body.
function captureFetch(): CapturedCall[] {
  const calls: CapturedCall[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ path: String(input), init: init ?? {} });
    return new Response(JSON.stringify({ id: 1 }), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;
  return calls;
}

describe("uploadJob", () => {
  test("posts multipart FormData with the file and no explicit Content-Type", async () => {
    const calls = captureFetch();
    const file = new File(["RIFFdata"], "take one.wav", { type: "audio/wav" });
    await uploadJob(file);

    expect(calls).toHaveLength(1);
    const { path, init } = calls[0];
    expect(path).toBe("/jobs/upload");
    expect(init.method).toBe("POST");

    const body = init.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect((body.get("file") as File).name).toBe("take one.wav");
    expect(body.has("title")).toBe(false);

    const headers = init.headers as Record<string, string>;
    expect(Object.keys(headers).map((k) => k.toLowerCase())).not.toContain("content-type");
    expect(headers.Accept).toBe("application/json");
  });

  test("a provided title rides along trimmed", async () => {
    const calls = captureFetch();
    await uploadJob(new File(["x"], "a.mp3"), "  My Song  ");
    expect((calls[0].init.body as FormData).get("title")).toBe("My Song");
  });

  test("a whitespace-only title is omitted", async () => {
    const calls = captureFetch();
    await uploadJob(new File(["x"], "a.mp3"), "   ");
    expect((calls[0].init.body as FormData).has("title")).toBe(false);
  });
});
