"""Built-in sample bank for gesture sampler mode (no external files required)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 48000

# Zone labels shown on the pitch strip (6 slots).
SAMPLE_SLOTS: tuple[tuple[str, str], ...] = (
    ("S1", "Kick"),
    ("S2", "Snare"),
    ("S3", "Hat"),
    ("S4", "Bass"),
    ("S5", "Whoosh"),
    ("S6", "Boom"),
)

SAMPLE_LABELS = tuple(f"{a}-{b}" for a, b in SAMPLE_SLOTS)
SAMPLE_INDEX: dict[str, int] = {label: i for i, label in enumerate(SAMPLE_LABELS)}


def sampler_scale() -> tuple[tuple[str, float], ...]:
    """Fake 'notes' for PitchMapper — one zone per sample."""
    return tuple((label, 110.0 * (1.06**i)) for i, label in enumerate(SAMPLE_LABELS))


def sample_index_for_note(name: str | None) -> int | None:
    if name is None:
        return None
    if name in SAMPLE_INDEX:
        return SAMPLE_INDEX[name]
    for label, idx in SAMPLE_INDEX.items():
        if label.endswith(name) or name in label:
            return idx
    return None


def display_name(note: str | None) -> str:
    if not note:
        return "-"
    if "-" in note:
        return note.split("-", 1)[1]
    return note


@dataclass
class SampleBank:
    sample_rate: int = SAMPLE_RATE
    buffers: tuple[np.ndarray, ...] = ()

    def __post_init__(self) -> None:
        if not self.buffers:
            self.buffers = tuple(_build_all(self.sample_rate))

    def get(self, index: int) -> np.ndarray:
        if not self.buffers:
            return np.zeros(1, dtype=np.float64)
        i = int(index) % len(self.buffers)
        return self.buffers[i]

    def trigger_label(self, index: int) -> str:
        i = int(index) % len(SAMPLE_SLOTS)
        return SAMPLE_SLOTS[i][1]


def _env(t: np.ndarray, attack: float, decay: float) -> np.ndarray:
    e = np.exp(-decay * t)
    a = np.minimum(1.0, t / max(attack, 1e-4))
    return e * a


def _kick(sr: int) -> np.ndarray:
    n = int(sr * 0.28)
    t = np.arange(n, dtype=np.float64) / sr
    freq = 180.0 * np.exp(-10.0 * t) + 40.0
    phase = 2.0 * np.pi * np.cumsum(freq / sr)
    body = np.sin(phase) * _env(t, 0.002, 14.0)
    click = np.sin(2.0 * np.pi * 800.0 * t) * np.exp(-80.0 * t) * 0.35
    return np.clip(body + click, -1.0, 1.0) * 0.95


def _snare(sr: int) -> np.ndarray:
    n = int(sr * 0.22)
    t = np.arange(n, dtype=np.float64) / sr
    rng = np.random.default_rng(3)
    noise = rng.uniform(-1.0, 1.0, n)
    tone = np.sin(2.0 * np.pi * 180.0 * t) * np.exp(-35.0 * t)
    env = _env(t, 0.001, 18.0)
    return np.clip((noise * 0.55 + tone * 0.45) * env, -1.0, 1.0) * 0.85


def _hat(sr: int) -> np.ndarray:
    n = int(sr * 0.08)
    t = np.arange(n, dtype=np.float64) / sr
    rng = np.random.default_rng(5)
    noise = rng.uniform(-1.0, 1.0, n)
    # High-pass-ish via differencing
    hp = np.empty(n, dtype=np.float64)
    hp[0] = noise[0]
    for i in range(1, n):
        hp[i] = 0.92 * (noise[i] - noise[i - 1])
    return hp * np.exp(-55.0 * t) * 0.7


def _bass(sr: int) -> np.ndarray:
    n = int(sr * 0.32)
    t = np.arange(n, dtype=np.float64) / sr
    root = 55.0
    sig = (
        np.sin(2.0 * np.pi * root * t) * 0.7
        + np.sin(2.0 * np.pi * root * 2.0 * t) * 0.18
        + np.sin(2.0 * np.pi * root * 3.0 * t) * 0.08
    )
    env = np.exp(-4.5 * t) * (1.0 - np.exp(-120.0 * t))
    return np.clip(sig * env, -1.0, 1.0) * 0.82


def _whoosh(sr: int) -> np.ndarray:
    n = int(sr * 0.45)
    t = np.arange(n, dtype=np.float64) / sr
    rng = np.random.default_rng(11)
    noise = rng.uniform(-1.0, 1.0, n)
    # Rising band-pass sweep
    out = np.zeros(n, dtype=np.float64)
    lp = 0.0
    for i in range(n):
        cutoff = 0.05 + 0.9 * (i / max(n - 1, 1)) ** 1.4
        alpha = 1.0 - math.exp(-2.0 * math.pi * (200.0 + 8000.0 * cutoff) / sr)
        lp += alpha * (noise[i] - lp)
        out[i] = lp
    env = np.minimum(1.0, t / 0.04) * np.exp(-2.2 * t)
    return out * env * 0.8


def _boom(sr: int) -> np.ndarray:
    n = int(sr * 0.9)
    t = np.arange(n, dtype=np.float64) / sr
    freq = 90.0 * np.exp(-2.5 * t) + 35.0
    phase = 2.0 * np.pi * np.cumsum(freq / sr)
    body = np.sin(phase)
    sub = np.sin(2.0 * np.pi * 45.0 * t) * 0.4
    return (body + sub) * np.exp(-3.0 * t) * 0.85


def _build_all(sr: int) -> list[np.ndarray]:
    return [_kick(sr), _snare(sr), _hat(sr), _bass(sr), _whoosh(sr), _boom(sr)]
