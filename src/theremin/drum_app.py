"""Sonar drum — each sensor is a pad; cross 10 cm line = hit."""

from __future__ import annotations

import time

import dearpygui.dearpygui as dpg
import numpy as np

from theremin.drum_trigger import DEFAULT_THRESHOLD_MM, DrumPad
from theremin.pipeline import Pipeline, RuntimeConfig
from theremin.sampler import SAMPLE_SLOTS
from theremin.sources import SerialSource, SimulatorSource, list_serial_ports
from theremin.synth import Synth
from theremin.types import Voice

ANT_W, ANT_H = 180, 440
MM_LO = 30.0
MM_HI = 420.0
DEFAULT_PORT = "COM7"
DEFAULT_BAUD = 115200

PAD_COLORS = {
    "Kick": (220, 90, 80),
    "Snare": (230, 150, 70),
    "Hat": (240, 210, 70),
    "Bass": (90, 190, 120),
    "Whoosh": (70, 150, 210),
    "Boom": (150, 90, 210),
}

SOUND_NAMES = tuple(label for _sid, label in SAMPLE_SLOTS)
SOUND_INDEX = {label: i for i, (_sid, label) in enumerate(SAMPLE_SLOTS)}


class DrumApp:
    def __init__(self) -> None:
        self.cfg = RuntimeConfig()
        self.cfg.volume_enabled = False
        self.cfg.snap_pick = False
        self.cfg.retrigger = False
        self.cfg.sampler_mode = False
        self.cfg.filter_mode = "median_ema"
        self.cfg.median_k = 9
        self.cfg.ema_alpha = 0.12
        self.cfg.jump_snap_mm = 90.0
        self.cfg.hold_missing = 14
        self.pipeline = Pipeline(self.cfg)
        self.left = DrumPad(sample_index=0, label="Kick")
        self.right = DrumPad(sample_index=1, label="Snare")
        self.simulator = SimulatorSource()
        self.serial = SerialSource()
        self.source = self.simulator
        self.synth = Synth()
        self.synth.sampler_active = True
        self._last_voice: Voice | None = None
        self._flash_left = 0.0
        self._flash_right = 0.0
        self._sim_restore_t = 0.0
        self._sim_restore_pitch = 280.0
        self._sim_restore_vol = 280.0
        self._sim_strike_at = 0.0
        self._sim_strike_near_mm = 75.0
        self._sim_strike_side = ""
        self._ui = {
            "master_vol": 0.62,
            "sim_pitch": 280.0,
            "sim_volume": 280.0,
            "pitch_min": 40.0,
            "pitch_max": 520.0,
            "volume_min": 40.0,
            "volume_max": 520.0,
            "ema_alpha": 0.12,
            "hold_missing": 14,
            "sim_jitter": 2.0,
            "sim_dropout": 0.01,
            "sim_rate": 45.0,
            "baud": DEFAULT_BAUD,
            "filter_mode": "median_ema",
        }

    def run(self) -> None:
        self.simulator.start()
        self.synth.start()

        dpg.create_context()
        self._theme()
        self._build()
        dpg.create_viewport(title="Sonar Drum", width=920, height=780)
        dpg.setup_dearpygui()
        dpg.bind_theme("drum_theme")
        dpg.set_primary_window("drum_root", True)
        dpg.show_viewport()
        self._auto_serial()

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
        with dpg.theme(tag="drum_theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (12, 14, 18, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (225, 228, 232, 255))

    def _build(self) -> None:
        with dpg.window(tag="drum_root", no_close=True):
            with dpg.group(horizontal=True):
                dpg.add_text("SONAR DRUM", color=(255, 180, 90, 255))
                dpg.add_spacer(width=10)
                dpg.add_radio_button(
                    ("Simulator", "Serial"),
                    horizontal=True,
                    tag="source_radio",
                    callback=lambda _s, a: self._set_source(a),
                )
                dpg.add_combo(
                    items=list_serial_ports(),
                    default_value=DEFAULT_PORT,
                    tag="port_combo",
                    width=100,
                )
                dpg.add_button(label="Connect", callback=lambda: self._set_source("Serial"))
                dpg.add_spacer(width=10)
                dpg.add_checkbox(label="MUTE", tag="mute", default_value=False)
                dpg.add_button(label="Ticho", user_data=0.2, callback=self._set_master)
                dpg.add_button(label="Normal", user_data=0.55, callback=self._set_master)
                dpg.add_button(label="Nahlas", user_data=0.78, callback=self._set_master)

            dpg.add_spacer(height=6)
            dpg.add_text(
                "Uder = VSTUP do blizke zony  |  znovu az ruka v modre zone (daleko)",
                color=(255, 210, 120, 255),
            )
            dpg.add_text("", tag="status", color=(150, 160, 170, 255))

            with dpg.group(horizontal=True):
                self._build_pad_panel("LEVA — cidlo 0 (pitch)", "left", self.left)
                self._build_pad_panel("PRAVA — cidlo 1 (volume)", "right", self.right)

            with dpg.collapsing_header(label="Nastaveni", default_open=True):
                dpg.add_text("Zvuk na stranu")
                with dpg.group(horizontal=True):
                    dpg.add_text("Leva:")
                    dpg.add_combo(
                        items=list(SOUND_NAMES),
                        default_value="Kick",
                        tag="sound_left",
                        width=100,
                        callback=self._on_sound_left,
                    )
                    dpg.add_spacer(width=16)
                    dpg.add_text("Prava:")
                    dpg.add_combo(
                        items=list(SOUND_NAMES),
                        default_value="Snare",
                        tag="sound_right",
                        width=100,
                        callback=self._on_sound_right,
                    )
                dpg.add_spacer(height=4)
                dpg.add_slider_int(
                    label="Prah uderu (mm)",
                    default_value=int(DEFAULT_THRESHOLD_MM),
                    min_value=60,
                    max_value=160,
                    tag="threshold",
                    callback=self._on_threshold,
                )
                dpg.add_slider_int(
                    label="Tolerance hranice (mm)",
                    default_value=15,
                    min_value=0,
                    max_value=45,
                    tag="tolerance",
                    callback=self._on_tuning,
                )
                dpg.add_text(
                    "Sirsí zona pod carou = spolehlivejsi, mensi = presnejsi",
                    color=(130, 140, 150, 255),
                )
                dpg.add_slider_int(
                    label="Min doba mezi udery (ms)",
                    default_value=120,
                    min_value=40,
                    max_value=450,
                    tag="cooldown_ms",
                    callback=self._on_tuning,
                )
                dpg.add_text(
                    "Vetsi = mene falesnych uderu ze sumy",
                    color=(130, 140, 150, 255),
                )
                dpg.add_slider_int(
                    label="Filtr / vyhlazeni",
                    default_value=70,
                    min_value=0,
                    max_value=100,
                    tag="smooth",
                    callback=self._on_tuning,
                )
                dpg.add_text(
                    "Vetsi = mene klepani, mensi = rychlejsi reakce",
                    color=(130, 140, 150, 255),
                )
                dpg.add_text(
                    "Pravidlo: uder jen pri prechodu do BLIZKO. V blizke zone "
                    "mavas rukou jak chces — bez dalsich uderu.",
                    color=(130, 140, 150, 255),
                )
                dpg.add_text("Sim — test uderu (pretahne ruku pod caru)")
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Leva uder", callback=lambda: self._sim_strike("left"))
                    dpg.add_button(label="Prava uder", callback=lambda: self._sim_strike("right"))
                dpg.add_text("Sim — drz vzdalenost")
                with dpg.group(horizontal=True):
                    for mm in (80, 120, 200, 320):
                        dpg.add_button(
                            label=f"L {mm}",
                            user_data=("left", float(mm)),
                            callback=self._sim_set_mm,
                        )
                    for mm in (80, 120, 200, 320):
                        dpg.add_button(
                            label=f"P {mm}",
                            user_data=("right", float(mm)),
                            callback=self._sim_set_mm,
                        )

    def _build_pad_panel(self, title: str, side: str, pad: DrumPad) -> None:
        with dpg.child_window(width=ANT_W + 36, height=ANT_H + 120, border=True):
            dpg.add_text(title, color=(200, 210, 220, 255))
            col = PAD_COLORS.get(pad.label, (200, 200, 200))
            dpg.add_text(pad.label, tag=f"{side}_label", color=(*col, 255))
            dpg.add_text("0 uderu", tag=f"{side}_hits", color=(160, 170, 180, 255))
            dpg.add_drawlist(width=ANT_W, height=ANT_H, tag=f"{side}_draw")
            dpg.add_text("-", tag=f"{side}_read")

    def _auto_serial(self) -> None:
        ports = list_serial_ports()
        if ports:
            dpg.configure_item("port_combo", items=ports)
        dpg.set_value("port_combo", DEFAULT_PORT)
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
            msg = f"Serial {port}"
        else:
            self.simulator.start()
            self.source = self.simulator
            msg = "Simulator"
        if dpg.does_item_exist("status"):
            dpg.set_value("status", msg)

    def _set_master(self, _s=None, _a=None, user_data=0.55) -> None:
        self._ui["master_vol"] = float(user_data)
        self.synth.master = float(user_data)

    def _on_sound_left(self, _s, label: str) -> None:
        self.left.sample_index = SOUND_INDEX.get(label, 0)
        self.left.label = label

    def _on_sound_right(self, _s, label: str) -> None:
        self.right.sample_index = SOUND_INDEX.get(label, 1)
        self.right.label = label

    def _on_threshold(self, _s, val: int) -> None:
        t = float(val)
        self.left.threshold_mm = t
        self.right.threshold_mm = t

    def _on_tuning(self, _s=None, _a=None) -> None:
        if not dpg.does_item_exist("tolerance"):
            return
        tol = float(dpg.get_value("tolerance"))
        cd_ms = float(dpg.get_value("cooldown_ms"))
        smooth = float(dpg.get_value("smooth")) / 100.0
        for pad in (self.left, self.right):
            pad.tolerance_mm = tol
            pad.cooldown_s = max(0.04, cd_ms / 1000.0)
            pad.smooth = smooth
        # Pipeline: more smooth = wider median, slower EMA, ignore smaller jumps.
        self.cfg.median_k = int(round(5 + 6 * smooth))
        self.cfg.ema_alpha = float(0.28 - 0.18 * smooth)
        self.cfg.jump_snap_mm = 70.0 + 50.0 * smooth
        self._ui["median_k"] = self.cfg.median_k
        self._ui["ema_alpha"] = self.cfg.ema_alpha
        self._ui["jump_snap_mm"] = self.cfg.jump_snap_mm

    def _sim_strike(self, side: str) -> None:
        now = time.perf_counter()
        if side == "left":
            self.left._zone = "far"
            self.left._armed = True
            self._sim_restore_pitch = float(self.simulator.pitch_target_mm)
            self.simulator.pitch_target_mm = 280.0
            self._sim_strike_near_mm = 75.0
            self._sim_strike_at = now + 0.12
            self._sim_strike_side = "left"
        else:
            self.right._zone = "far"
            self.right._armed = True
            self._sim_restore_vol = float(self.simulator.volume_target_mm)
            self.simulator.volume_target_mm = 280.0
            self._sim_strike_near_mm = 75.0
            self._sim_strike_at = now + 0.12
            self._sim_strike_side = "right"
        self._sim_restore_t = now + 0.35

    def _sim_strike_step(self, now: float) -> None:
        if not self._sim_strike_at or now < self._sim_strike_at:
            return
        mm = self._sim_strike_near_mm
        if self._sim_strike_side == "left":
            self.simulator.pitch_target_mm = mm
            self._ui["sim_pitch"] = mm
        else:
            self.simulator.volume_target_mm = mm
            self._ui["sim_volume"] = mm
        self._sim_strike_at = 0.0

    def _sim_set_mm(self, _s, _a, user_data: tuple[str, float]) -> None:
        side, mm = user_data
        if side == "left":
            self.simulator.pitch_target_mm = mm
            self._ui["sim_pitch"] = mm
        else:
            self.simulator.volume_target_mm = mm
            self._ui["sim_volume"] = mm

    def _read_controls(self) -> None:
        if not dpg.does_item_exist("mute"):
            return
        self.synth.muted = bool(dpg.get_value("mute"))
        self.synth.master = float(self._ui["master_vol"])
        self.cfg.pitch_min_mm = float(self._ui["pitch_min"])
        self.cfg.pitch_max_mm = float(self._ui["pitch_max"])
        self.cfg.volume_min_mm = float(self._ui["volume_min"])
        self.cfg.volume_max_mm = float(self._ui["volume_max"])
        self.cfg.ema_alpha = float(self._ui["ema_alpha"])
        self.cfg.hold_missing = int(self._ui["hold_missing"])
        self.cfg.filter_mode = str(self._ui["filter_mode"])
        self.cfg.median_k = int(self._ui.get("median_k", 9))
        self.cfg.jump_snap_mm = float(self._ui.get("jump_snap_mm", 90.0))
        self.pipeline.sync_filters()
        self._on_tuning()
        self.simulator.jitter_mm = float(self._ui["sim_jitter"])
        self.simulator.dropout = float(self._ui["sim_dropout"])
        self.simulator.rate_hz = float(self._ui["sim_rate"])
        self.simulator.pitch_target_mm = float(self._ui["sim_pitch"])
        self.simulator.volume_target_mm = float(self._ui["sim_volume"])

    def _tick(self) -> None:
        self._read_controls()
        now = time.perf_counter()
        self.synth.ensure_running(now)

        if self._sim_restore_t and now >= self._sim_restore_t:
            self.simulator.volume_target_mm = self._sim_restore_vol
            self.simulator.pitch_target_mm = self._sim_restore_pitch
            self._ui["sim_volume"] = self._sim_restore_vol
            self._ui["sim_pitch"] = self._sim_restore_pitch
            self._sim_restore_t = 0.0

        self._sim_strike_step(now)

        voice: Voice | None = None
        frame = self.source.poll()
        if frame is not None:
            voice = self.pipeline.push(frame)
            self._last_voice = voice
        if voice is None:
            voice = self._last_voice

        vol_mm = voice.volume_mm if voice else None
        pitch_mm = voice.pitch_mm if voice else None

        hit_l, peak_l = self.left.update(now, pitch_mm)
        hit_r, peak_r = self.right.update(now, vol_mm)

        master = float(self.synth.master)
        if hit_l:
            self.synth.trigger_sample(self.left.sample_index, peak_l * master)
            self._flash_left = 1.0
        if hit_r:
            self.synth.trigger_sample(self.right.sample_index, peak_r * master)
            self._flash_right = 1.0

        self._flash_left *= 0.78
        self._flash_right *= 0.78

        if voice:
            self.synth.apply_voice(self._mute_voice(voice))

        self._draw_pad("left", self.left.filtered_mm(), self.left, self._flash_left)
        self._draw_pad("right", self.right.filtered_mm(), self.right, self._flash_right)

    def _mute_voice(self, voice: Voice) -> Voice:
        return Voice(
            0.0,
            0.0,
            None,
            False,
            False,
            voice.pitch_raw_mm,
            voice.pitch_mm,
            voice.volume_raw_mm,
            voice.volume_mm,
            None,
            False,
            voice.in_pitch_range,
            voice.in_volume_range,
            None,
        )

    def _draw_pad(self, side: str, mm: float | None, pad: DrumPad, flash: float) -> None:
        parent = f"{side}_draw"
        if not dpg.does_item_exist(parent):
            return
        dpg.delete_item(parent, children_only=True)

        col = PAD_COLORS.get(pad.label, (200, 200, 200))
        dpg.draw_rectangle((0, 0), (ANT_W, ANT_H), fill=(16, 18, 24, 255), parent=parent)

        if flash > 0.05:
            a = int(50 + 180 * flash)
            dpg.draw_rectangle((0, 0), (ANT_W, ANT_H), fill=(*col, a), parent=parent)

        # Strike zone below threshold (bottom of pad = close to sensor).
        y_thr = _mm_to_y(pad.threshold_mm)
        y_low = _mm_to_y(pad.strike_below_mm)
        dpg.draw_rectangle(
            (0, y_low),
            (ANT_W, ANT_H),
            fill=(*col, 45),
            parent=parent,
        )
        if pad.tolerance_mm > 0.5:
            dpg.draw_line(
                (8, y_low),
                (ANT_W - 8, y_low),
                color=(255, 140, 80, 180),
                thickness=1,
                parent=parent,
            )
        dpg.draw_line(
            (8, y_thr),
            (ANT_W - 8, y_thr),
            color=(255, 80, 80, 255),
            thickness=3,
            parent=parent,
        )
        y_rearm = _mm_to_y(pad.rearm_mm)
        if y_rearm > 8:
            dpg.draw_line(
                (8, y_rearm),
                (ANT_W - 8, y_rearm),
                color=(80, 160, 255, 120),
                thickness=1,
                parent=parent,
            )
        dpg.draw_text(
            (12, y_thr - 18),
            f"prah {pad.threshold_mm:.0f} mm",
            size=12,
            color=(255, 120, 120, 255),
            parent=parent,
        )
        dpg.draw_text((12, 8), "cidlo", size=11, color=(140, 150, 160, 255), parent=parent)
        dpg.draw_text((12, ANT_H - 22), "uder", size=11, color=(140, 150, 160, 255), parent=parent)

        if mm is not None:
            y = _mm_to_y(mm)
            dpg.draw_circle(
                (ANT_W - 28, y),
                11,
                fill=(*col, 255),
                color=(20, 20, 20, 200),
                parent=parent,
            )
            dpg.draw_line((20, y), (ANT_W - 44, y), color=(*col, 180), thickness=2, parent=parent)

        if dpg.does_item_exist(f"{side}_hits"):
            dpg.set_value(f"{side}_hits", f"{pad.hits} uderu")
        if dpg.does_item_exist(f"{side}_label"):
            dpg.set_value(f"{side}_label", pad.label)
            dpg.configure_item(f"{side}_label", color=(*col, 255))
        zone_names = {"near": "BLIZKO", "mid": "stred", "far": "daleko"}
        if dpg.does_item_exist(f"{side}_read"):
            z = zone_names.get(pad.zone(), "?")
            dpg.set_value(f"{side}_read", f"{_mm(mm)}  [{z}]")


def _mm(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f} mm"


def _mm_to_y(mm: float) -> float:
    t = (float(mm) - MM_LO) / max(MM_HI - MM_LO, 1.0)
    t = float(np.clip(t, 0.0, 1.0))
    pad = 24.0
    return pad + (1.0 - t) * (ANT_H - 2 * pad)


def main() -> None:
    DrumApp().run()
