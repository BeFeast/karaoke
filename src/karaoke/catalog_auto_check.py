"""Measure the exports of an immutable catalog pilot job before publication."""
from __future__ import annotations

import math
import re
import statistics
import subprocess
import sys
from array import array
from bisect import bisect_left
from contextlib import suppress
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

SAMPLE_RATE = 8_000
FRAME_SECONDS = 0.05
CHANNELS = 2
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_SECONDS) * CHANNELS
MAX_AUDIO_SECONDS = 900
LINE = re.compile(r"^\[(\d+):([0-5]\d)(?:[.:](\d{1,3}))?\](.*)$")
METADATA = re.compile(r"^\[(?:ar|al|ti|au|by|re|ve|length):[^\]]*\]$", re.I)
WORD = re.compile(r"<(\d+):([0-5]\d)(?:\.(\d{1,3}))?>")


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    duration_tolerance_s: float = Field(default=2, gt=0, le=2)
    rms_floor: float = Field(default=0.015, gt=0, lt=1)
    vocal_active_floor: float = Field(default=0.003, gt=0, lt=1)
    vocal_active_relative: float = Field(default=0.18, gt=0, lt=1)
    leakage_gain_max: float = Field(default=0.12, gt=0, lt=1)
    leakage_correlation_max: float = Field(default=0.20, gt=0, lt=1)
    lyrics_coverage_min: float = Field(default=0.8, ge=0.8, le=1)
    lyric_gap_max_s: float = Field(default=5, gt=0)
    lyric_line_window_s: float = Field(default=8, gt=0)
    onset_rise_ratio: float = Field(default=1.55, gt=1)
    onset_offset_max_s: float = Field(default=0.5, gt=0, le=0.5)


class Report(BaseModel):
    thresholds: Thresholds
    values: dict[str, bool | int | float | None] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)

    def check(self, key: str, passed: bool) -> None:
        if not passed:
            self.reasons.append(key)


def _decode(path: Path, duration_limit: float, *, vocal_band: bool = False) -> array:
    filters = ["aresample=8000"]
    if vocal_band:
        filters.extend(("highpass=f=250", "lowpass=f=3400"))
    audio = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-threads", "1", "-i", str(path),
         "-map", "0:a:0", "-vn", "-filter_threads", "1", "-af", ",".join(filters),
         "-t", str(duration_limit), "-ac", str(CHANNELS), "-f", "s16le", "-"],
        capture_output=True, timeout=90, check=True,
    ).stdout
    pcm = array("h")
    pcm.frombytes(audio)
    if sys.byteorder != "little":
        pcm.byteswap()
    if not pcm:
        raise ValueError("No decoded audio")
    return pcm


def _rms(pcm: array) -> float:
    return math.sqrt(math.sumprod(pcm, pcm) / len(pcm)) / 32768


def _timer(match: re.Match) -> float:
    return int(match[1]) * 60 + int(match[2]) + float("0." + (match[3] or "0"))


def _lyrics_times(lrc: bytes, duration: float) -> tuple[list[float], bool]:
    previous = -1.0
    starts = []
    monotonic = True
    for raw in lrc.decode("utf-8-sig").splitlines():
        line = raw.strip()
        if not line or METADATA.fullmatch(line):
            continue
        if not (tag := LINE.fullmatch(line)):
            monotonic = False
            continue
        start = _timer(tag)
        words = [_timer(word) for word in WORD.finditer(tag[4])]
        if (
            start <= previous or start > duration
            or any(left >= right for left, right in zip(words, words[1:], strict=False))
            or (words and (words[0] < start - 2 or words[-1] > duration))
        ):
            monotonic = False
        previous = start
        if WORD.sub("", tag[4]).strip() and not tag[4].lstrip().startswith("[speech]"):
            starts.append(start)
    return starts, monotonic


def _vocal_activity(vocals: array, thresholds: Thresholds) -> tuple[list[bool], list[float], float]:
    levels = [_rms(vocals[start:start + FRAME_SAMPLES]) for start in range(0, len(vocals), FRAME_SAMPLES)]
    loud = sorted(levels)[int(0.9 * (len(levels) - 1))]
    floor = max(thresholds.vocal_active_floor, loud * thresholds.vocal_active_relative)
    active = [level >= floor for level in levels]
    onsets = []
    for i, level in enumerate(levels):
        if not active[i] or (onsets and i * FRAME_SECONDS - onsets[-1] < 0.2):
            continue
        history = levels[max(0, i - 4):i]
        if not history or level >= max(floor, statistics.median(history) * thresholds.onset_rise_ratio):
            onsets.append(i * FRAME_SECONDS)
    return active, onsets, floor


def _leakage(vocals: array, instrumental: array, active: list[bool]) -> tuple[float, float]:
    # Project the instrumental onto the vocal waveform from the same time
    # window. Per-channel projection prevents opposite phase from cancelling.
    gains, correlations = [], []
    for channel in range(CHANNELS):
        dot = vocal_power = instrumental_power = 0
        for i, present in enumerate(active):
            if not present:
                continue
            start = i * FRAME_SAMPLES + channel
            end = min((i + 1) * FRAME_SAMPLES, len(vocals), len(instrumental))
            vocal = vocals[start:end:CHANNELS]
            backing = instrumental[start:end:CHANNELS]
            dot += sum(left * right for left, right in zip(vocal, backing, strict=True))
            vocal_power += sum(value * value for value in vocal)
            instrumental_power += sum(value * value for value in backing)
        gains.append(abs(dot) / vocal_power if vocal_power else 0)
        correlations.append(abs(dot) / math.sqrt(vocal_power * instrumental_power)
                            if vocal_power and instrumental_power else 0)
    return max(gains), max(correlations)


def _lyric_metrics(report: Report, active: list[bool], onsets: list[float], starts: list[float]) -> None:
    limits = report.thresholds
    first_line = 0
    covered = gap = longest = 0
    silent = 0
    for i, present in enumerate(active):
        t = i * FRAME_SECONDS
        while first_line + 1 < len(starts) and starts[first_line + 1] <= t + limits.onset_offset_max_s:
            first_line += 1
        offset = t - starts[first_line]
        if not present:
            silent += 1
            if silent * FRAME_SECONDS >= 0.5:
                gap = 0
            continue
        silent = 0
        if -limits.onset_offset_max_s <= offset <= limits.lyric_line_window_s:
            covered += 1
            gap = 0
        else:
            gap += 1
            longest = max(longest, gap)
    coverage = covered / sum(active)
    gap_s = longest * FRAME_SECONDS
    report.values.update(lyrics_coverage=coverage, longest_uncovered_activity_s=gap_s)
    report.check("lyrics_coverage", coverage >= limits.lyrics_coverage_min)
    report.check("lyrics_gap", gap_s <= limits.lyric_gap_max_s)

    offsets = []
    for start in starts:
        i = bisect_left(onsets, start)
        choices = onsets[max(0, i - 1):i + 1]
        if choices:
            offsets.append(min(abs(start - onset) for onset in choices))
    offset = statistics.median(offsets) if offsets else None
    report.values["median_lyric_onset_offset_s"] = offset
    report.check("lyric_onset_offset", offset is not None and offset <= limits.onset_offset_max_s)


def measure(exports: Path, duration: float | None, lrc: bytes | None, thresholds: Thresholds) -> Report:
    report = Report(thresholds=thresholds)
    report.values["source_duration_s"] = duration
    valid_duration = duration is not None and 0 < duration <= MAX_AUDIO_SECONDS
    report.check("source_duration", valid_duration)
    limit = duration + thresholds.duration_tolerance_s + 1 if valid_duration else MAX_AUDIO_SECONDS + 1
    bands = {}
    for name in ("vocals", "karaoke"):
        path = exports / f"{name}.mp3"
        exists = path.is_file()
        report.values[f"{name}_exists"] = exists
        report.values[f"{name}_decodes"] = False
        if not exists:
            report.check(f"{name}_missing", False)
            continue
        try:
            pcm = _decode(path, limit)
            seconds = len(pcm) / (SAMPLE_RATE * CHANNELS)
            rms = _rms(pcm)
            bands[name] = _decode(path, limit, vocal_band=True)
        except (OSError, subprocess.SubprocessError, ValueError):
            report.check(f"{name}_decode", False)
            continue
        report.values.update({f"{name}_decodes": True, f"{name}_duration_s": seconds, f"{name}_rms": rms})
        report.check(f"{name}_duration", valid_duration and abs(seconds - duration) <= thresholds.duration_tolerance_s)
        report.check(f"{name}_rms", rms > thresholds.rms_floor)

    starts = []
    monotonic = False
    if lrc:
        with suppress(UnicodeDecodeError):
            starts, monotonic = _lyrics_times(lrc, limit - 1 if valid_duration else limit)
    report.values.update(lyric_lines=len(starts), lyric_timestamps_monotonic=monotonic)
    report.check("synced_lyrics_missing", len(starts) >= 2)
    report.check("lyric_timestamps", monotonic)
    if "vocals" not in bands:
        return report
    vocals = bands["vocals"]
    active, onsets, floor = _vocal_activity(vocals, thresholds)
    report.values.update(vocal_active_s=sum(active) * FRAME_SECONDS, vocal_onsets=len(onsets), vocal_active_rms_floor=floor)
    report.check("vocal_activity", any(active))
    if any(active):
        if "karaoke" in bands:
            gain, correlation = _leakage(vocals, bands["karaoke"], active)
            report.values.update(vocal_band_leakage_gain=gain, vocal_band_correlation=correlation)
            report.check("vocal_band_leakage", gain <= thresholds.leakage_gain_max)
            report.check("vocal_band_correlation", correlation <= thresholds.leakage_correlation_max)
        if len(starts) >= 2 and monotonic:
            _lyric_metrics(report, active, onsets, starts)
    return report
