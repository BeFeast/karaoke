import { expect, test } from "bun:test";
import { itemUrl } from "./router";

test("copied stage links use the configured public base and keep the token route", () => {
  expect(itemUrl("https://sonereli.oklabs.uk/", "unlisted_token")).toBe(
    "https://sonereli.oklabs.uk/app/#/job/unlisted_token",
  );
  expect(itemUrl("https://karaoke.oklabs.uk", "unlisted_token")).toBe(
    "https://karaoke.oklabs.uk/app/#/job/unlisted_token",
  );
});
