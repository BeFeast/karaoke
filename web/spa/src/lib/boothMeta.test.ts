// Unit tests for the booth wiring helpers (#153): cost/duration formatting,
// the infra-strip "today" spend sum, and the relocated filter logic.
// Runs under `bun test src` (excluded from tsc — see tsconfig "exclude").

import { describe, expect, test } from "bun:test";
import type { JobOut } from "../api";
import { jobCounts, jobMatchesFilter } from "../jobStatus";
import { costDollars, fmtDuration, todaySpendMicros } from "./jobListUtils";

function job(overrides: Partial<JobOut>): JobOut {
  return {
    id: 1,
    job_token: "tok",
    source_url: "https://youtu.be/x",
    title: "t",
    artist: null,
    track: null,
    album: null,
    duration: null,
    status: "completed",
    progress: 100,
    stage_note: null,
    error: null,
    share_url: "https://example/share/tok",
    owner_subject: "owner",
    gpu_instance_id: null,
    gpu_cost_micros: null,
    created_at: "2026-06-11T10:00:00Z",
    completed_at: null,
    artifacts: [],
    ...overrides,
  };
}

describe("costDollars", () => {
  test("derives dollars from gpu_cost_micros", () => {
    expect(costDollars(310_000)).toBe("$0.31");
    expect(costDollars(1_125_000)).toBe("$1.13");
    expect(costDollars(0)).toBe("$0.00");
  });
  test("null/undefined cost renders nothing", () => {
    expect(costDollars(null)).toBeNull();
    expect(costDollars(undefined)).toBeNull();
  });
});

describe("fmtDuration", () => {
  test("formats m:ss with zero-padded seconds", () => {
    expect(fmtDuration(96)).toBe("1:36");
    expect(fmtDuration(219)).toBe("3:39");
    expect(fmtDuration(59.6)).toBe("1:00");
    expect(fmtDuration(0)).toBe("0:00");
  });
  test("unknown duration renders nothing", () => {
    expect(fmtDuration(null)).toBeNull();
    expect(fmtDuration(undefined)).toBeNull();
    expect(fmtDuration(-1)).toBeNull();
    expect(fmtDuration(Number.NaN)).toBeNull();
  });
});

describe("todaySpendMicros", () => {
  // Fixed "now": 2026-06-11 21:00 local time of the test runner is irrelevant —
  // both the job timestamps and `now` go through the same local calendar-day
  // comparison, so we pin everything to one instant.
  const now = Date.parse("2026-06-11T21:00:00");

  test("sums only today's jobs with a recorded cost", () => {
    const jobs = [
      job({ id: 1, created_at: "2026-06-11T20:00:00", gpu_cost_micros: 310_000 }),
      job({ id: 2, created_at: "2026-06-11T09:00:00", gpu_cost_micros: 290_000 }),
      job({ id: 3, created_at: "2026-06-10T23:59:00", gpu_cost_micros: 990_000 }), // yesterday
      job({ id: 4, created_at: "2026-06-11T12:00:00", gpu_cost_micros: null }), // no cost yet
      job({ id: 5, created_at: "not-a-date", gpu_cost_micros: 500_000 }),
    ];
    expect(todaySpendMicros(jobs, now)).toBe(600_000);
  });

  test("empty list sums to zero", () => {
    expect(todaySpendMicros([], now)).toBe(0);
  });
});

describe("jobMatchesFilter / jobCounts (relocated ex-Sidebar logic)", () => {
  const jobs = [
    job({ id: 1, status: "queued" }),
    job({ id: 2, status: "separating" }),
    job({ id: 3, status: "completed" }),
    job({ id: 4, status: "failed" }),
    job({ id: 5, status: "cancelled" }),
  ];

  test("active covers queued + in-flight stages", () => {
    expect(jobs.filter((j) => jobMatchesFilter(j, "active")).map((j) => j.id)).toEqual([1, 2]);
  });

  test("completed/failed are exact; all passes everything", () => {
    expect(jobs.filter((j) => jobMatchesFilter(j, "completed")).map((j) => j.id)).toEqual([3]);
    expect(jobs.filter((j) => jobMatchesFilter(j, "failed")).map((j) => j.id)).toEqual([4]);
    expect(jobs.filter((j) => jobMatchesFilter(j, "all"))).toHaveLength(5);
  });

  test("counts mirror the filters (cancelled counts only toward all)", () => {
    expect(jobCounts(jobs)).toEqual({ all: 5, active: 2, completed: 1, failed: 1 });
  });
});
