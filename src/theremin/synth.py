from __future__ import annotations

import math

import numpy as np

from theremin.types import Voice

# Named like keyboard Tone/Voice: same notes, different colour.
VOICES = {
    "Pistala": {
        "wave": "sine",
        "wave2": "sine",
        "detune": 1200.0,
        "mix2": 0.18,
        "sub": 0.0,
        "noise": 0.05,
        "cutoff": 0.42,
        "drive": 1.0,
        "crush": 0.0,
        "portamento_s": 0.04,
        "attack_s": 0.045,
        "release_s": 0.2,
        "gain": 1.15,
    },
    "Varhany": {
        "wave": "organ",
        "wave2": "sine",
        "detune": 1200.0,
        "mix2": 0.28,
        "sub": 0.08,
        "noise": 0.0,
        "cutoff": 0.84,
        "drive": 1.15,
        "crush": 0.0,
        "portamento_s": 0.008,
        "attack_s": 0.006,
        "release_s": 0.08,
        "gain": 0.95,
    },
    "8-bit": {
        "wave": "square",
        "wave2": "pulse",
        "detune": 0.0,
        "mix2": 0.12,
        "sub": 0.0,
        "noise": 0.0,
        "cutoff": 0.96,
        "drive": 1.12,
        "crush": 4.0,
        "portamento_s": 0.001,
        "attack_s": 0.002,
        "release_s": 0.045,
        "gain": 0.72,
    },
    "Plocha": {
        "wave": "triangle",
        "wave2": "sine",
        "detune": 8.0,
        "mix2": 0.55,
        "sub": 0.12,
        "noise": 0.02,
        "cutoff": 0.32,
        "drive": 1.05,
        "crush": 0.0,
        "portamento_s": 0.07,
        "attack_s": 0.09,
        "release_s": 0.35,
        "gain": 1.2,
    },
    "Sci-fi": {
        "wave": "saw",
        "wave2": "pulse",
        "detune": 9.0,
        "mix2": 0.45,
        "sub": 0.1,
        "noise": 0.03,
        "cutoff": 0.55,
        "drive": 1.2,
        "crush": 0.0,
        "portamento_s": 0.025,
        "attack_s": 0.012,
        "release_s": 0.12,
        "gain": 0.78,
    },
    "Fanfara": {
        "wave": "saw",
        "wave2": "square",
        "detune": 4.0,
        "mix2": 0.22,
        "sub": 0.15,
        "noise": 0.01,
        "cutoff": 0.5,
        "drive": 1.25,
        "crush": 0.0,
        "portamento_s": 0.018,
        "attack_s": 0.055,
        "release_s": 0.14,
        "gain": 1.05,
    },
    "Bass": {
        "wave": "bass",
        "wave2": "sine",
        "detune": -1200.0,
        "mix2": 0.35,
        "sub": 0.25,
        "noise": 0.0,
        "cutoff": 0.28,
        "drive": 1.9,
        "crush": 0.0,
        "portamento_s": 0.02,
        "attack_s": 0.008,
        "release_s": 0.12,
        "gain": 1.05,
    },
    "Brnk": {
        "wave": "pulse",
        "wave2": "triangle",
        "detune": 3.0,
        "mix2": 0.3,
        "sub": 0.0,
        "noise": 0.04,
        "cutoff": 0.68,
        "drive": 1.25,
        "crush": 0.0,
        "portamento_s": 0.004,
        "attack_s": 0.002,
        "release_s": 0.16,
        "gain": 0.85,
    },
}

VOICE_NAMES = tuple(VOICES.keys())


class Synth:
    def __init__(self, sample_rate: int = 48000, master: float = 0.2) -> None:
        self.sample_rate = sample_rate
        self.master = master
        self.timbre = "Pistala"
        self.waveform = "sine"
        self.waveform2 = "sine"
        self.detune = 0.0
        self.mix2 = 0.0
        self.sub = 0.0
        self.noise = 0.0
        self.cutoff = 0.5
        self.drive = 1.0
        self.crush = 0.0
        self.gain = 1.0
        self.brightness = 0.55
        self.portamento_s = 0.012
        self.attack_s = 0.008
        self.release_s = 0.07
        self.muted = False
        self.drum_gain = 0.7
        self._freq = 220.0
        self._amp = 0.0
        self._target_freq = 220.0
        self._target_amp = 0.0
        self._phase = 0.0
        self._phase2 = 0.0
        self._sub_phase = 0.0
        self._lp = 0.0
        self._kick_env = 0.0
        self._kick_hz = 80.0
        self._kick_phase = 0.0
        self._kick_trig = False
        self._hat_env = 0.0
        self._hat_trig = False
        self._snare_env = 0.0
        self._snare_phase = 0.0
        self._snare_trig = False
        self._rng = np.random.default_rng(7)
        self._stream = None
        self.error: str | None = None
        self.apply_timbre("Pistala")

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            self.error = "sounddevice is not installed"
            return
        try:
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=256,
                latency="low",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self._stream = None

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def apply_voice(self, voice: Voice) -> None:
        if voice.frequency_hz > 0.0:
            self._target_freq = voice.frequency_hz
        if voice.retrigger:
            self._amp *= 0.12
        if self.muted:
            self._target_amp = 0.0
        else:
            self._target_amp = voice.amplitude * self.master if voice.gate else 0.0

    def apply_timbre(self, name: str) -> None:
        if name not in VOICES:
            name = "Pistala"
        patch = VOICES[name]
        self.timbre = name
        self.waveform = patch["wave"]
        self.waveform2 = patch["wave2"]
        self.detune = float(patch["detune"])
        self.mix2 = float(patch["mix2"])
        self.sub = float(patch["sub"])
        self.noise = float(patch["noise"])
        self.cutoff = float(patch["cutoff"])
        self.drive = float(patch["drive"])
        self.crush = float(patch["crush"])
        self.gain = float(patch["gain"])
        self.portamento_s = float(patch["portamento_s"])
        self.attack_s = float(patch["attack_s"])
        self.release_s = float(patch["release_s"])

    def silence(self) -> None:
        self._target_amp = 0.0

    def trigger_kick(self) -> None:
        self._kick_trig = True

    def trigger_hat(self) -> None:
        self._hat_trig = True

    def trigger_snare(self) -> None:
        self._snare_trig = True

    def _callback(self, outdata, frames, _time_info, _status) -> None:  # noqa: ANN001
        sr = float(self.sample_rate)
        n = np.arange(frames, dtype=np.float64)
        kf = 1.0 - math.exp(-1.0 / max(self.portamento_s * sr, 1.0))
        if self._target_amp >= self._amp:
            ka = 1.0 - math.exp(-1.0 / max(self.attack_s * sr, 1.0))
        else:
            ka = 1.0 - math.exp(-1.0 / max(self.release_s * sr, 1.0))
        freq = self._target_freq + (self._freq - self._target_freq) * (1.0 - kf) ** n
        amp = self._target_amp + (self._amp - self._target_amp) * (1.0 - ka) ** n
        phase = self._phase + np.cumsum(freq / sr)
        osc = _oscillator(phase, self.waveform)
        if self.mix2 > 0.0:
            ratio = 2.0 ** (self.detune / 1200.0)
            phase2 = self._phase2 + np.cumsum(freq * ratio / sr)
            osc = (1.0 - self.mix2) * osc + self.mix2 * _oscillator(phase2, self.waveform2)
            self._phase2 = float(phase2[-1] % 1.0)
        else:
            self._phase2 = float(self._phase2)
        if self.sub > 0.0:
            sub_phase = self._sub_phase + np.cumsum(0.5 * freq / sr)
            osc = osc + self.sub * np.sin(2.0 * np.pi * (sub_phase % 1.0))
            self._sub_phase = float(sub_phase[-1] % 1.0)
        if self.noise > 0.0:
            osc = osc + self.noise * self._rng.uniform(-1.0, 1.0, frames)
        if self.crush > 1.0:
            osc = np.round(osc * self.crush) / self.crush
        if self.drive > 1.02:
            osc = np.tanh(self.drive * osc) / math.tanh(self.drive)
        bright = float(np.clip(self.cutoff + (self.brightness - 0.5) * 0.75, 0.02, 0.99))
        if bright < 0.93:
            osc = self._lowpass(osc, bright)
        else:
            self._lp = float(osc[-1])
        lead = amp * self.gain * osc
        drums = self._drums(n, sr, frames) * (0.0 if self.muted else self.drum_gain)
        out = (lead + drums).astype(np.float32)
        np.clip(out, -0.95, 0.95, out=out)
        outdata[:, 0] = out
        self._phase = float(phase[-1] % 1.0)
        self._freq = float(freq[-1])
        self._amp = float(amp[-1])

    def _lowpass(self, signal: np.ndarray, cutoff: float) -> np.ndarray:
        hz = 160.0 * (14000.0 / 160.0) ** float(cutoff)
        alpha = 1.0 - math.exp(-2.0 * math.pi * hz / self.sample_rate)
        out = np.empty_like(signal)
        state = self._lp
        for i, sample in enumerate(signal):
            state += alpha * (sample - state)
            out[i] = state
        self._lp = float(state)
        return out

    def _drums(self, n: np.ndarray, sr: float, frames: int) -> np.ndarray:
        if self._kick_trig:
            self._kick_env = 1.0
            self._kick_hz = 150.0
            self._kick_phase = 0.0
            self._kick_trig = False
        if self._hat_trig:
            self._hat_env = 0.55
            self._hat_trig = False
        if self._snare_trig:
            self._snare_env = 0.7
            self._snare_phase = 0.0
            self._snare_trig = False

        kick_env = self._kick_env * np.exp(-n / (0.16 * sr))
        kick_hz = 42.0 + (self._kick_hz - 42.0) * np.exp(-n / (0.05 * sr))
        kick_phase = self._kick_phase + np.cumsum(kick_hz / sr)
        kick = kick_env * np.sin(2.0 * np.pi * kick_phase)
        self._kick_env = float(kick_env[-1])
        self._kick_hz = float(kick_hz[-1])
        self._kick_phase = float(kick_phase[-1] % 1.0)

        hat_env = self._hat_env * np.exp(-n / (0.035 * sr))
        hat = hat_env * self._rng.uniform(-1.0, 1.0, frames)
        self._hat_env = float(hat_env[-1])

        snare_env = self._snare_env * np.exp(-n / (0.09 * sr))
        snare_phase = self._snare_phase + np.cumsum(np.full(frames, 190.0 / sr))
        snare = snare_env * (
            0.35 * np.sin(2.0 * np.pi * snare_phase) + 0.65 * self._rng.uniform(-1.0, 1.0, frames)
        )
        self._snare_env = float(snare_env[-1])
        self._snare_phase = float(snare_phase[-1] % 1.0)

        return kick + 0.45 * hat + 0.7 * snare


def _oscillator(phase: np.ndarray, waveform: str) -> np.ndarray:
    wrapped = phase % 1.0
    sine = np.sin(2.0 * np.pi * wrapped)
    if waveform == "triangle":
        tri = 2.0 * np.abs(2.0 * wrapped - 1.0) - 1.0
        return 0.25 * sine + 0.75 * tri
    if waveform == "saw":
        return 2.0 * wrapped - 1.0
    if waveform == "square":
        return np.where(wrapped < 0.5, 1.0, -1.0)
    if waveform == "pulse":
        return np.where(wrapped < 0.22, 1.0, -1.0)
    if waveform == "organ":
        return (
            0.38 * sine
            + 0.26 * np.sin(4.0 * np.pi * wrapped)
            + 0.2 * np.sin(6.0 * np.pi * wrapped)
            + 0.16 * np.sin(8.0 * np.pi * wrapped)
        )
    if waveform == "bass":
        saw = 2.0 * wrapped - 1.0
        return np.tanh(1.6 * (0.45 * sine + 0.55 * saw))
    return sine
