"""Air DJ — beat-quantized virtual mixer (two sonars)."""

from __future__ import annotations

import time

import dearpygui.dearpygui as dpg
import numpy as np

from theremin.air_dj import (
    CLOSE_MM,
    FAR_MM,
    FxKind,
    MM_HI,
    MM_LO,
    AirDjController,
)
from theremin.pipeline import Pipeline, RuntimeConfig
from theremin.rhythm_engine import PATTERN_PRESETS, STEPS, RhythmEngine
from theremin.sampler import SAMPLE_SLOTS
from theremin.sources import SerialSource, SimulatorSource, list_serial_ports
from theremin.synth import Synth
from theremin.types import Voice

ANT_W, ANT_H = 168, 420
DEFAULT_PORT = "COM7"
DEFAULT_BAUD = 115200
STABLE_HIT = 0.72


class DjApp:
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
        self.rhythm = RhythmEngine(bpm=104.0)
        self.rhythm.load_preset("Groove")
        self.dj = AirDjController()
        self.simulator = SimulatorSource()
        self.serial = SerialSource()
        self.source = self.simulator
        self.synth = Synth()
        self.synth.sampler_active = True
        self._last_voice: Voice | None = None
        self._last_beat_step = -1
        self._beat_flash = 0.0
        self._status = "Air DJ — groove bezi"
        self._ui = {
            "master_vol": 0.62,
            "sim_pitch": 360.0,
            "sim_volume": 210.0,
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
        dpg.create_viewport(title="Sonar Air DJ", width=1100, height=820)
        dpg.setup_dearpygui()
        dpg.bind_theme("dj_theme")
        dpg.set_primary_window("dj_root", True)
        dpg.show_viewport()
        self._set_source("Simulator")
        self.rhythm.reset(time.perf_counter())

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
        with dpg.theme(tag="dj_theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (14, 16, 22, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (220, 225, 230, 255))
        with dpg.theme(tag="btn_on"):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (50, 120, 90, 255))

    def _build(self) -> None:
        with dpg.window(tag="dj_root", no_close=True):
            with dpg.group(horizontal=True):
                dpg.add_text("AIR DJ", color=(120, 220, 180, 255))
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
                "Leva = energie vrstev  |  Prava = filtr  |  okamzita odezva",
                tag="hero_banner",
                color=(255, 210, 100, 255),
            )
            dpg.add_text("", tag="fx_banner", color=(180, 230, 200, 255))
            dpg.add_text("", tag="status", color=(150, 160, 170, 255))

            with dpg.group(horizontal=True):
                self._build_help()
                self._build_strip("LEVA — bicí + basa", "vol_draw", (90, 200, 160))
                self._build_strip("PRAVA — low-pass filtr", "pitch_draw", (255, 200, 90))
                self._build_meters()

            with dpg.collapsing_header(label="Tempo a preset", default_open=True):
                with dpg.group(horizontal=True):
                    for bpm in (80, 100, 120, 140):
                        dpg.add_button(
                            label=str(bpm),
                            tag=f"btn_bpm_{bpm}",
                            user_data=bpm,
                            callback=self._set_bpm,
                        )
                with dpg.group(horizontal=True):
                    for name in ("Groove", "Rock", "Dense"):
                        dpg.add_button(
                            label=name,
                            user_data=name,
                            callback=self._load_preset,
                        )

    def _build_help(self) -> None:
        with dpg.child_window(width=260, height=ANT_H + 40, border=True):
            dpg.add_text("Ovladani", color=(255, 200, 120, 255))
            dpg.add_text(
                "LEVA — energie (bliz = vic):\n"
                "  postupne Kick → Snare → Hat → Basa\n\n"
                "PRAVA — filtr (bliz = otevreno,\n"
                "  dale = low-pass / klid)\n\n"
                "Gesta (na dobu):\n"
                "  prava ruka najednou bliz = echo\n"
                "  leva ruka najednou bliz = fill\n"
                "  obe ruce 0,2 s blizko = DROP\n"
                "  obe ruce daleko = klid",
                color=(160, 170, 180, 255),
            )
            dpg.add_spacer(height=8)
            dpg.add_text("Sim — leva (intenzita)")
            with dpg.group(horizontal=True):
                for mm in (120, 250, 400):
                    dpg.add_button(label=str(mm), user_data=mm, callback=lambda _s, a, u: self._set_sim_vol(u))
            dpg.add_text("Sim — prava (filtr)")
            with dpg.group(horizontal=True):
                for mm in (120, 280, 420):
                    dpg.add_button(label=str(mm), user_data=mm, callback=lambda _s, a, u: self._set_sim_pitch(u))

    def _build_strip(self, title: str, tag: str, color: tuple[int, int, int]) -> None:
        with dpg.child_window(width=ANT_W + 20, height=ANT_H + 40, border=True):
            dpg.add_text(title, color=(*color, 255))
            dpg.add_drawlist(width=ANT_W, height=ANT_H, tag=tag)

    def _build_meters(self) -> None:
        with dpg.child_window(width=200, height=ANT_H + 40, border=True):
            dpg.add_text("Takt", color=(200, 210, 220, 255))
            dpg.add_text("-", tag="now_step", color=(255, 220, 120, 255))
            dpg.add_spacer(height=6)
            dpg.add_text("Vrstvy", color=(200, 210, 220, 255))
            dpg.add_text("-", tag="now_layers", color=(180, 230, 200, 255))
            dpg.add_spacer(height=6)
            dpg.add_text("Energie", color=(200, 210, 220, 255))
            dpg.add_text("-", tag="now_intensity", color=(180, 230, 200, 255))
            dpg.add_spacer(height=6)
            dpg.add_text("Filtr", color=(200, 210, 220, 255))
            dpg.add_text("-", tag="now_filter", color=(255, 220, 160, 255))
            dpg.add_spacer(height=6)
            dpg.add_text("Echo", color=(200, 210, 220, 255))
            dpg.add_text("-", tag="now_echo", color=(180, 200, 255, 255))
            dpg.add_spacer(height=6)
            dpg.add_text("Rezim", color=(200, 210, 220, 255))
            dpg.add_text("-", tag="now_mode", color=(200, 210, 220, 255))
            dpg.add_spacer(height=8)
            dpg.add_text("-", tag="read_pitch")
            dpg.add_text("-", tag="read_vol")

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

    def _set_bpm(self, _s, _a, bpm: int) -> None:
        self.rhythm.set_bpm(float(bpm))
        for v in (80, 100, 120, 140):
            tag = f"btn_bpm_{v}"
            if dpg.does_item_exist(tag):
                dpg.bind_item_theme(tag, "btn_on" if v == bpm else "btn_off")

    def _load_preset(self, _s, _a, name: str) -> None:
        self.rhythm.load_preset(name)

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

    def _trigger_fx_sound(self, kind: FxKind, peak: float) -> None:
        if kind == FxKind.FILL:
            self.synth.trigger_sample(1, peak)
            self.synth.trigger_sample(2, peak * 0.75)
            self.synth.trigger_sample(0, peak * 0.55)
        elif kind == FxKind.DROP:
            self.synth.trigger_sample(5, min(1.0, peak * 1.2))
            self.synth.trigger_sample(0, peak)
            self.synth.trigger_sample(3, peak * 0.85)

    def _echo_delay_s(self) -> float:
        beat = 60.0 / max(self.rhythm.bpm, 40.0)
        return beat * 0.75

    def _tick(self) -> None:
        self._read_controls()
        now = time.perf_counter()
        self.synth.ensure_running(now)

        voice: Voice | None = None
        frame = self.source.poll()
        if frame is not None:
            voice = self.pipeline.push(frame)
            self._last_voice = voice

        if voice is None:
            voice = self._last_voice

        st = self.dj.state
        self.dj.observe(
            now,
            voice.pitch_mm if voice else None,
            voice.volume_mm if voice else None,
        )
        self.dj.tick_fx_decay()
        self.synth.set_dj_mix(
            st.filter_open,
            st.echo,
            st.drive,
            self._echo_delay_s(),
        )

        peak = self._stable_peak()
        fired, steps = self.rhythm.update(now)
        for step in steps:
            if step != self._last_beat_step:
                self._beat_flash = 1.0
                self._last_beat_step = step
            fx = self.dj.on_step(step)
            if fx is not None:
                self.dj.apply_fx(fx)
                self._trigger_fx_sound(fx, peak)

        self._beat_flash *= 0.82

        for idx in fired:
            vol = peak * self.dj.slot_gain(idx)
            if vol >= 0.035:
                self.synth.trigger_sample(idx, vol)

        self.synth.apply_voice(self._mute_voice(voice))
        self._update_ui(voice)

    def _update_ui(self, voice: Voice | None) -> None:
        st = self.dj.state
        step = self.rhythm.current_step + 1
        if dpg.does_item_exist("now_step"):
            dpg.set_value("now_step", f"{step} / {STEPS}  ({self.rhythm.bpm:.0f} BPM)")
        if dpg.does_item_exist("now_intensity"):
            dpg.set_value("now_intensity", f"{int(st.energy * 100)} %")
        if dpg.does_item_exist("now_layers"):
            dpg.set_value("now_layers", self.dj.layer_labels())
        if dpg.does_item_exist("now_filter"):
            dpg.set_value("now_filter", f"{int(st.filter_open * 100)} % otevreno")
        if dpg.does_item_exist("now_echo"):
            dpg.set_value("now_echo", f"{int(st.echo * 100)} %" if st.echo > 0.02 else "—")
        if dpg.does_item_exist("now_mode"):
            dpg.set_value("now_mode", st.mode)
        if dpg.does_item_exist("hero_banner"):
            dpg.set_value(
                "hero_banner",
                f"Groove {self.rhythm.preset_name}  —  takt {step}",
            )
        if dpg.does_item_exist("fx_banner"):
            banner = st.banner or ("Fronta: " + ", ".join(p.kind.value for p in self.dj.pending) if self.dj.pending else "")
            dpg.set_value("fx_banner", banner)
        if dpg.does_item_exist("read_pitch"):
            dpg.set_value("read_pitch", _mm(voice.pitch_mm if voice else None))
        if dpg.does_item_exist("read_vol"):
            dpg.set_value("read_vol", _mm(voice.volume_mm if voice else None))
        self._draw_strips(voice)

    def _draw_strips(self, voice: Voice | None) -> None:
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
        if self._beat_flash > 0.08:
            a = int(40 + 80 * self._beat_flash)
            dpg.draw_rectangle((0, 0), (ANT_W, ANT_H), fill=(90, 200, 140, a), parent=parent)
        dpg.draw_text((28, 6), "bliz = vic vrstev", size=12, color=(160, 190, 175, 255), parent=parent)
        dpg.draw_text((28, ANT_H - 20), "dale = klid", size=12, color=(160, 190, 175, 255), parent=parent)
        y_close = _mm_to_y(CLOSE_MM)
        y_far = _mm_to_y(FAR_MM)
        dpg.draw_line((20, y_close), (ANT_W - 8, y_close), color=(255, 180, 80, 160), thickness=1, parent=parent)
        dpg.draw_line((20, y_far), (ANT_W - 8, y_far), color=(100, 180, 255, 160), thickness=1, parent=parent)
        lvl = self.dj.state.energy
        y_lvl = _mm_to_y(MM_LO + (1.0 - lvl) * (MM_HI - MM_LO))
        dpg.draw_line((32, y_lvl), (ANT_W - 16, y_lvl), color=(100, 255, 180, 200), thickness=3, parent=parent)
        dpg.draw_text(
            (36, ANT_H // 2 - 8),
            f"{self.dj.layer_labels()}",
            size=12,
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
        dpg.draw_text((28, 6), "dale = filtr", size=12, color=(255, 200, 120, 255), parent=parent)
        dpg.draw_text((28, ANT_H - 20), "bliz = otevreno", size=12, color=(255, 200, 120, 255), parent=parent)
        y_close = _mm_to_y(CLOSE_MM)
        y_far = _mm_to_y(FAR_MM)
        dpg.draw_line((20, y_close), (ANT_W - 8, y_close), color=(255, 180, 80, 160), thickness=1, parent=parent)
        dpg.draw_line((20, y_far), (ANT_W - 8, y_far), color=(100, 180, 255, 160), thickness=1, parent=parent)
        fc = self.dj.state.filter_open
        y_fc = _mm_to_y(MM_LO + (1.0 - fc) * (MM_HI - MM_LO))
        dpg.draw_line((32, y_fc), (ANT_W - 16, y_fc), color=(255, 220, 100, 200), thickness=3, parent=parent)
        dpg.draw_text((36, ANT_H // 2 - 8), f"filtr {int(fc * 100)}%", size=13, color=(255, 230, 160, 255), parent=parent)
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
    DjApp().run()
