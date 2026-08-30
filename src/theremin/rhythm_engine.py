"""16-step rhythm clock for gesture sampler — hands pick instruments, clock fires them."""

from __future__ import annotations

from dataclasses import dataclass, field

STEPS = 16

# One row per sample slot (Kick … Boom).
PATTERN_PRESETS: dict[str, tuple[tuple[int, ...], ...]] = {
    "Rock": (
        (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0),
        (0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1),
        (1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0),
        (1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1),
    ),
    "Groove": (
        (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0),
        (0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 1),
        (0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0),
        (1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 1, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ),
    "Minimal": (
        (1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0),
        (0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0),
        (0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ),
    "Dense": (
        (1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0),
        (0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 1),
        (1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1),
        (0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0),
        (0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0),
    ),
    "Pulse": (
        (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ),
}


def _empty_rows(n_slots: int = 6) -> list[list[int]]:
    return [[0] * STEPS for _ in range(n_slots)]


def _normalize_rows(rows: tuple[tuple[int, ...], ...] | list[list[int]], n_slots: int = 6) -> list[list[int]]:
    out = _empty_rows(n_slots)
    for i in range(min(n_slots, len(rows))):
        row = rows[i]
        for j in range(STEPS):
            out[i][j] = 1 if j < len(row) and row[j] else 0
    return out


@dataclass
class RhythmEngine:
    """Quantized 16th-note clock; each slot has its own hit pattern."""

    bpm: float = 100.0
    n_slots: int = 6
    running: bool = True
    patterns: list[list[int]] = field(default_factory=_empty_rows)
    preset_name: str = "Rock"
    _step: int = -1
    _next_t: float | None = None

    def __post_init__(self) -> None:
        if not any(any(r) for r in self.patterns):
            self.load_preset(self.preset_name)

    def load_preset(self, name: str) -> None:
        if name not in PATTERN_PRESETS:
            name = "Rock"
        self.preset_name = name
        self.patterns = _normalize_rows(PATTERN_PRESETS[name], self.n_slots)

    def set_bpm(self, bpm: float) -> None:
        self.bpm = max(40.0, min(200.0, float(bpm)))

    def toggle_step(self, slot: int, step: int) -> None:
        i = int(slot) % self.n_slots
        j = int(step) % STEPS
        self.patterns[i][j] = 0 if self.patterns[i][j] else 1
        self.preset_name = "Custom"

    def reset(self, now: float) -> None:
        self._step = -1
        self._next_t = now

    @property
    def current_step(self) -> int:
        return max(0, self._step)

    def step_duration_s(self) -> float:
        return 60.0 / max(self.bpm, 40.0) / 4.0

    def update(self, now: float) -> tuple[list[int], list[int]]:
        """Return (fired sample indices, rhythm steps crossed this tick)."""
        if not self.running:
            self._next_t = None
            return [], []
        dt = self.step_duration_s()
        if self._next_t is None:
            self._next_t = now
        fired: list[int] = []
        steps_crossed: list[int] = []
        caught = 0
        while now >= self._next_t and caught < 4:
            self._step = (self._step + 1) % STEPS
            steps_crossed.append(self._step)
            self._next_t += dt
            caught += 1
            s = self._step
            for i in range(self.n_slots):
                if self.patterns[i][s]:
                    fired.append(i)
        return fired, steps_crossed
