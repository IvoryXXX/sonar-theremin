"""Edge-triggered drum pads — consistent hit on entering the strike zone."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

DEFAULT_THRESHOLD_MM = 100.0  # 10 cm
DEFAULT_TOLERANCE_MM = 15.0
DEFAULT_COOLDOWN_S = 0.12
DEFAULT_SMOOTH = 0.70  # 0 = rychle, 1 = hodne filtr


@dataclass
class DrumPad:
    """One sonar → one sample; fires when hand enters the near zone (always the same rule)."""

    sample_index: int = 0
    threshold_mm: float = DEFAULT_THRESHOLD_MM
    tolerance_mm: float = DEFAULT_TOLERANCE_MM
    cooldown_s: float = DEFAULT_COOLDOWN_S
    smooth: float = DEFAULT_SMOOTH
    label: str = "Kick"
    _buf: deque[float] = field(default_factory=lambda: deque(maxlen=11))
    _stable: float | None = None
    _zone: str = "far"
    _armed: bool = True
    _last_hit_t: float = -1.0
    _miss: int = 0
    hits: int = 0

    @property
    def rearm_mm(self) -> float:
        return self.threshold_mm + self.tolerance_mm + 18.0

    @property
    def strike_below_mm(self) -> float:
        return self.threshold_mm - self.tolerance_mm

    @property
    def _deadband_mm(self) -> float:
        s = float(np.clip(self.smooth, 0.0, 1.0))
        return 4.0 + 8.0 * s

    @property
    def _spike_mm(self) -> float:
        s = float(np.clip(self.smooth, 0.0, 1.0))
        return 32.0 + 18.0 * s

    @property
    def _confirm_n(self) -> int:
        s = float(np.clip(self.smooth, 0.0, 1.0))
        return 2 if s < 0.40 else (3 if s < 0.75 else 4)

    def update(self, now: float, mm: float | None) -> tuple[bool, float]:
        if mm is None:
            self._miss += 1
            if self._miss > 14:
                self._stable = None
                self._zone = "far"
            return False, 0.0

        self._miss = 0
        raw = float(mm)
        if self._is_spike(raw):
            return False, 0.0

        self._buf.append(raw)
        signal = self._stabilize()
        prev = self._stable
        zone = self._zone_name(signal)

        hit = False
        peak = 0.68
        entered_near = zone == "near" and self._zone != "near"
        if entered_near and self._armed and (now - self._last_hit_t) >= self.cooldown_s:
            hit = True
            self._armed = False
            self._last_hit_t = now
            self.hits += 1
            if prev is not None:
                approach = max(0.0, prev - signal)
                peak = float(np.clip(0.48 + approach / 70.0, 0.42, 1.0))

        if zone == "far":
            self._armed = True

        self._zone = zone
        self._stable = signal
        return hit, peak

    def zone(self) -> str:
        return self._zone

    def filtered_mm(self) -> float | None:
        return self._stable

    def _is_spike(self, raw: float) -> bool:
        if len(self._buf) < 4:
            return False
        med = float(sorted(self._buf)[len(self._buf) // 2])
        last = self._buf[-1]
        # Isolated echo bounce: one sample leaps away, previous was still in cluster.
        if abs(raw - med) < self._spike_mm:
            return False
        return abs(last - med) < self._spike_mm * 0.45

    def _stabilize(self) -> float:
        s = float(np.clip(self.smooth, 0.0, 1.0))
        win = max(3, min(len(self._buf), 5 + int(round(4 * s))))
        tail = list(self._buf)[-win:]
        mid = float(sorted(tail)[len(tail) // 2])
        recent = list(self._buf)[-3:]
        recent_mid = float(sorted(recent)[len(recent) // 2])
        if self._stable is None:
            return mid
        # Hand really moved: last few samples agree, ignore old buffer.
        if abs(recent_mid - self._stable) >= 22.0 and max(recent) - min(recent) < 24.0:
            self._buf.clear()
            for _ in range(5):
                self._buf.append(recent_mid)
            return recent_mid
        err = mid - self._stable
        if abs(err) < self._deadband_mm:
            return self._stable
        fast = 0.40 + 0.40 * (1.0 - s)
        slow = 0.12 + 0.20 * (1.0 - s)
        alpha = fast if abs(err) >= 25.0 else slow
        return self._stable + alpha * err

    def _zone_name(self, mm: float) -> str:
        if mm <= self.strike_below_mm:
            return "near"
        if mm >= self.rearm_mm:
            return "far"
        return "mid"

    def reset_stats(self) -> None:
        self.hits = 0
