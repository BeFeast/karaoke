import { expect, test } from "bun:test";
import { itemHash, publicShareUrl } from "./router";

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
