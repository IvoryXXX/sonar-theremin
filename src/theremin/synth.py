from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

from theremin.sampler import SampleBank
from theremin.types import Voice


@dataclass
class _SampleVoice:
    buf: np.ndarray
    pos: int = 0
    gain: float = 0.0
    peak: float = 0.5
    release_s: float = 0.1


MAX_SAMPLE_VOICES = 20

# Named like keyboard Tone/Voice: same notes, different colour.
VOICES = {
    "Pistala": {
        "wave": "sine",
        "wave2": "sine",
        "detune": 1200.0,
        "mix2": 0.14,
        "sub": 0.0,
        "noise": 0.08,
        "cutoff": 0.48,
        "drive": 1.0,
        "crush": 0.0,
        "portamento_s": 0.035,
        "attack_s": 0.055,
        "release_s": 0.18,
        "gain": 1.2,
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
    # Clean continuous-pitch voice for rezim Nastroj (theremin)
    "Theremin": {
        "wave": "sine",
        "wave2": "sine",
        "detune": 7.0,
        "mix2": 0.12,
        "sub": 0.06,
        "noise": 0.0,
        "cutoff": 0.92,
        "drive": 1.04,
        "crush": 0.0,
        "portamento_s": 0.045,
        "attack_s": 0.02,
        "release_s": 0.22,
        "gain": 1.05,
        "pluck": False,
    },
    # Karplus-Strong guitar string — normal sustained voice (pluck via envelope in apply_voice)
    "Kytara": {
        "wave": "saw",
        "wave2": "triangle",
        "detune": 2.0,
        "mix2": 0.2,
        "sub": 0.08,
        "noise": 0.0,
        "cutoff": 0.72,
        "drive": 1.15,
        "crush": 0.0,
        "portamento_s": 0.001,
        "attack_s": 0.003,
        "release_s": 0.9,
        "gain": 1.35,
    },
}

VOICE_NAMES = tuple(VOICES.keys())


class Synth:
    def __init__(self, sample_rate: int = 48000, master: float = 0.4) -> None:
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
        self.guitar_active = False  # set by app when Kytara mode button is on
        self.flute_active = False  # breath + fingering
        self.sampler_active = False
        self._sample_bank = SampleBank(sample_rate=sample_rate)
        self._spl_voices: list[_SampleVoice] = []
        self._sampler_master = 1.0
        self._sampler_master_target = 1.0
        self._spl_attack_s = 0.002
        self._dj_filter = 0.75
        self._dj_filter_target = 0.75
        self._dj_lp = 0.0
        self._dj_echo = 0.0
        self._dj_echo_target = 0.0
        self._dj_drive = 0.0
        self._dj_drive_target = 0.0
        self._dj_delay: np.ndarray | None = None
        self._dj_delay_pos = 0
        self._dj_delay_len = 0
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
        self._xruns = 0
        self._callback_errors = 0
        self._last_watchdog = 0.0
        self._ring_until = 0.0  # monotonic: force audible amp until this time
        self._guitar_open = False  # note is ringing; left hand modulates length live
        self.apply_timbre("Pistala")

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            self.error = "sounddevice is not installed"
            return
        self.stop()
        try:
            # Bigger blocks + higher latency = fewer dropouts on Windows under UI load.
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=1024,
                latency=0.08,
                callback=self._callback,
            )
            self._stream.start()
            self.error = None
            n = int(self.sample_rate * 0.55)
            self._dj_delay = np.zeros(max(n, 2048), dtype=np.float64)
            self._dj_delay_len = self._dj_delay.shape[0]
            self._dj_delay_pos = 0
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self._stream = None

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.abort()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def is_alive(self) -> bool:
        stream = self._stream
        try:
            return stream is not None and bool(getattr(stream, "active", False))
        except Exception:
            return False

    def ensure_running(self, now: float | None = None) -> bool:
        """Restart the PortAudio stream if it died (common after long play / device sleep)."""
        t = time.monotonic() if now is None else now
        if t - self._last_watchdog < 0.5:
            return self.is_alive()
        self._last_watchdog = t
        if self.is_alive():
            return True
        self.start()
        return self.is_alive()

    def apply_voice(self, voice: Voice) -> None:
        if voice.frequency_hz > 0.0:
            self._target_freq = max(40.0, float(voice.frequency_hz))
        if self.muted:
            self._target_amp = 0.0
            self._guitar_open = False
            self._stop_sample()
            return

        now = time.monotonic()

        if self.sampler_active:
            self._apply_sampler_voice(voice, now)
            return

        if self.guitar_active:
            # Left hand = LENGTH, live while the note rings.
            # Closer = shorten / mute; farther = hold / long decay.
            raw = float(voice.amplitude) if voice.amplitude > 0.0 else 0.55
            raw = float(np.clip(raw, 0.0, 1.0))
            # volume_amplitude is "closer = louder"; invert so closer = shorter.
            sustain = 1.0 - raw
            sustain_s = 0.06 + 5.0 * sustain  # ~0.06s … ~5.0s
            peak = max(float(self.master), 0.3) * 0.9
            self.attack_s = 0.003
            self.portamento_s = 0.001
            self.release_s = sustain_s

            if voice.retrigger and voice.frequency_hz > 0.0:
                self._freq = self._target_freq
                self._amp = 0.0
                self._target_amp = peak
                self._guitar_open = True
                self._ring_until = now + 0.05  # short attack, then left hand takes over
                return

            if voice.gate and voice.frequency_hz > 0.0:
                self._guitar_open = True
                # Far left = hold; bring left closer = shorten toward mute.
                if sustain >= 0.88:
                    self._target_amp = peak
                else:
                    self._target_amp = 0.0
                return

            if self._guitar_open:
                # Real-time: close = kill fast, far = keep sounding.
                if now < self._ring_until:
                    self._target_amp = peak
                elif sustain >= 0.88:
                    self._target_amp = max(float(self._amp), peak * 0.55)
                    self.release_s = 0.35
                else:
                    self._target_amp = 0.0
                if self._amp < 0.008 and now >= self._ring_until:
                    self._guitar_open = False
                return

            self._target_amp = 0.0
            return

        if self.flute_active:
            # Soft breath: amplitude = blow strength; soft attack already in patch.
            self.attack_s = 0.05
            self.release_s = 0.16
            self.portamento_s = 0.03
            if voice.retrigger:
                self._amp *= 0.35  # light tongue
            self._target_amp = voice.amplitude * self.master if voice.gate else 0.0
            # A little extra air noise while blowing
            self.noise = 0.06 + 0.1 * float(np.clip(voice.amplitude, 0.0, 1.0))
            return

        # Forced ring (test pluck) for non-guitar paths.
        if now < self._ring_until:
            self._target_amp = max(float(self.master), 0.3) * 0.9
            return

        if voice.retrigger:
            self._amp *= 0.12
        self._target_amp = voice.amplitude * self.master if voice.gate else 0.0

    def _apply_sampler_voice(self, voice: Voice, now: float) -> None:
        self._sampler_master_target = 1.0

    def set_dj_mix(
        self,
        filter_cut: float,
        echo: float,
        drive: float = 0.0,
        delay_s: float | None = None,
    ) -> None:
        self._dj_filter_target = float(np.clip(filter_cut, 0.05, 0.99))
        self._dj_echo_target = float(np.clip(echo, 0.0, 0.85))
        self._dj_drive_target = float(np.clip(drive, 0.0, 0.55))
        if delay_s is not None and self._dj_delay is not None:
            want = int(np.clip(delay_s * self.sample_rate, 512, self.sample_rate * 0.75))
            if abs(want - self._dj_delay_len) > 64:
                self._dj_delay = np.zeros(want, dtype=np.float64)
                self._dj_delay_len = want
                self._dj_delay_pos = 0

    def _process_dj_fx(self, sample: float) -> float:
        cf = self._dj_filter + (self._dj_filter_target - self._dj_filter) * 0.14
        self._dj_filter = cf
        self._dj_echo += (self._dj_echo_target - self._dj_echo) * 0.1
        self._dj_drive += (self._dj_drive_target - self._dj_drive) * 0.08
        x = sample
        if self._dj_drive > 0.01:
            x = math.tanh(x * (1.0 + self._dj_drive * 2.8))
        hz = 70.0 * (18000.0 / 70.0) ** cf
        alpha = 1.0 - math.exp(-2.0 * math.pi * hz / self.sample_rate)
        self._dj_lp += alpha * (x - self._dj_lp)
        out = self._dj_lp
        if self._dj_delay is not None and self._dj_echo > 0.01:
            delayed = float(self._dj_delay[self._dj_delay_pos])
            wet = delayed * self._dj_echo
            out = out * (1.0 - self._dj_echo * 0.35) + wet
            self._dj_delay[self._dj_delay_pos] = out * 0.58
            self._dj_delay_pos = (self._dj_delay_pos + 1) % self._dj_delay.shape[0]
        return out

    def _stop_sample(self) -> None:
        self._spl_voices.clear()

    def trigger_sample(self, index: int, peak: float | None = None) -> None:
        if self.muted:
            return
        buf = self._sample_bank.get(int(index))
        if buf.size < 2:
            return
        p = float(np.clip(peak if peak is not None else max(float(self.master), 0.4), 0.06, 1.0))
        rel = 0.05 if int(index) == 2 else 0.11
        self._spl_voices.append(_SampleVoice(buf=buf, peak=p, release_s=rel))
        while len(self._spl_voices) > MAX_SAMPLE_VOICES:
            self._spl_voices.pop(0)

    def _render_sample(self, frames: int, sr: float) -> np.ndarray:
        if not self._spl_voices:
            self._sampler_master += (self._sampler_master_target - self._sampler_master) * 0.08
            return np.zeros(frames, dtype=np.float64)
        out = np.zeros(frames, dtype=np.float64)
        ka_m = 1.0 - math.exp(-1.0 / max(0.025 * sr, 1.0))
        ka_up = 1.0 - math.exp(-1.0 / max(self._spl_attack_s * sr, 1.0))
        master = self._sampler_master
        target_m = self._sampler_master_target
        for fi in range(frames):
            master += (target_m - master) * ka_m
            mix = 0.0
            for v in self._spl_voices:
                if v.pos >= v.buf.shape[0]:
                    continue
                v.gain += (v.peak - v.gain) * ka_up
                mix += float(v.buf[v.pos]) * v.gain
                v.pos += 1
            out[fi] = self._process_dj_fx(math.tanh(mix * master * 1.35))
        self._sampler_master = master
        self._spl_voices = [v for v in self._spl_voices if v.pos < v.buf.shape[0]]
        return out

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
        self._ring_until = 0.0
        self._guitar_open = False
        self._stop_sample()

    def pluck_test(self, freq: float = 220.0) -> None:
        """Must be audible when enabling Kytara mode."""
        self.apply_timbre("Kytara")
        self.guitar_active = True
        self._target_freq = float(freq)
        self._freq = float(freq)
        self._amp = 0.0
        self._target_amp = max(float(self.master), 0.3) * 0.9
        self._guitar_open = True
        self._ring_until = time.monotonic() + 0.08
        self.release_s = 1.2
        self.attack_s = 0.003

    def trigger_kick(self) -> None:
        self._kick_trig = True

    def trigger_hat(self) -> None:
        self._hat_trig = True

    def trigger_snare(self) -> None:
        self._snare_trig = True

    def _callback(self, outdata, frames, _time_info, status) -> None:  # noqa: ANN001
        try:
            if status:
                self._xruns += 1
            sr = float(self.sample_rate)
            n = np.arange(frames, dtype=np.float64)
            target_freq = float(np.nan_to_num(self._target_freq, nan=220.0))
            target_amp = float(np.nan_to_num(self._target_amp, nan=0.0))
            cur_freq = float(np.nan_to_num(self._freq, nan=220.0))
            cur_amp = float(np.nan_to_num(self._amp, nan=0.0))
            if target_freq < 40.0:
                target_freq = 220.0
            if cur_freq < 40.0:
                cur_freq = target_freq
            kf = 1.0 - math.exp(-1.0 / max(self.portamento_s * sr, 1.0))
            if target_amp >= cur_amp:
                ka = 1.0 - math.exp(-1.0 / max(self.attack_s * sr, 1.0))
            else:
                ka = 1.0 - math.exp(-1.0 / max(self.release_s * sr, 1.0))
            freq = target_freq + (cur_freq - target_freq) * (1.0 - kf) ** n
            amp = target_amp + (cur_amp - target_amp) * (1.0 - ka) ** n
            phase = self._phase + np.cumsum(freq / sr)
            osc = _oscillator(phase, self.waveform)
            if self.mix2 > 0.0:
                ratio = 2.0 ** (self.detune / 1200.0)
                phase2 = self._phase2 + np.cumsum(freq * ratio / sr)
                osc = (1.0 - self.mix2) * osc + self.mix2 * _oscillator(phase2, self.waveform2)
                self._phase2 = float(phase2[-1] % 1.0)
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
            if bright < 0.82:
                osc = self._lowpass(osc, bright)
            else:
                self._lp = float(osc[-1])
            if self.sampler_active:
                lead = self._render_sample(frames, sr) * 1.35
            else:
                lead = amp * self.gain * osc
            drums = self._drums(n, sr, frames) * (0.0 if self.muted else self.drum_gain * self.master * 4.0)
            out = (lead + drums).astype(np.float32)
            np.nan_to_num(out, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            np.clip(out, -0.95, 0.95, out=out)
            outdata[:, 0] = out
            self._phase = float(phase[-1] % 1.0)
            self._freq = float(freq[-1])
            self._amp = float(amp[-1])
        except Exception:  # noqa: BLE001
            self._callback_errors += 1
            outdata.fill(0.0)

    def _lowpass(self, signal: np.ndarray, cutoff: float) -> np.ndarray:
        hz = 160.0 * (14000.0 / 160.0) ** float(cutoff)
        alpha = 1.0 - math.exp(-2.0 * math.pi * hz / self.sample_rate)
        out = np.empty(signal.shape[0], dtype=np.float64)
        state = float(self._lp)
        if not math.isfinite(state):
            state = 0.0
        aa = float(alpha)
        sig = signal
        for i in range(sig.shape[0]):
            state += aa * (float(sig[i]) - state)
            out[i] = state
        self._lp = state if math.isfinite(state) else 0.0
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
