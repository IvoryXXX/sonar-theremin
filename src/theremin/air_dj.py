"""Air DJ — hands control energy + filter; FX land on the beat."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

# Kick, Snare, Hat, Bass, Whoosh, Boom
KICK, SNARE, HAT, BASS, WHOOSH, BOOM = 0, 1, 2, 3, 4, 5

MM_LO = 80.0
MM_HI = 500.0
CLOSE_MM = 140.0
FAR_MM = 400.0


class FxKind(str, Enum):
    ECHO = "echo"
    FILL = "fill"
    DROP = "drop"
    CALM = "calm"


@dataclass
class PendingFx:
    kind: FxKind
    on_bar: bool = False


@dataclass
class AirDjState:
    energy: float = 0.78
    filter_open: float = 0.68
    echo: float = 0.0
    drive: float = 0.0
    mode: str = "groove"
    banner: str = ""
    layers: int = 4


@dataclass
class AirDjController:
    state: AirDjState = field(default_factory=AirDjState)
    pending: list[PendingFx] = field(default_factory=list)
    _prev_pitch: float | None = None
    _prev_vol: float | None = None
    _gesture_cooldown: float = 0.0
    _close_both_t: float | None = None
    _far_both_t: float | None = None

    def observe(
        self,
        now: float,
        pitch_mm: float | None,
        volume_mm: float | None,
    ) -> None:
        prev_p, prev_v = self._prev_pitch, self._prev_vol

        if volume_mm is not None:
            target = _energy_from_mm(volume_mm)
            self.state.energy += (target - self.state.energy) * 0.28
        if pitch_mm is not None:
            target_f = _filter_from_mm(pitch_mm)
            self.state.filter_open += (target_f - self.state.filter_open) * 0.32

        close = (
            pitch_mm is not None
            and volume_mm is not None
            and pitch_mm <= CLOSE_MM
            and volume_mm <= CLOSE_MM
        )
        far = (
            pitch_mm is not None
            and volume_mm is not None
            and pitch_mm >= FAR_MM
            and volume_mm >= FAR_MM
        )

        if close:
            self._close_both_t = self._close_both_t or now
            self._far_both_t = None
        else:
            self._close_both_t = None

        if far:
            self._far_both_t = self._far_both_t or now
            self._close_both_t = None
        else:
            self._far_both_t = None

        if now >= self._gesture_cooldown:
            if self._close_both_t is not None and now - self._close_both_t >= 0.22:
                self._queue(PendingFx(FxKind.DROP, on_bar=True))
                self.state.banner = "DROP…"
                self._gesture_cooldown = now + 1.0
                self._close_both_t = None
            elif self._far_both_t is not None and now - self._far_both_t >= 0.35:
                self._queue(PendingFx(FxKind.CALM))
                self.state.banner = "Klid…"
                self._gesture_cooldown = now + 0.8
                self._far_both_t = None
            elif prev_p is not None and pitch_mm is not None and prev_p > CLOSE_MM and pitch_mm <= CLOSE_MM:
                self._queue(PendingFx(FxKind.ECHO))
                self.state.banner = "Echo…"
                self._gesture_cooldown = now + 0.35
            elif prev_v is not None and volume_mm is not None and prev_v > CLOSE_MM and volume_mm <= CLOSE_MM:
                self._queue(PendingFx(FxKind.FILL))
                self.state.banner = "Fill…"
                self._gesture_cooldown = now + 0.35

        self._prev_pitch = pitch_mm
        self._prev_vol = volume_mm
        self._update_layers()

    def _update_layers(self) -> None:
        e = self.state.energy
        if e < 0.22:
            self.state.layers = 1
        elif e < 0.42:
            self.state.layers = 2
        elif e < 0.62:
            self.state.layers = 3
        elif e < 0.82:
            self.state.layers = 4
        else:
            self.state.layers = 5

    def on_step(self, step: int) -> FxKind | None:
        if not self.pending:
            return None
        head = self.pending[0]
        if head.on_bar and (step % 4) != 0:
            return None
        self.pending.pop(0)
        return head.kind

    def apply_fx(self, kind: FxKind) -> None:
        if kind == FxKind.ECHO:
            self.state.echo = 0.62
            self.state.banner = "Echo!"
        elif kind == FxKind.FILL:
            self.state.banner = "Fill!"
        elif kind == FxKind.DROP:
            self.state.energy = 1.0
            self.state.filter_open = 0.98
            self.state.echo = 0.18
            self.state.drive = 0.35
            self.state.layers = 5
            self.state.mode = "drop"
            self.state.banner = "DROP!"
        elif kind == FxKind.CALM:
            self.state.energy = 0.18
            self.state.filter_open = 0.35
            self.state.echo = 0.0
            self.state.drive = 0.0
            self.state.layers = 1
            self.state.mode = "klid"
            self.state.banner = "Klid"

    def tick_fx_decay(self) -> None:
        self.state.echo *= 0.994
        self.state.drive *= 0.996
        if self.state.echo < 0.02:
            self.state.echo = 0.0
        if self.state.drive < 0.02:
            self.state.drive = 0.0
        if self.state.mode == "drop" and self.state.drive < 0.05:
            self.state.mode = "groove"

    def slot_gain(self, idx: int) -> float:
        e = float(np.clip(self.state.energy, 0.08, 1.0))
        layers = self.state.layers

        if idx == KICK:
            return 0.55 + 0.45 * e
        if idx == SNARE:
            return e * (1.0 if layers >= 2 else 0.0)
        if idx == HAT:
            return e * 0.85 * (1.0 if layers >= 3 else 0.0)
        if idx == BASS:
            return e * 0.9 * (1.0 if layers >= 4 else 0.0)
        if idx == WHOOSH:
            return 0.25 + 0.35 * e if layers >= 3 else 0.0
        if idx == BOOM:
            return 0.0
        return 0.5

    def layer_labels(self) -> str:
        parts = ["Kick"]
        if self.state.layers >= 2:
            parts.append("Snare")
        if self.state.layers >= 3:
            parts.append("Hat")
        if self.state.layers >= 4:
            parts.append("Basa")
        if self.state.layers >= 5:
            parts.append("Full")
        return " + ".join(parts)

    def _queue(self, fx: PendingFx) -> None:
        if self.pending and self.pending[-1].kind == fx.kind:
            return
        if len(self.pending) < 3:
            self.pending.append(fx)


def _norm_mm(mm: float) -> float:
    t = (float(mm) - MM_LO) / max(MM_HI - MM_LO, 1.0)
    return float(np.clip(t, 0.0, 1.0))


def _energy_from_mm(mm: float) -> float:
    # Bliz = vic energie (mene mm).
    return float(np.clip(0.1 + 0.9 * (1.0 - _norm_mm(mm)), 0.1, 1.0))


def _filter_from_mm(mm: float) -> float:
    # Bliz = otevreno, dale = low-pass (tmavsi).
    return float(np.clip(0.06 + 0.94 * (1.0 - _norm_mm(mm)), 0.04, 1.0))
