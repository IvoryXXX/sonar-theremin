from __future__ import annotations

from dataclasses import dataclass, field


def parse_mm(token: str) -> float | None:
    token = token.strip()
    if token in {"", "-", "-1", "none", "None", "nan"}:
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


@dataclass(slots=True)
class SensorFrame:
    """One snapshot from N range sensors. Invalid readings are None."""

    t_ns: int
    ranges_mm: tuple[float | None, ...]
    source: str = "unknown"

    def channel(self, index: int) -> float | None:
        if index < 0 or index >= len(self.ranges_mm):
            return None
        return self.ranges_mm[index]


@dataclass(slots=True)
class Voice:
    frequency_hz: float
    amplitude: float
    note_name: str | None
    gate: bool
    retrigger: bool
    pitch_raw_mm: float | None
    pitch_mm: float | None
    volume_raw_mm: float | None
    volume_mm: float | None
    pitch_velocity_mm_s: float | None
    strike: bool
    in_pitch_range: bool
    in_volume_range: bool


@dataclass
class Stats:
    frames: int = 0
    invalid_pitch: int = 0
    invalid_volume: int = 0
    rate_hz: float = 0.0
    last_dt_s: float = 0.0
    pitch_jitter_mm: float = 0.0
    volume_jitter_mm: float = 0.0
    strikes: int = 0
    note_changes: int = 0
    _last_t_ns: int | None = field(default=None, repr=False)
    _pitch_window: list[float] = field(default_factory=list, repr=False)
    _volume_window: list[float] = field(default_factory=list, repr=False)

    def observe(self, frame: SensorFrame, pitch_ch: int, volume_ch: int) -> None:
        self.frames += 1
        if frame.channel(pitch_ch) is None:
            self.invalid_pitch += 1
        if frame.channel(volume_ch) is None:
            self.invalid_volume += 1
        if self._last_t_ns is not None:
            dt = (frame.t_ns - self._last_t_ns) / 1e9
            if 0.0 < dt < 1.0:
                self.last_dt_s = dt
                inst = 1.0 / dt
                self.rate_hz = inst if self.rate_hz == 0.0 else self.rate_hz * 0.9 + inst * 0.1
        self._last_t_ns = frame.t_ns
        self._push_jitter(self._pitch_window, frame.channel(pitch_ch))
        self._push_jitter(self._volume_window, frame.channel(volume_ch))
        self.pitch_jitter_mm = _std(self._pitch_window)
        self.volume_jitter_mm = _std(self._volume_window)

    def _push_jitter(self, window: list[float], value: float | None) -> None:
        if value is None:
            return
        window.append(value)
        if len(window) > 40:
            del window[0 : len(window) - 40]

    def reset(self) -> None:
        self.frames = 0
        self.invalid_pitch = 0
        self.invalid_volume = 0
        self.rate_hz = 0.0
        self.last_dt_s = 0.0
        self.pitch_jitter_mm = 0.0
        self.volume_jitter_mm = 0.0
        self.strikes = 0
        self.note_changes = 0
        self._last_t_ns = None
        self._pitch_window.clear()
        self._volume_window.clear()


def _std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    return var ** 0.5
