import { expect, test } from "bun:test";
import { itemHash, parseHash, publicShareUrl } from "./router";

test("copying a Stage share uses the public host while the in-app link stays on the current origin", () => {
  const lanPage = "http://booth.test:13140/app/";
  const token = "unlisted+token";
  expect(new URL(itemHash(token), lanPage).href).toBe(
    "http://booth.test:13140/app/#/job/unlisted%2Btoken",
  );
  expect(new URL(publicShareUrl("https://sonereli.oklabs.uk/", token), lanPage).href).toBe(
    "https://sonereli.oklabs.uk/share/unlisted%2Btoken",
  );
});

test("copying a Stage share honors either configured public hostname", () => {
  expect(publicShareUrl("https://sonereli.oklabs.uk/", "unlisted_token")).toBe(
    "https://sonereli.oklabs.uk/share/unlisted_token",
  );
  expect(publicShareUrl("https://karaoke.oklabs.uk", "unlisted_token")).toBe(
    "https://karaoke.oklabs.uk/share/unlisted_token",
  );
});

test("Add song is its own route and carries a prefilled link", () => {
  expect(parseHash("#/add")).toEqual({ name: "add", url: "" });
  expect(parseHash("#/add?url=https%3A%2F%2Fyoutu.be%2FdQw4w9WgXcQ")).toEqual({
    name: "add", url: "https://youtu.be/dQw4w9WgXcQ",
  });
  expect(parseHash("#/")).toEqual({ name: "dashboard" });
  expect(parseHash("#/catalog")).toEqual({ name: "catalog" });
});

test("a resumed song carries its start position", () => {
  expect(itemHash("abc", 34.27)).toBe("#/job/abc?t=34.2");
  expect(parseHash("#/job/abc?t=34.2")).toEqual({ name: "item", token: "abc", startAt: 34.2 });
  expect(parseHash("#/job/abc")).toEqual({ name: "item", token: "abc" });
});
