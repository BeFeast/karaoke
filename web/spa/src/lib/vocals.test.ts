import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { DEFAULT_VOCAL_PCT, rememberVocalPct, snapDetent, startVocalPct } from "./vocals";

class MemoryStorage {
  private data = new Map<string, string>();
  getItem(key: string) {
    return this.data.has(key) ? this.data.get(key)! : null;
  }
  setItem(key: string, value: string) {
    this.data.set(key, String(value));
  }
  removeItem(key: string) {
    this.data.delete(key);
  }
}

const g = globalThis as { localStorage?: unknown };
let saved: unknown;
beforeEach(() => {
  saved = g.localStorage;
  g.localStorage = new MemoryStorage();
});
afterEach(() => {
  g.localStorage = saved;
});

describe("Original vocals start level", () => {
  test("the first song on a device starts at 50%", () => {
    expect(DEFAULT_VOCAL_PCT).toBe(50);
    expect(startVocalPct()).toBe(50);
  });

  test("later songs start at the last chosen level", () => {
    rememberVocalPct(70);
    expect(startVocalPct()).toBe(70);
    rememberVocalPct(15);
    expect(startVocalPct()).toBe(15);
  });

  test("a chosen 0 is remembered too", () => {
    rememberVocalPct(0);
    expect(startVocalPct()).toBe(0);
  });

  test("levels are clamped whole percents", () => {
    rememberVocalPct(123.4);
    expect(startVocalPct()).toBe(100);
    rememberVocalPct(-3);
    expect(startVocalPct()).toBe(0);
  });

  test("garbage in storage falls back to 50%", () => {
    (g.localStorage as MemoryStorage).setItem("sonereli-vocals-default", "loud");
    expect(startVocalPct()).toBe(50);
    (g.localStorage as MemoryStorage).setItem("sonereli-vocals-default", "");
    expect(startVocalPct()).toBe(50);
  });

  test("the old per-song map is dropped once a level is chosen", () => {
    const storage = g.localStorage as MemoryStorage;
    storage.setItem("sonereli-vocals-by-song", JSON.stringify({ abc: 10 }));
    rememberVocalPct(60);
    expect(storage.getItem("sonereli-vocals-by-song")).toBeNull();
  });

  test("storage that throws: every song starts at 50%, nothing crashes", () => {
    g.localStorage = {
      getItem() {
        throw new Error("denied");
      },
      setItem() {
        throw new Error("denied");
      },
      removeItem() {
        throw new Error("denied");
      },
    };
    expect(() => rememberVocalPct(80)).not.toThrow();
    expect(startVocalPct()).toBe(50);
  });

  test("detents still pull to 0/50/100", () => {
    expect(snapDetent(48)).toBe(50);
    expect(snapDetent(2)).toBe(0);
    expect(snapDetent(97)).toBe(100);
    expect(snapDetent(40)).toBe(40);
  });
});
