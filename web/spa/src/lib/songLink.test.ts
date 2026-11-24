import { describe, expect, test } from "bun:test";
import { detectLang, t } from "../i18n";
import { parseSongLink, youtubeId } from "./songLink";

describe("song links", () => {
  test.each([
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=dQw4w9WgXcQ&si=abc",
    "https://youtu.be/dQw4w9WgXcQ?si=xyz",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
  ])("%s resolves to the video id", (url) => {
    expect(youtubeId(url)).toBe("dQw4w9WgXcQ");
    expect(parseSongLink(url)).toMatchObject({ kind: "youtube", id: "dQw4w9WgXcQ" });
  });

  test("a scheme-less YouTube link is accepted", () => {
    expect(parseSongLink("youtu.be/dQw4w9WgXcQ")).toMatchObject({ kind: "youtube", url: "https://youtu.be/dQw4w9WgXcQ" });
  });

  test("other web links stay submittable, text is not", () => {
    expect(parseSongLink("https://soundcloud.com/a/b").kind).toBe("url");
    expect(parseSongLink("  ").kind).toBe("empty");
    expect(parseSongLink("borrowed glow").kind).toBe("invalid");
    expect(parseSongLink("https://www.youtube.com/watch?v=short").kind).toBe("url");
  });
});

describe("interface language", () => {
  test("Russian only for ru* device languages", () => {
    expect(detectLang("ru-RU")).toBe("ru");
    expect(detectLang("ru")).toBe("ru");
    expect(detectLang("he-IL")).toBe("en");
    expect(detectLang("en-US")).toBe("en");
    expect(detectLang(undefined)).toBe("en");
  });

  test("every key has both languages", () => {
    expect(t("tab.mySongs", "en")).toBe("My songs");
    expect(t("tab.mySongs", "ru")).toBe("Мои песни");
    expect(t("action.add", "ru")).toBe("Добавить песню");
  });
});
