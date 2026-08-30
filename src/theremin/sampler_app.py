"""Rhythm sampler — preset pattern + hand distance picks instrument on the beat."""

from __future__ import annotations

import time
from collections import deque

import dearpygui.dearpygui as dpg
import numpy as np

from theremin.pipeline import Pipeline, RuntimeConfig
from theremin.rhythm_engine import PATTERN_PRESETS, STEPS, RhythmEngine
from theremin.sampler import SAMPLE_SLOTS, display_name, sample_index_for_note
from theremin.sources import SerialSource, SimulatorSource, list_serial_ports
from theremin.synth import Synth
from theremin.types import SensorFrame, Voice

MM_LO = 40.0
MM_HI = 520.0
ANT_W, ANT_H = 168, 420
HISTORY = 180
DEFAULT_PORT = "COM7"
DEFAULT_BAUD = 115200

SAMPLE_COLORS = (
    (200, 90, 90),
    (210, 130, 70),
    (220, 190, 60),
    (90, 190, 120),
    (70, 150, 210),
    (140, 100, 200),
)

# Stable hit level — per-channel fader controls loudness (DJ mixer).
STABLE_HIT = 0.58


class SamplerApp:
    def __init__(self) -> None:
        self.cfg = RuntimeConfig()
        self.cfg.sampler_mode = False
        self.cfg.guitar_mode = False
        self.cfg.snap_pick = False
        self.cfg.retrigger = False
        self.cfg.volume_enabled = True
        self.cfg.continuous_pitch = False
        self.cfg.default_amp = 0.65
        self.pipeline = Pipeline(self.cfg)
        self.pipeline.apply_sampler()
        self.rhythm = RhythmEngine(bpm=100.0)
        self.simulator = SimulatorSource()
        self.serial = SerialSource()
        self.source = self.simulator
        self.synth = Synth()
        self.synth.sampler_active = True
        self._t0 = time.perf_counter()
        self._hist_t: deque[float] = deque(maxlen=HISTORY)
        self._hist_pr: deque[float] = deque(maxlen=HISTORY)
        self._hist_pf: deque[float] = deque(maxlen=HISTORY)
        self._hist_vr: deque[float] = deque(maxlen=HISTORY)
        self._hist_vf: deque[float] = deque(maxlen=HISTORY)
        self._last_voice: Voice | None = None
        self._selected_slot: int = 0
        self._channel_vol: list[float] = [1.0] * len(SAMPLE_SLOTS)
        self._last_fired_slot: int | None = None
        self._status = "Rytmus bezi — dej ruku do zony"
        self._ui = {
            "master_vol": 0.55,
            "sim_pitch": 130.0,
            "sim_volume": 420.0,
            "pitch_min": 80.0,
            "pitch_max": 500.0,
            "volume_min": 60.0,
            "volume_max": 500.0,
            "ema_alpha": 0.38,
            "hold_missing": 6,
            "sim_jitter": 3.0,
            "sim_dropout": 0.02,
            "sim_rate": 40.0,
            "baud": DEFAULT_BAUD,
            "filter_mode": "median_ema",
        }

    def run(self) -> None:
        self.simulator.start()
        self.synth.start()
        if self.synth.error:
            self._status = f"Audio: {self.synth.error}"

        dpg.create_context()
        self._theme()
        self._build()
        dpg.create_viewport(title="Sonar Sampler — Rytmus", width=1180, height=880)
        dpg.setup_dearpygui()
        dpg.bind_theme("sampler_theme")
        dpg.set_primary_window("sampler_root", True)
        dpg.show_viewport()
        self._set_source("Simulator")
        self.rhythm.reset(time.perf_counter())
        self._draw_zones(None)
        self._sync_pattern_ui()

        try:
            while dpg.is_dearpygui_running():
                self._tick()
                dpg.render_dearpygui_frame()
        finally:
            self.source.stop()
            self.serial.stop()
            self.synth.stop()
            dpg.destroy_context()

    def _theme(self) -> None:
        with dpg.theme(tag="sampler_theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (16, 18, 22, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (220, 225, 230, 255))
        with dpg.theme(tag="btn_on"):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (50, 120, 90, 255))
        with dpg.theme(tag="step_on"):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (90, 180, 120, 255))
        with dpg.theme(tag="step_off"):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (45, 50, 58, 255))
        with dpg.theme(tag="step_now"):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (255, 200, 80, 255))

    def _build(self) -> None:
        with dpg.window(tag="sampler_root", no_close=True):
            with dpg.group(horizontal=True):
                dpg.add_text("SONAR SAMPLER", color=(120, 220, 180, 255))
                dpg.add_spacer(width=12)
                dpg.add_radio_button(
                    ("Simulator", "Serial"),
                    horizontal=True,
                    tag="source_radio",
                    callback=lambda _s, a: self._set_source(a),
                )
                dpg.add_combo(items=list_serial_ports(), tag="port_combo", width=100)
                dpg.add_button(label="Connect", callback=lambda: self._set_source("Serial"))
                dpg.add_spacer(width=12)
                dpg.add_checkbox(label="Rytmus", tag="rhythm_run", default_value=True)
                dpg.add_checkbox(label="MUTE", tag="mute", default_value=False)
                dpg.add_button(label="Ticho", user_data=0.15, callback=self._set_master)
                dpg.add_button(label="Normal", user_data=0.45, callback=self._set_master)
                dpg.add_button(label="Nahlas", user_data=0.7, callback=self._set_master)

            dpg.add_spacer(height=4)
            dpg.add_text(
                "DJ mix — prava = kanal, leva = fader",
                tag="hero_banner",
                color=(255, 210, 100, 255),
            )
            dpg.add_text("", tag="status", color=(150, 160, 170, 255))

            with dpg.group(horizontal=True):
                self._build_side_panel()
                self._build_strip("FADER — leva ruka", "vol_draw", (90, 200, 160))
                self._build_strip("KANAL — prava ruka", "pitch_draw", (255, 200, 90))
                self._build_now()

            with dpg.collapsing_header(label="Rytmus — nastaveni", default_open=True):
                dpg.add_text("Tempo (BPM)")
                with dpg.group(horizontal=True):
                    for bpm in (70, 90, 100, 120, 140):
                        dpg.add_button(
                            label=str(bpm),
                            tag=f"btn_bpm_{bpm}",
                            user_data=bpm,
                            callback=self._set_bpm,
                        )
                dpg.add_spacer(height=4)
                dpg.add_text("Preset vzoru")
                with dpg.group(horizontal=True):
                    for name in PATTERN_PRESETS:
                        dpg.add_button(
                            label=name,
                            tag=f"btn_preset_{name}",
                            user_data=name,
                            callback=self._load_preset,
                        )
                dpg.add_spacer(height=6)
                dpg.add_text("Krok 1-16 (klik = zap/vyp pro kazdy zvuk)", color=(160, 170, 180, 255))
                for slot, (_sid, label) in enumerate(SAMPLE_SLOTS):
                    col = SAMPLE_COLORS[slot % len(SAMPLE_COLORS)]
                    with dpg.group(horizontal=True):
                        dpg.add_text(f"{label:6}", color=(*col, 255))
                        for step in range(STEPS):
                            dpg.add_button(
                                label=str(step + 1),
                                tag=f"pat_{slot}_{step}",
                                user_data=(slot, step),
                                callback=self._toggle_pat,
                                width=26,
                                height=22,
                            )

            with dpg.collapsing_header(label="Grafy", default_open=False):
                with dpg.plot(label="Vyska", height=140, width=480):
                    dpg.add_plot_legend()
                    dpg.add_plot_axis(dpg.mvXAxis, label="s", tag="px")
                    with dpg.plot_axis(dpg.mvYAxis, label="mm", tag="py"):
                        dpg.add_line_series([], [], label="raw", tag="s_pr")
                        dpg.add_line_series([], [], label="filt", tag="s_pf")
                    dpg.set_axis_limits("py", 0, 520)

    def _build_side_panel(self) -> None:
        with dpg.child_window(width=250, height=ANT_H + 40, border=True):
            dpg.add_text("Jak to funguje", color=(255, 200, 120, 255))
            dpg.add_text(
                "Jako DJ pult:\n"
                "1. Groove bezi sam (rytmus).\n"
                "2. PRAVA = vyber kanalu\n"
                "   (Kick, Snare, Hat…).\n"
                "3. LEVA = fader toho kanalu\n"
                "   dal = nahlas, bliz = vyp.\n\n"
                "Ostatni kanaly jedou dal,\n"
                "ty jen stlmis vybranou cast.",
                color=(160, 170, 180, 255),
            )
            dpg.add_spacer(height=8)
            dpg.add_text("Zony:", color=(180, 190, 200, 255))
            for i, (_sid, label) in enumerate(SAMPLE_SLOTS):
                col = SAMPLE_COLORS[i % len(SAMPLE_COLORS)]
                dpg.add_text(f"  {label}", color=(*col, 255))
            dpg.add_spacer(height=8)
            dpg.add_text("Sim — prava")
            with dpg.group(horizontal=True):
                for mm in (120, 220, 320, 420):
                    dpg.add_button(label=str(mm), user_data=mm, callback=lambda _s, a, u: self._set_sim_pitch(u))
            dpg.add_text("Sim — leva")
            with dpg.group(horizontal=True):
                for mm in (80, 200, 350, 480):
                    dpg.add_button(label=str(mm), user_data=mm, callback=lambda _s, a, u: self._set_sim_vol(u))

    def _build_strip(self, title: str, tag: str, color: tuple[int, int, int]) -> None:
        with dpg.child_window(width=ANT_W + 20, height=ANT_H + 40, border=True):
            dpg.add_text(title, color=(*color, 255))
            dpg.add_drawlist(width=ANT_W, height=ANT_H, tag=tag)

    def _build_now(self) -> None:
        with dpg.child_window(width=200, height=ANT_H + 40, border=True):
            dpg.add_text("Takt", color=(200, 210, 220, 255))
            dpg.add_text("-", tag="now_step", color=(255, 220, 120, 255))
            dpg.add_spacer(height=8)
            dpg.add_text("Kanal", color=(200, 210, 220, 255))
            dpg.add_text("-", tag="now_slot", color=(255, 220, 120, 255))
            dpg.add_spacer(height=8)
            dpg.add_text("Fader", color=(200, 210, 220, 255))
            dpg.add_text("-", tag="now_fader", color=(180, 230, 200, 255))
            dpg.add_spacer(height=8)
            dpg.add_text("Bezi", color=(200, 210, 220, 255))
            dpg.add_text("-", tag="now_hit", color=(180, 230, 200, 255))
            dpg.add_spacer(height=8)
            dpg.add_text("-", tag="read_pitch")
            dpg.add_text("-", tag="read_vol")

    def _auto_serial(self) -> None:
        ports = list_serial_ports()
        dpg.configure_item("port_combo", items=ports)
        port = DEFAULT_PORT if DEFAULT_PORT in ports else (ports[0] if ports else "")
        if port:
            dpg.set_value("port_combo", port)
        dpg.set_value("source_radio", "Serial")
        self._ui["baud"] = DEFAULT_BAUD
        self._set_source("Serial")

    def _set_source(self, name: str) -> None:
        self.source.stop()
        if name == "Serial":
            port = dpg.get_value("port_combo") if dpg.does_item_exist("port_combo") else ""
            self.serial.port = str(port)
            self.serial.baud = int(self._ui["baud"])
            self.serial.start()
            self.source = self.serial
            self._status = f"Serial {port}"
        else:
            self.simulator.start()
            self.source = self.simulator
            self._status = "Simulator"
        if dpg.does_item_exist("status"):
            dpg.set_value("status", self._status)

    def _set_master(self, _s=None, _a=None, user_data=0.45) -> None:
        self._ui["master_vol"] = float(user_data)
        self.synth.master = float(user_data)
        self._sync_bpm_lights()

    def _set_bpm(self, _s, _a, bpm: int) -> None:
        self.rhythm.set_bpm(float(bpm))
        self._sync_bpm_lights()

    def _load_preset(self, _s, _a, name: str) -> None:
        self.rhythm.load_preset(name)
        self._sync_pattern_ui()
        self._sync_preset_lights()

    def _toggle_pat(self, _s, _a, user_data: tuple[int, int]) -> None:
        slot, step = user_data
        self.rhythm.toggle_step(slot, step)
        self._sync_pattern_ui()

    def _set_sim_pitch(self, mm: float) -> None:
        self._ui["sim_pitch"] = float(mm)
        self.simulator.pitch_target_mm = float(mm)

    def _set_sim_vol(self, mm: float) -> None:
        self._ui["sim_volume"] = float(mm)
        self.simulator.volume_target_mm = float(mm)

    def _read_controls(self) -> None:
        if not dpg.does_item_exist("mute"):
            return
        self.cfg.muted = bool(dpg.get_value("mute"))
        self.cfg.snap_pick = False
        self.cfg.sampler_mode = False
        self.cfg.guitar_mode = False
        self.cfg.retrigger = False
        self.cfg.volume_enabled = True
        self.cfg.pitch_min_mm = float(self._ui["pitch_min"])
        self.cfg.pitch_max_mm = float(self._ui["pitch_max"])
        self.cfg.volume_min_mm = float(self._ui["volume_min"])
        self.cfg.volume_max_mm = float(self._ui["volume_max"])
        self.cfg.ema_alpha = float(self._ui["ema_alpha"])
        self.cfg.hold_missing = int(self._ui["hold_missing"])
        self.cfg.filter_mode = str(self._ui["filter_mode"])
        self.pipeline.sync_filters()
        self.synth.master = float(self._ui["master_vol"])
        self.synth.muted = self.cfg.muted
        self.synth.sampler_active = True
        self.rhythm.running = bool(dpg.get_value("rhythm_run"))
        self.simulator.jitter_mm = float(self._ui["sim_jitter"])
        self.simulator.dropout = float(self._ui["sim_dropout"])
        self.simulator.rate_hz = float(self._ui["sim_rate"])
        self.simulator.pitch_target_mm = float(self._ui["sim_pitch"])
        self.simulator.volume_target_mm = float(self._ui["sim_volume"])

    def _fader_target(self, voice: Voice | None) -> float:
        """0 = mute, 1 = full — far left hand = up (DJ fader)."""
        if voice is None or voice.volume_mm is None:
            return self._channel_vol[self._selected_slot]
        lo = self.cfg.volume_min_mm
        hi = self.cfg.volume_max_mm
        span = max(hi - lo, 1.0)
        t = (float(voice.volume_mm) - lo) / span
        t = float(np.clip(t, 0.0, 1.0))
        if self.cfg.invert_volume:
            t = 1.0 - t
        return t

    def _update_channel_fader(self, voice: Voice | None) -> None:
        target = self._fader_target(voice)
        slot = self._selected_slot
        cur = self._channel_vol[slot]
        self._channel_vol[slot] = cur + (target - cur) * 0.22
        if dpg.does_item_exist("now_fader"):
            pct = int(self._channel_vol[slot] * 100)
            dpg.set_value("now_fader", f"fader {pct}%")

    def _stable_peak(self) -> float:
        return STABLE_HIT * max(float(self.synth.master), 0.35)

    def _mute_voice(self, voice: Voice | None) -> Voice:
        amp = voice.amplitude if voice else 0.35
        return Voice(
            0.0,
            amp,
            None,
            False,
            False,
            voice.pitch_raw_mm if voice else None,
            voice.pitch_mm if voice else None,
            voice.volume_raw_mm if voice else None,
            voice.volume_mm if voice else None,
            None,
            False,
            voice.in_pitch_range if voice else False,
            voice.in_volume_range if voice else False,
            None,
        )

    def _tick(self) -> None:
        self._read_controls()
        now = time.perf_counter()
        self.synth.ensure_running(now)

        voice: Voice | None = None
        frame = self.source.poll()
        if frame is not None:
            voice = self.pipeline.push(frame)
            self._last_voice = voice
            self._push_history(frame, voice)

        if voice is None:
            voice = self._last_voice

        if voice and voice.in_pitch_range and voice.note_name:
            idx = sample_index_for_note(voice.note_name)
            if idx is not None:
                self._selected_slot = idx

        self._update_channel_fader(voice)

        peak = self._stable_peak()
        hits: list[str] = []
        fired, _steps = self.rhythm.update(now)
        for idx in fired:
            level = self._channel_vol[idx]
            vol = peak * level
            if vol >= 0.04:
                self.synth.trigger_sample(idx, vol)
                if level >= 0.5:
                    hits.append(SAMPLE_SLOTS[idx][1])
        if hits and dpg.does_item_exist("now_hit"):
            dpg.set_value("now_hit", " ".join(hits[:4]))

        self.synth.apply_voice(self._mute_voice(voice))

        self._update_banner()
        self._update_readouts(voice)
        self._draw_zones(voice)
        self._update_plots()
        self._sync_pattern_ui()

    def _update_banner(self) -> None:
        if not dpg.does_item_exist("hero_banner"):
            return
        step = self.rhythm.current_step + 1
        if dpg.does_item_exist("now_step"):
            dpg.set_value("now_step", f"{step} / {STEPS}")
        label = SAMPLE_SLOTS[self._selected_slot][1]
        if dpg.does_item_exist("now_slot"):
            dpg.set_value("now_slot", label)
        dpg.set_value(
            "hero_banner",
            f"Kanal {label}  —  takt {step}  ({self.rhythm.bpm:.0f} BPM)",
        )

    def _update_readouts(self, voice: Voice | None) -> None:
        dpg.set_value("read_pitch", _mm(voice.pitch_mm if voice else None))
        dpg.set_value("read_vol", _mm(voice.volume_mm if voice else None))

    def _push_history(self, _frame: SensorFrame, voice: Voice) -> None:
        t = time.perf_counter() - self._t0
        self._hist_t.append(t)
        self._hist_pr.append(voice.pitch_raw_mm if voice.pitch_raw_mm is not None else float("nan"))
        self._hist_pf.append(voice.pitch_mm if voice.pitch_mm is not None else float("nan"))
        self._hist_vr.append(voice.volume_raw_mm if voice.volume_raw_mm is not None else float("nan"))
        self._hist_vf.append(voice.volume_mm if voice.volume_mm is not None else float("nan"))

    def _update_plots(self) -> None:
        if not dpg.does_item_exist("s_pr"):
            return
        xs = list(self._hist_t)
        dpg.set_value("s_pr", [xs, list(self._hist_pr)])
        dpg.set_value("s_pf", [xs, list(self._hist_pf)])
        if xs:
            dpg.set_axis_limits("px", max(0, xs[-1] - 8), xs[-1] + 0.2)

    def _sync_bpm_lights(self) -> None:
        bpm = int(round(self.rhythm.bpm))
        for v in (70, 90, 100, 120, 140):
            tag = f"btn_bpm_{v}"
            if dpg.does_item_exist(tag):
                dpg.bind_item_theme(tag, "btn_on" if bpm == v else "btn_off")

    def _sync_preset_lights(self) -> None:
        name = self.rhythm.preset_name
        for preset in PATTERN_PRESETS:
            tag = f"btn_preset_{preset}"
            if dpg.does_item_exist(tag):
                dpg.bind_item_theme(tag, "btn_on" if preset == name else "btn_off")

    def _sync_pattern_ui(self) -> None:
        cur = self.rhythm.current_step
        for slot in range(len(SAMPLE_SLOTS)):
            for step in range(STEPS):
                tag = f"pat_{slot}_{step}"
                if not dpg.does_item_exist(tag):
                    continue
                on = bool(self.rhythm.patterns[slot][step])
                if step == cur and self.rhythm.running:
                    dpg.bind_item_theme(tag, "step_now")
                elif on:
                    dpg.bind_item_theme(tag, "step_on")
                else:
                    dpg.bind_item_theme(tag, "step_off")
        self._sync_bpm_lights()
        self._sync_preset_lights()

    def _draw_zones(self, voice: Voice | None) -> None:
        if voice is None:
            voice = Voice(0, 0, None, False, False, None, None, None, None, None, False, False, False, None)
        self._draw_vol(voice)
        self._draw_pitch(voice)

    def _draw_vol(self, voice: Voice) -> None:
        parent = "vol_draw"
        if not dpg.does_item_exist(parent):
            return
        dpg.delete_item(parent, children_only=True)
        dpg.draw_rectangle((0, 0), (ANT_W, ANT_H), fill=(14, 18, 22, 255), parent=parent)
        dpg.draw_text((40, 6), "50 cm — nahlas", size=12, color=(160, 190, 175, 255), parent=parent)
        dpg.draw_text((40, ANT_H - 20), "0 cm — vyp", size=12, color=(160, 190, 175, 255), parent=parent)
        slot = self._selected_slot
        fv = self._channel_vol[slot]
        lo, hi = self.cfg.volume_min_mm, self.cfg.volume_max_mm
        mm_fader = lo + (1.0 - fv) * (hi - lo)
        yf = _mm_to_y(mm_fader)
        dpg.draw_line((36, yf), (ANT_W - 14, yf), color=(255, 220, 100, 220), thickness=3, parent=parent)
        dpg.draw_text(
            (40, ANT_H // 2 - 8),
            f"{SAMPLE_SLOTS[slot][1]} {int(fv * 100)}%",
            size=13,
            color=(255, 230, 160, 255),
            parent=parent,
        )
        self._marker(parent, voice.volume_raw_mm, (120, 160, 150, 140), 5)
        self._marker(parent, voice.volume_mm, (100, 255, 180, 255), 9)

    def _draw_pitch(self, voice: Voice) -> None:
        parent = "pitch_draw"
        if not dpg.does_item_exist(parent):
            return
        dpg.delete_item(parent, children_only=True)
        dpg.draw_rectangle((0, 0), (ANT_W, ANT_H), fill=(14, 16, 20, 255), parent=parent)
        notes = self.pipeline.mapper.notes
        n = len(notes)
        lo, hi = self.cfg.pitch_min_mm, self.cfg.pitch_max_mm
        cur = self.rhythm.current_step
        for i, (name, _) in enumerate(notes):
            col = SAMPLE_COLORS[i % len(SAMPLE_COLORS)]
            a, b = i / n, (i + 1) / n
            d0 = lo + a * (hi - lo)
            d1 = lo + b * (hi - lo)
            ya, yb = _mm_to_y(d1), _mm_to_y(d0)
            active = self._selected_slot == i
            pat_hit = bool(self.rhythm.patterns[i][cur]) if self.rhythm.running else False
            ch = self._channel_vol[i]
            if active:
                fill = (*col, 255)
            elif pat_hit and ch > 0.5:
                fill = (*col, 190)
            elif ch > 0.05:
                fill = (*col, int(80 + 100 * ch))
            else:
                fill = (*col, 40)
            dpg.draw_rectangle((28, ya), (ANT_W - 12, yb), fill=fill, parent=parent)
            label = display_name(name)
            dpg.draw_text((36, (ya + yb) / 2 - 8), label, size=14, color=(250, 250, 245, 255), parent=parent)
        self._marker(parent, voice.pitch_raw_mm, (255, 200, 90, 140), 5)
        self._marker(parent, voice.pitch_mm, (255, 220, 100, 255), 9)

    def _marker(self, parent: str, mm: float | None, color: tuple, radius: int) -> None:
        if mm is None:
            return
        y = _mm_to_y(mm)
        dpg.draw_circle((ANT_W - 24, y), radius, fill=color, color=(10, 10, 10, 180), parent=parent)


def _mm(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f} mm"


def _mm_to_y(mm: float) -> float:
    t = (mm - MM_LO) / (MM_HI - MM_LO)
    t = min(1.0, max(0.0, t))
    pad = 28.0
    return pad + (1.0 - t) * (ANT_H - 2 * pad)


def main() -> None:
    SamplerApp().run()
