from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import log2

import numpy as np

from theremin.sampler import sample_index_for_note
from theremin.types import SensorFrame, Stats, Voice


def _hz(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def _midi(hz: float) -> float:
    return 69.0 + 12.0 * log2(hz / 440.0)


_LETTER = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_to_midi(name: str) -> int:
    text = name.strip()
    if len(text) < 2:
        raise ValueError(name)
    letter = text[0].upper()
    rest = text[1:]
    accidental = 0
    if rest.startswith("#") or rest.startswith("s"):
        accidental = 1
        rest = rest[1:]
    elif rest.startswith("b"):
        accidental = -1
        rest = rest[1:]
    octave = int(rest)
    return 12 * (octave + 1) + _LETTER[letter] + accidental


def note_to_hz(name: str) -> float:
    return _hz(note_to_midi(name))


def _named(*names: str) -> tuple[tuple[str, float], ...]:
    return tuple((name, note_to_hz(name)) for name in names)


NOTE_CHOICES = tuple(
    f"{letter}{octave}"
    for octave in range(3, 6)
    for letter in ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
) + ("C6",)

PENTATONIC = _named("C4", "D4", "E4", "G4", "A4", "C5")

SCALES: dict[str, tuple[tuple[str, float], ...]] = {
    "Pentatonika C": PENTATONIC,
    "C dur": _named("C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"),
    "C dur siroky": _named("C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5", "D5", "E5"),
    "A moll": _named("A3", "B3", "C4", "D4", "E4", "F4", "G4", "A4"),
    "D moll": _named("D4", "E4", "F4", "G4", "A4", "Bb4", "C5", "D5"),
}

SCALE_NAMES = tuple(SCALES.keys()) + ("Custom",)
FILTER_MODES = ("raw", "median", "ema", "median_ema")


def note_positions(
    notes: tuple[tuple[str, float], ...],
    dmin: float,
    dmax: float,
    invert: bool,
) -> dict[str, float]:
    n = max(len(notes), 1)
    span = max(dmax - dmin, 1.0)
    out: dict[str, float] = {}
    for i, (name, _) in enumerate(notes):
        t = (i + 0.5) / n
        if invert:
            t = 1.0 - t
        out[name] = dmin + t * span
    return out


@dataclass
class RuntimeConfig:
    pitch_channel: int = 0
    volume_channel: int = 1
    # Full scale fits in ~8–50 cm
    pitch_min_mm: float = 80.0
    pitch_max_mm: float = 500.0
    volume_min_mm: float = 60.0
    volume_max_mm: float = 500.0
    hysteresis_mm: float = 18.0
    jump_snap_mm: float = 45.0  # big hand jump: snap filter, don't glide through notes
    filter_mode: str = "median_ema"
    median_k: int = 3
    ema_alpha: float = 0.35
    invert_pitch: bool = False  # False: farther = higher note
    invert_volume: bool = False  # False: closer = louder
    continuous_pitch: bool = False
    pitch_magnet: float = 0.0  # 0=pure continuous, 1=snap to scale notes
    retrigger: bool = False
    volume_enabled: bool = True
    default_amp: float = 0.55  # used when volume hand is off / out of range
    space_to_play: bool = False
    muted: bool = False
    space_down: bool = False
    midi_low: float = 60.0  # C4
    midi_high: float = 72.0  # C5
    strike_mm_s: float = 900.0
    hold_missing: int = 8
    scale_name: str = "C dur"
    custom_notes: tuple[tuple[str, float], ...] | None = None
    nastroj_mode: bool = False
    guitar_mode: bool = False
    flute_mode: bool = False  # breath (vol) + fingering (pitch)
    sampler_mode: bool = False  # gesture triggers built-in samples
    snap_pick: bool = False  # swipe once → play only the nearest/winning note
    play_portamento_s: float = 0.01


class ChannelFilter:
    def __init__(
        self,
        mode: str = "median_ema",
        median_k: int = 3,
        ema_alpha: float = 0.35,
        jump_snap_mm: float = 45.0,
    ) -> None:
        self.mode = mode
        self.median_k = max(1, median_k)
        self.ema_alpha = float(np.clip(ema_alpha, 0.01, 1.0))
        self.jump_snap_mm = max(0.0, float(jump_snap_mm))
        self._buf: deque[float] = deque(maxlen=self.median_k)
        self._ema: float | None = None
        self._last: float | None = None
        self._misses = 0

    def reset(self) -> None:
        self._buf.clear()
        self._ema = None
        self._last = None
        self._misses = 0

    def snap(self, value: float | None) -> None:
        self.reset()
        if value is None:
            return
        for _ in range(self.median_k):
            self._buf.append(value)
        self._ema = value
        self._last = value
        self._misses = 0

    def configure(self, mode: str, median_k: int, ema_alpha: float, jump_snap_mm: float | None = None) -> None:
        if mode != self.mode or median_k != self.median_k:
            self._buf = deque(maxlen=max(1, median_k))
        self.mode = mode
        self.median_k = max(1, median_k)
        self.ema_alpha = float(np.clip(ema_alpha, 0.01, 1.0))
        if jump_snap_mm is not None:
            self.jump_snap_mm = max(0.0, float(jump_snap_mm))

    def process(self, value: float | None, hold_missing: int = 2) -> float | None:
        if value is None:
            self._misses += 1
            if self._misses <= hold_missing:
                return self._last
            # lost hand long enough — forget position so next hit snaps in place
            self._ema = None
            self._buf.clear()
            self._last = None
            return None
        was_missing = self._misses > 0 or self._last is None
        self._misses = 0

        # Big jump or hand reappearing: appear on the new spot, don't glide
        if was_missing or (
            self._last is not None
            and self.jump_snap_mm > 0.0
            and abs(value - self._last) >= self.jump_snap_mm
        ):
            self.snap(value)
            return value

        self._buf.append(value)
        median = float(np.median(self._buf))
        if self.mode == "raw":
            out = value
        elif self.mode == "median":
            out = median
        elif self.mode == "ema":
            out = self._step_ema(value)
        else:
            out = self._step_ema(median)
        self._last = out
        return out

    def _step_ema(self, value: float) -> float:
        if self._ema is None:
            self._ema = value
        else:
            self._ema += self.ema_alpha * (value - self._ema)
        return self._ema


class PitchMapper:
    def __init__(self, notes: tuple[tuple[str, float], ...] = PENTATONIC) -> None:
        self.notes = notes
        self._index: int | None = None

    def reset(self) -> None:
        self._index = None

    def force_note(self, name: str | None) -> None:
        if name is None:
            self._index = None
            return
        for i, (note, _) in enumerate(self.notes):
            if note == name:
                self._index = i
                return
        self._index = None

    def map(self, distance_mm: float | None, cfg: RuntimeConfig) -> tuple[str | None, float, bool]:
        if distance_mm is None:
            self._index = None
            return None, 0.0, False
        span = max(cfg.pitch_max_mm - cfg.pitch_min_mm, 1.0)
        slack = 25.0
        in_range = (cfg.pitch_min_mm - slack) <= distance_mm <= (cfg.pitch_max_mm + slack)
        if not in_range:
            self._index = None
            return None, 0.0, False
        t = (distance_mm - cfg.pitch_min_mm) / span
        t = float(np.clip(t, 0.0, 1.0))
        if cfg.invert_pitch:
            t = 1.0 - t
        if cfg.continuous_pitch:
            midi = cfg.midi_low + t * (cfg.midi_high - cfg.midi_low)
            hz = _hz(midi)
            name = _nearest_note_name(hz, self.notes)
            magnet = float(np.clip(cfg.pitch_magnet, 0.0, 1.0))
            if magnet > 0.0 and self.notes:
                nearest_hz = min(self.notes, key=lambda item: abs(item[1] - hz))[1]
                # Pull only when close to a scale note — avoids constant "out of tune" drag.
                cents = abs(1200.0 * log2(max(hz, 1e-6) / max(nearest_hz, 1e-6)))
                proximity = float(np.clip(1.0 - cents / 90.0, 0.0, 1.0))
                m = magnet * proximity
                if m > 0.001:
                    midi_n = _midi(nearest_hz)
                    midi = midi * (1.0 - m) + midi_n * m
                    hz = _hz(midi)
                    name = _nearest_note_name(hz, self.notes)
            return name, hz, True
        n = len(self.notes)
        zone_mm = span / max(n, 1)
        hyst_mm = min(cfg.hysteresis_mm, zone_mm * 0.3)
        hyst_t = hyst_mm / span
        if self._index is None:
            self._index = min(int(t * n), n - 1)
        else:
            i = self._index
            lo = i / n - hyst_t
            hi = (i + 1) / n + hyst_t
            if t < lo:
                self._index = max(0, min(int(t * n), n - 1))
            elif t >= hi:
                self._index = max(0, min(int(t * n), n - 1))
        name, hz = self.notes[self._index]
        return name, hz, True


def volume_amplitude(distance_mm: float | None, cfg: RuntimeConfig) -> tuple[float, bool]:
    """Return (amp, in_volume_range).

    Theremin: out-of-range / disabled → default_amp (pitch still plays).
    Guitar: always map distance (clamped) — value = sustain length, not loudness.
    Flute: breath — out of range = silence; closer = louder blow.
    """
    default = float(np.clip(cfg.default_amp, 0.05, 1.0))
    if not cfg.volume_enabled:
        return default, True
    if distance_mm is None:
        if cfg.flute_mode:
            return 0.0, False
        return default, False
    span = max(cfg.volume_max_mm - cfg.volume_min_mm, 1.0)
    in_range = cfg.volume_min_mm <= distance_mm <= cfg.volume_max_mm
    t = (distance_mm - cfg.volume_min_mm) / span
    t = float(np.clip(t, 0.0, 1.0))
    amp = t if cfg.invert_volume else (1.0 - t)
    if amp < 0.02:
        amp = 0.02

    if cfg.flute_mode:
        if not in_range:
            return 0.0, False
        return amp, True
    if cfg.guitar_mode or cfg.sampler_mode:
        return amp, in_range
    if not in_range:
        return default, False
    return amp, True


def _nearest_note_name(hz: float, notes: tuple[tuple[str, float], ...]) -> str:
    return min(notes, key=lambda item: abs(item[1] - hz))[0]


class Pipeline:
    def __init__(self, cfg: RuntimeConfig | None = None) -> None:
        self.cfg = cfg or RuntimeConfig()
        self.pitch_filter = ChannelFilter(
            self.cfg.filter_mode, self.cfg.median_k, self.cfg.ema_alpha, self.cfg.jump_snap_mm
        )
        self.volume_filter = ChannelFilter(
            self.cfg.filter_mode, self.cfg.median_k, self.cfg.ema_alpha, self.cfg.jump_snap_mm
        )
        self.mapper = PitchMapper(SCALES[self.cfg.scale_name])
        self.apply_scale(self.cfg.scale_name)
        self.stats = Stats()
        self._prev_pitch: float | None = None
        self._prev_t_ns: int | None = None
        self._last_note: str | None = None
        self._was_gated: bool = False
        self._swipe_mm: list[float] = []
        self._snap_hz: float | None = None
        self._snap_note: str | None = None
        self.last_voice: Voice | None = None
        self.last_pick: str | None = None

    def reset(self) -> None:
        self.pitch_filter.reset()
        self.volume_filter.reset()
        self.mapper.reset()
        self.stats.reset()
        self._prev_pitch = None
        self._prev_t_ns = None
        self._last_note = None
        self._was_gated = False
        self._swipe_mm.clear()
        self._snap_hz = None
        self._snap_note = None
        self.last_voice = None
        self.last_pick = None

    def apply_sampler(self) -> None:
        from theremin.sampler import sampler_scale

        self.mapper.notes = sampler_scale()
        self.mapper.reset()
        self.cfg.scale_name = "Sampler"
        self.cfg.custom_notes = None

    def apply_scale(self, name: str) -> None:
        if name == "Custom":
            notes = self.cfg.custom_notes or self.mapper.notes
            self.cfg.custom_notes = notes
        elif name in SCALES:
            notes = SCALES[name]
            self.cfg.custom_notes = None
        else:
            name = "C dur"
            notes = SCALES[name]
            self.cfg.custom_notes = None
        self.cfg.scale_name = name
        self.mapper.notes = notes
        self.mapper.reset()
        self.cfg.midi_low = _midi(notes[0][1])
        self.cfg.midi_high = _midi(notes[-1][1])

    def apply_custom(self, names: tuple[str, ...]) -> None:
        notes = _named(*names)
        self.cfg.custom_notes = notes
        self.apply_scale("Custom")

    def snap_to(self, pitch_mm: float | None, volume_mm: float | None, note: str | None) -> None:
        self.pitch_filter.snap(pitch_mm)
        self.volume_filter.snap(volume_mm)
        self.mapper.force_note(note)
        self._prev_pitch = pitch_mm

    def sync_filters(self) -> None:
        for filt in (self.pitch_filter, self.volume_filter):
            filt.configure(
                self.cfg.filter_mode,
                self.cfg.median_k,
                self.cfg.ema_alpha,
                self.cfg.jump_snap_mm,
            )

    def push(self, frame: SensorFrame) -> Voice:
        cfg = self.cfg
        self.stats.observe(frame, cfg.pitch_channel, cfg.volume_channel)
        raw_p = frame.channel(cfg.pitch_channel)
        raw_v = frame.channel(cfg.volume_channel)

        prev_pitch = self.pitch_filter._last
        pitch = self.pitch_filter.process(raw_p, cfg.hold_missing)
        volume = self.volume_filter.process(raw_v, cfg.hold_missing)

        # Hand teleported — pick the new note zone immediately (no glide through neighbors)
        if (
            pitch is not None
            and prev_pitch is not None
            and abs(pitch - prev_pitch) >= cfg.jump_snap_mm
        ):
            self.mapper.reset()
        elif pitch is not None and prev_pitch is None:
            self.mapper.reset()

        vy = None
        if pitch is not None and self._prev_pitch is not None and self._prev_t_ns is not None:
            dt = (frame.t_ns - self._prev_t_ns) / 1e9
            if dt > 1e-4:
                vy = (pitch - self._prev_pitch) / dt
        self._prev_pitch = pitch
        self._prev_t_ns = frame.t_ns

        toward_sensor = False
        if vy is not None:
            toward_sensor = vy < -cfg.strike_mm_s if not cfg.invert_pitch else vy > cfg.strike_mm_s
        strike = bool(toward_sensor)
        if strike:
            self.stats.strikes += 1

        note, hz, in_pitch = self.mapper.map(pitch, cfg)
        amp, in_vol = volume_amplitude(volume, cfg)

        pitch_hand = bool(in_pitch and hz > 0.0 and not cfg.muted)
        if cfg.space_to_play:
            pitch_hand = pitch_hand and cfg.space_down

        # Flute (no snap): need breath. Snap collect uses pitch hand only.
        if cfg.flute_mode and not cfg.snap_pick:
            gate = pitch_hand and in_vol and amp >= 0.03
        else:
            gate = pitch_hand

        if not gate and not cfg.guitar_mode and not cfg.sampler_mode and not cfg.snap_pick:
            amp = 0.0

        retrigger = False
        pick_note = note
        pick_hz = hz
        play_style = cfg.guitar_mode or cfg.sampler_mode

        if cfg.snap_pick:
            # Swipe silently → on leave play only the nearest zone (note or sample).
            if pitch_hand and pitch is not None:
                if not self._was_gated:
                    self._snap_hz = None
                    self._snap_note = None
                self._swipe_mm.append(float(pitch))
                if len(self._swipe_mm) > 80:
                    self._swipe_mm = self._swipe_mm[-80:]
                gate_out = False
                freq_out = 0.0
                amp_out = amp
                note_out = None
                sample_out = None
            elif self._was_gated and not pitch_hand and len(self._swipe_mm) >= 2:
                pick_note, pick_hz = self._pick_nearest_note(self._swipe_mm, cfg)
                self._swipe_mm.clear()
                self.last_pick = pick_note
                self._snap_note = pick_note
                self._snap_hz = pick_hz if pick_hz > 0.0 else None
                retrigger = self._snap_hz is not None
                if cfg.flute_mode:
                    sounding = in_vol and amp >= 0.03
                    gate_out = bool(retrigger and sounding)
                    freq_out = self._snap_hz or 0.0
                    amp_out = amp if sounding else 0.0
                elif play_style:
                    gate_out = retrigger
                    freq_out = self._snap_hz or 0.0
                    amp_out = amp
                else:
                    gate_out = retrigger
                    freq_out = self._snap_hz or 0.0
                    amp_out = amp if amp > 0 else cfg.default_amp
                note_out = pick_note
                sample_out = sample_index_for_note(pick_note) if cfg.sampler_mode else None
            elif self._snap_hz is not None:
                if play_style:
                    gate_out = False
                    freq_out = self._snap_hz
                    amp_out = amp
                    note_out = self._snap_note
                    sample_out = sample_index_for_note(self._snap_note) if cfg.sampler_mode else None
                elif cfg.flute_mode:
                    sounding = in_vol and amp >= 0.03
                    gate_out = sounding
                    freq_out = self._snap_hz
                    amp_out = amp if sounding else 0.0
                    note_out = self._snap_note
                    sample_out = None
                else:
                    sounding = in_vol and amp >= 0.04
                    gate_out = sounding
                    freq_out = self._snap_hz
                    amp_out = amp if sounding else 0.0
                    note_out = self._snap_note
                    sample_out = sample_index_for_note(self._snap_note) if cfg.sampler_mode else None
            else:
                if not pitch_hand:
                    self._swipe_mm.clear()
                gate_out = False
                freq_out = self.last_voice.frequency_hz if self.last_voice else 0.0
                amp_out = amp
                note_out = None
                sample_out = None
            if pitch_hand:
                self._last_note = note
            else:
                self._last_note = None
            self._was_gated = pitch_hand
            voice = Voice(
                frequency_hz=freq_out,
                amplitude=amp_out,
                note_name=note_out,
                gate=gate_out,
                retrigger=retrigger,
                pitch_raw_mm=raw_p,
                pitch_mm=pitch,
                volume_raw_mm=raw_v,
                volume_mm=volume,
                pitch_velocity_mm_s=vy,
                strike=strike,
                in_pitch_range=in_pitch,
                in_volume_range=in_vol,
                sample_index=sample_out,
            )
            self.last_voice = voice
            return voice

        if play_style:
            if gate and note is not None and (note != self._last_note or not self._was_gated):
                retrigger = True
        elif cfg.flute_mode:
            if gate and note is not None and note != self._last_note:
                retrigger = self._last_note is not None  # tongue when changing finger
        elif cfg.retrigger and gate and note is not None and note != self._last_note:
            retrigger = self._last_note is not None
        if note != self._last_note and note is not None:
            self.stats.note_changes += 1
        if gate:
            self._last_note = note
        else:
            self._last_note = None
        self._was_gated = gate

        if play_style:
            freq_out = hz if gate else (self.last_voice.frequency_hz if self.last_voice else 0.0)
            amp_out = amp
            gate_out = gate
        elif cfg.flute_mode:
            freq_out = hz if gate else (self.last_voice.frequency_hz if self.last_voice else 0.0)
            amp_out = amp if gate else 0.0
            gate_out = gate
        else:
            freq_out = hz if gate or in_pitch else 0.0
            amp_out = amp if gate else 0.0
            gate_out = gate

        sample_out = sample_index_for_note(note) if cfg.sampler_mode and retrigger else None

        voice = Voice(
            frequency_hz=freq_out,
            amplitude=amp_out,
            note_name=note if in_pitch else None,
            gate=gate_out,
            retrigger=retrigger,
            pitch_raw_mm=raw_p,
            pitch_mm=pitch,
            volume_raw_mm=raw_v,
            volume_mm=volume,
            pitch_velocity_mm_s=vy,
            strike=strike,
            in_pitch_range=in_pitch,
            in_volume_range=in_vol,
            sample_index=sample_out if cfg.sampler_mode else None,
        )
        self.last_voice = voice
        return voice

    def _pick_nearest_note(
        self, samples_mm: list[float], cfg: RuntimeConfig
    ) -> tuple[str | None, float]:
        """Pick the scale note whose zone center is closest to the swipe median distance."""
        if not samples_mm or not self.mapper.notes:
            return None, 0.0
        target = float(np.median(np.asarray(samples_mm, dtype=np.float64)))
        # Also favor notes that appeared most during the swipe (stable through a zone).
        votes: dict[str, int] = {}
        for mm in samples_mm:
            n, _, ok = self.mapper.map(mm, cfg)
            if ok and n:
                votes[n] = votes.get(n, 0) + 1
        # Distance of each note center to median sample
        n_notes = len(self.mapper.notes)
        span = max(cfg.pitch_max_mm - cfg.pitch_min_mm, 1.0)
        best_name = None
        best_hz = 0.0
        best_score = -1e18
        for i, (name, hz) in enumerate(self.mapper.notes):
            t = (i + 0.5) / n_notes
            if cfg.invert_pitch:
                t = 1.0 - t
            center = cfg.pitch_min_mm + t * span
            dist = abs(center - target)
            vote = float(votes.get(name, 0))
            # Lower distance is better; more votes better.
            score = vote * 30.0 - dist
            if score > best_score:
                best_score = score
                best_name = name
                best_hz = float(hz)
        return best_name, best_hz
