import { describe, expect, test } from "bun:test";
import type { JobOut } from "../api";
import { sortLibrary } from "./MySongs";
import { processingPhase, sourceProblem, stageIndex, stageLabel } from "./Processing";

describe("Processing stages", () => {
  test("job statuses map to the singer's stages in order", () => {
    expect(["queued", "downloading", "separating", "transcribing", "finalizing", "completed"].map(
      (status) => stageIndex(status as never),
    )).toEqual([-1, 0, 1, 2, 3, 3]);
    expect(processingPhase("queued")).toBe("queued");
    expect(processingPhase("separating")).toBe("running");
    expect(processingPhase("finalizing")).toBe("running");
    expect(processingPhase("completed")).toBe("ready");
    expect(processingPhase("failed")).toBe("failed");
  });

  test("row labels name the stage, never the worker note or cost", () => {
    expect(stageLabel("separating", 46.4)).toBe("Separating vocals · 46%");
    expect(stageLabel("queued", 0)).toBe("Waiting to start");
  });

  test("source failures read as a link problem", () => {
    expect(sourceProblem("ERROR: [youtube] abc: Private video")).toBe(true);
    expect(sourceProblem("CUDA out of memory")).toBe(false);
  });
});

describe("My songs order", () => {
  const job = (id: number, token: string, completed: string) =>
    ({ id, job_token: token, completed_at: completed, created_at: completed }) as JobOut;

  test("last sung first, then most recently completed", () => {
    const rows = [job(1, "a", "2026-11-20T10:00:00Z"), job(2, "b", "2026-11-23T10:00:00Z"), job(3, "c", "2026-11-22T10:00:00Z")];
    expect(sortLibrary(rows, { c: 5 }).map((row) => row.id)).toEqual([3, 2, 1]);
    expect(sortLibrary(rows, {}).map((row) => row.id)).toEqual([2, 3, 1]);
  });
});
