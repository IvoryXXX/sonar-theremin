from __future__ import annotations

import random
from dataclasses import dataclass

from theremin.pipeline import note_to_hz
from theremin.types import Voice

FANFARE = ("C4", "E4", "G4", "C5", "G4", "C5")


@dataclass
class SimonConfig:
    length: int = 3
    note_s: float = 0.55
    gap_s: float = 0.28
    min_hold_s: float = 0.25
    tolerance: int = 2
    clear_s: float = 0.45
    fanfare_note_s: float = 0.22
    fanfare_gap_s: float = 0.08


class SimonGame:
    IDLE = "idle"
    DEMO = "demo"
    READY = "ready"
    LISTEN = "listen"
    SUCCESS = "success"
    FANFARE = "fanfare"
    FAIL = "fail"

    def __init__(self, cfg: SimonConfig | None = None) -> None:
        self.cfg = cfg or SimonConfig()
        self.phase = self.IDLE
        self.sequence: list[str] = []
        self.scale_notes: list[str] = []
        self.note_mm: dict[str, float] = {}
        self.heard: list[str] = []
        self.status = "Simon vypnutý"
        self.banner = ""
        self.streak = 0
        self._demo_i = 0
        self._t0 = 0.0
        self._clear_t0: float | None = None
        self._hold_note: str | None = None
        self._hold_t0 = 0.0
        self._ready_commit = True
        self._demo_hz = 0.0
        self._demo_gate = False
        self._replay_same = False
        self._fanfare_i = 0

    @property
    def active(self) -> bool:
        return self.phase != self.IDLE

    @property
    def target_note(self) -> str | None:
        if self.phase == self.DEMO and self.sequence and self._demo_i < len(self.sequence):
            return self.sequence[self._demo_i]
        if self.phase == self.LISTEN and len(self.heard) < len(self.sequence):
            return self.sequence[len(self.heard)]
        if self.phase == self.FANFARE and self._fanfare_i < len(FANFARE):
            return FANFARE[self._fanfare_i]
        return None

    @property
    def lit_notes(self) -> set[str]:
        out: set[str] = set()
        if self.target_note:
            out.add(self.target_note)
        if self.phase == self.LISTEN:
            out.update(self.heard)
        return out

    def stop(self) -> None:
        self.phase = self.IDLE
        self.sequence = []
        self.heard = []
        self._demo_gate = False
        self.banner = ""
        self.status = "Simon vypnutý"

    def start(self, scale_notes: list[str], now: float, note_mm: dict[str, float] | None = None) -> None:
        names = [n for n in scale_notes if n]
        if len(names) < 2:
            self.status = "Simon: málo not ve stupnici"
            return
        self.scale_notes = names
        self.note_mm = dict(note_mm or {})
        if not self._replay_same or not self.sequence:
            self.sequence = [random.choice(names) for _ in range(max(1, self.cfg.length))]
        self._replay_same = False
        self._begin_demo(now, "Simon hraje — jen poslouchej")

    def repeat(self, now: float) -> None:
        if not self.sequence:
            self.status = "Není co opakovat — nejdřív Start"
            self.banner = ""
            return
        self._replay_same = True
        self._begin_demo(now, "Znovu stejnou melodii")

    def tick(self, now: float, gate: bool = False) -> None:
        if self.phase == self.DEMO:
            self._tick_demo(now)
        elif self.phase == self.READY:
            self._tick_ready(now, gate)
        elif self.phase == self.FANFARE:
            self._tick_fanfare(now)
        elif self.phase == self.SUCCESS:
            if now - self._t0 >= 0.35:
                self.phase = self.FANFARE
                self._fanfare_i = 0
                self._t0 = now
                self._demo_gate = False
                self.banner = "ZVLÁDL JSI TO!"
                self.status = "Oslava — Simon hraje fanfáru"
        elif self.phase == self.FAIL:
            self._demo_gate = False
            if now - self._t0 >= 1.0:
                self._replay_same = True
                self.phase = self.READY
                self._clear_t0 = None
                self.status = "Uvolni ruce — pak zahraju znovu"

    def demo_voice(self) -> Voice:
        name = self.target_note
        return Voice(
            frequency_hz=self._demo_hz if self._demo_gate else 0.0,
            amplitude=0.92 if self._demo_gate else 0.0,
            note_name=name,
            gate=self._demo_gate,
            retrigger=False,
            pitch_raw_mm=None,
            pitch_mm=None,
            volume_raw_mm=None,
            volume_mm=None,
            pitch_velocity_mm_s=None,
            strike=False,
            in_pitch_range=self._demo_gate,
            in_volume_range=True,
        )

    def feed(
        self,
        note: str | None,
        gate: bool,
        now: float,
        pitch_mm: float | None = None,
    ) -> None:
        if self.phase != self.LISTEN:
            return
        if not gate or not note:
            self._hold_note = None
            self._ready_commit = True
            self._update_listen_status()
            return
        if note != self._hold_note:
            self._hold_note = note
            self._hold_t0 = now
            self._ready_commit = True
        if self._ready_commit and (now - self._hold_t0) >= self.cfg.min_hold_s:
            self._accept(note, now, pitch_mm)
            self._ready_commit = False
        if self.phase == self.LISTEN:
            self._update_listen_status()

    def _begin_demo(self, now: float, label: str) -> None:
        self.heard = []
        self._demo_i = 0
        self._t0 = now
        self._clear_t0 = None
        self._hold_note = None
        self._ready_commit = True
        self._demo_gate = False
        self.banner = ""
        self.phase = self.DEMO
        self.status = f"{label}: " + " → ".join(self.sequence)

    def _tick_ready(self, now: float, gate: bool) -> None:
        self._demo_gate = False
        if gate:
            self._clear_t0 = None
            self.status = "Ruku z hlasitosti pryč — pak můžeš hrát"
            return
        if self._clear_t0 is None:
            self._clear_t0 = now
        left = self.cfg.clear_s - (now - self._clear_t0)
        if left > 0:
            self.status = f"Připrav se… {left:.1f}s"
            return
        if self._replay_same:
            self._replay_same = False
            self._begin_demo(now, "Simon hraje znovu")
        else:
            self.phase = self.LISTEN
            self.heard = []
            self._hold_note = None
            self._ready_commit = True
            self._update_listen_status()

    def _update_listen_status(self) -> None:
        need = len(self.sequence)
        got = len(self.heard)
        nxt = self.sequence[got] if got < need else "—"
        self.banner = f"TEĎ HRAJ  →  {nxt}"
        self.status = (
            f"Tvůj tah {got}/{need}  cíl {nxt}  ±{self.cfg.tolerance}  "
            + ("| " + " → ".join(self.heard) if self.heard else "| drž notu ~0.25 s")
        )

    def _tick_demo(self, now: float) -> None:
        elapsed = now - self._t0
        period = self.cfg.note_s + self.cfg.gap_s
        if self._demo_i >= len(self.sequence):
            self._demo_gate = False
            if elapsed >= 0.25:
                self.phase = self.READY
                self._clear_t0 = None
                self.banner = ""
                self.status = "Hotovo — uvolni ruce, pak zahraj ty"
            return
        local = elapsed - self._demo_i * period
        if local < 0:
            self._demo_gate = False
            return
        if local <= self.cfg.note_s:
            name = self.sequence[self._demo_i]
            self._demo_hz = note_to_hz(name)
            self._demo_gate = True
            self.banner = f"POSLOUCHEJ  →  {name}"
            self.status = f"Simon: {name}  ({self._demo_i + 1}/{len(self.sequence)})"
        else:
            self._demo_gate = False
            if local >= period:
                self._demo_i += 1
                if self._demo_i >= len(self.sequence):
                    self._t0 = now

    def _tick_fanfare(self, now: float) -> None:
        elapsed = now - self._t0
        period = self.cfg.fanfare_note_s + self.cfg.fanfare_gap_s
        if self._fanfare_i >= len(FANFARE):
            self._demo_gate = False
            if elapsed >= 0.45:
                self.streak += 1
                streak = self.streak
                self.stop()
                self.banner = "ZVLÁDL JSI TO!"
                self.status = f"Výborně! Série {streak}×  — Start = další kolo"
            return
        local = elapsed - self._fanfare_i * period
        if local <= self.cfg.fanfare_note_s:
            name = FANFARE[self._fanfare_i]
            self._demo_hz = note_to_hz(name)
            self._demo_gate = True
            self.banner = "ZVLÁDL JSI TO!"
            self.status = f"Fanfára: {name}"
        else:
            self._demo_gate = False
            if local >= period:
                self._fanfare_i += 1
                if self._fanfare_i >= len(FANFARE):
                    self._t0 = now

    def _accept(self, note: str, now: float, pitch_mm: float | None) -> None:
        expected = self.sequence[len(self.heard)]
        if self._close_enough(note, expected, pitch_mm):
            self.heard.append(note)
            if len(self.heard) >= len(self.sequence):
                self.phase = self.SUCCESS
                self._t0 = now
                self._demo_gate = False
                self.banner = "ZVLÁDL JSI TO!"
                self.status = "OK! " + " → ".join(self.heard)
        else:
            self.phase = self.FAIL
            self._t0 = now
            self._demo_gate = False
            self._replay_same = True
            self.banner = ""
            self.streak = 0
            self.status = f"Vedle ({note} ≠ {expected}) — zkus znovu"

    def _close_enough(self, heard: str, expected: str, pitch_mm: float | None) -> bool:
        if heard == expected:
            return True
        tol = max(0, int(self.cfg.tolerance))
        try:
            hi = self.scale_notes.index(heard)
            ei = self.scale_notes.index(expected)
            if abs(hi - ei) <= tol:
                return True
        except ValueError:
            pass
        if pitch_mm is not None and expected in self.note_mm and len(self.scale_notes) >= 2:
            centers = [self.note_mm[n] for n in self.scale_notes if n in self.note_mm]
            if len(centers) >= 2:
                zone = abs(centers[-1] - centers[0]) / max(len(centers) - 1, 1)
                if abs(pitch_mm - self.note_mm[expected]) <= zone * (tol + 0.85):
                    return True
        return False
