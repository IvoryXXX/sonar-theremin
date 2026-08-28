from __future__ import annotations

from theremin.songs import MELODIES

KICK = (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0)
HAT = (1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0)
SNARE = (0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1)


class Deck:
    """16th-note clock for demo melodies and a tiny drum machine."""

    def __init__(self) -> None:
        self.bpm = 108.0
        self.melody_id: str | None = None
        self.kick = False
        self.hat = False
        self.snare = False
        self.pitch_mm: float | None = None
        self.velocity = 0.0
        self.snap = False
        self.note_mm: dict[str, float] = {}
        self._step = -1
        self._next_t: float | None = None
        self._melody_pos = 0
        self._last_note: str | None = None
        self._mute_until = 0.0

    @property
    def current_note(self) -> str | None:
        return self._last_note

    @property
    def running(self) -> bool:
        return self.melody_id is not None or self.kick or self.hat or self.snare

    def start_melody(self, name: str) -> None:
        if name not in MELODIES:
            return
        self.melody_id = name
        self._melody_pos = 0
        self._step = -1
        self._next_t = None
        self.pitch_mm = None
        self.velocity = 0.0
        self.snap = False
        self._last_note = None
        self._mute_until = 0.0

    def stop_melody(self) -> None:
        self.melody_id = None
        self.pitch_mm = None
        self.velocity = 0.0
        self.snap = False
        self._last_note = None

    def _steps_between(self, a: str | None, b: str | None) -> int:
        names = list(self.note_mm.keys())
        if a is None or b is None or a not in names or b not in names:
            return 99
        return abs(names.index(a) - names.index(b))

    def update(self, now: float) -> list[str]:
        self.snap = False
        if not self.running:
            self._next_t = None
            return []
        step_s = 60.0 / max(self.bpm, 40.0) / 4.0
        if self._next_t is None:
            self._next_t = now
        events: list[str] = []
        caught = 0
        while now >= self._next_t and caught < 4:
            self._step = (self._step + 1) % 16
            self._next_t += step_s
            caught += 1
            i = self._step
            if self.kick and KICK[i]:
                events.append("kick")
            if self.hat and HAT[i]:
                events.append("hat")
            if self.snare and SNARE[i]:
                events.append("snare")
            if self.melody_id is not None:
                if self.melody_id not in MELODIES:
                    self.melody_id = None
                    continue
                seq = MELODIES[self.melody_id]
                note, vel = seq[self._melody_pos % len(seq)]
                self._melody_pos += 1
                changed = note != self._last_note
                leap = changed and self._steps_between(self._last_note, note) >= 2
                if note is None:
                    self.pitch_mm = 30.0
                    self.velocity = 0.0
                else:
                    self.pitch_mm = self.note_mm.get(note, 30.0)
                    if leap or (changed and self._last_note is None):
                        self._mute_until = now + 0.05
                        self.snap = True
                    if now < self._mute_until:
                        self.velocity = 0.0
                    else:
                        self.velocity = vel
                self._last_note = note
        if self.melody_id is not None and self._melody_pos > 0:
            seq = MELODIES[self.melody_id]
            note, vel = seq[(self._melody_pos - 1) % len(seq)]
            if now < self._mute_until or note is None:
                self.velocity = 0.0
            else:
                self.velocity = vel
        return events
