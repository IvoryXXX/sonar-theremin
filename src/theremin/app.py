from __future__ import annotations

import time
from collections import deque
from datetime import datetime
from pathlib import Path

import dearpygui.dearpygui as dpg

from theremin.logger import CsvLogger
from theremin.pipeline import FILTER_MODES, NOTE_CHOICES, SCALE_NAMES, Pipeline, RuntimeConfig, note_positions
from theremin.deck import Deck
from theremin.simon import SimonGame
from theremin.songs import DEMO_BUTTONS, DEMO_TIPS, MELODY_SETUP
from theremin.sources import ReplaySource, SerialSource, SimulatorSource, list_serial_ports
from theremin.synth import VOICE_NAMES, Synth
from theremin.types import SensorFrame, Voice

MM_LO = 40.0
MM_HI = 520.0
ANT_W, ANT_H = 168, 420
HISTORY = 240
PLOT_W = 520
DEFAULT_PORT = "COM7"
DEFAULT_BAUD = 115200


class App:
    def __init__(self) -> None:
        self.cfg = RuntimeConfig()
        self.pipeline = Pipeline(self.cfg)
        self.simulator = SimulatorSource()
        self.serial = SerialSource()
        self.replay = ReplaySource()
        self.source = self.simulator
        self.synth = Synth()
        self.deck = Deck()
        self.simon = SimonGame()
        self.logger: CsvLogger | None = None
        self._simon_phase: str | None = None
        self._t0 = time.perf_counter()
        self._hist_t: deque[float] = deque(maxlen=HISTORY)
        self._hist_pr: deque[float] = deque(maxlen=HISTORY)
        self._hist_pf: deque[float] = deque(maxlen=HISTORY)
        self._hist_vr: deque[float] = deque(maxlen=HISTORY)
        self._hist_vf: deque[float] = deque(maxlen=HISTORY)
        self._last_voice: Voice | None = None
        self._status = "Simulator ready"
        self._zone_count = 10
        self._last_lights_sync = 0.0
        self._audio_restarts = 0
        self._source_before_demo: str | None = None
        self._nastroj = False
        self._pistala = False
        self._before_nastroj: dict[str, object] | None = None
        self._before_pistala: dict[str, object] | None = None
        # All tunables live here - no slider widgets in the UI
        self._ui: dict[str, float | int | bool | str] = {
            "master_vol": 0.4,
            "jas": 0.55,
            "sim_pitch": 200.0,
            "sim_volume": 140.0,
            "pitch_min": 80.0,
            "pitch_max": 500.0,
            "volume_min": 60.0,
            "volume_max": 500.0,
            "ema_alpha": 0.35,
            "hysteresis": 18.0,
            "sim_jitter": 3.0,
            "sim_dropout": 0.02,
            "sim_rate": 40.0,
            "bpm": 108,
            "drum_gain": 0.7,
            "simon_len": 3,
            "simon_tol": 2,
            "sweep_pitch": False,
            "sweep_volume": False,
            "baud": 115200,
            "filter_mode": "median_ema",
            "pitch_magnet": 0.0,
            "hold_missing": 8,
            "default_amp": 0.55,
        }

    def run(self) -> None:
        self.simulator.start()
        self.synth.start()
        if self.synth.error:
            self._status = f"Audio: {self.synth.error}"

        dpg.create_context()
        self._theme()
        self._build()
        dpg.create_viewport(title="Sonar Theremin", width=1280, height=900)
        dpg.setup_dearpygui()
        dpg.bind_theme("app_theme")
        dpg.set_primary_window("root", True)
        dpg.show_viewport()
        self._auto_connect_serial()
        self._paint_scales(None)  # show tone/volume strips immediately

        try:
            while dpg.is_dearpygui_running():
                self._tick()
                dpg.render_dearpygui_frame()
        finally:
            self._shutdown()
            dpg.destroy_context()

    def _auto_connect_serial(self) -> None:
        ports = list_serial_ports()
        dpg.configure_item("port_combo", items=ports)
        port = DEFAULT_PORT if DEFAULT_PORT in ports else (ports[0] if ports else "")
        if port:
            dpg.set_value("port_combo", port)
        dpg.set_value("source_radio", "Serial")
        self._ui["baud"] = DEFAULT_BAUD
        self._set_source("Serial")

    def _shutdown(self) -> None:
        self._stop_log()
        self.source.stop()
        self.serial.stop()
        self.synth.stop()

    def _tick(self) -> None:
        self._read_controls()
        now = time.perf_counter()
        was_alive = self.synth.is_alive()
        if not self.synth.ensure_running(now):
            if self.synth.error:
                self._status = f"Audio: {self.synth.error}"
        elif not was_alive and self.synth.is_alive():
            self._audio_restarts += 1
            self._status = f"Audio obnoveno ({self._audio_restarts}x)"
        events = self.deck.update(now)
        for event in events:
            if event == "kick":
                self.synth.trigger_kick()
            elif event == "hat":
                self.synth.trigger_hat()
            elif event == "snare":
                self.synth.trigger_snare()
        if self.deck.melody_id:
            self.simulator.jitter_mm = 0.0
            self.simulator.dropout = 0.0
            if self.deck.pitch_mm is not None:
                self.simulator.pitch_target_mm = self.deck.pitch_mm
                self._ui["sim_pitch"] = self.deck.pitch_mm
            vol_mm = self._melody_volume_mm(self.deck.velocity)
            self.simulator.volume_target_mm = vol_mm
            self._ui["sim_volume"] = min(max(vol_mm, MM_LO), MM_HI)
            if self.deck.snap:
                self.pipeline.snap_to(self.deck.pitch_mm, vol_mm, self.deck.current_note)
        frame = self.source.poll()
        if self.simulator.sweep_pitch:
            self._ui["sim_pitch"] = self.simulator.pitch_target_mm
        if self.simulator.sweep_volume:
            self._ui["sim_volume"] = self.simulator.volume_target_mm

        voice: Voice | None = None
        if frame is not None:
            voice = self.pipeline.push(frame)
            self._last_voice = voice
            if self.logger is not None:
                self.logger.write(frame, voice)
            self._push_history(frame, voice)
            self._draw_antennas(voice)
            self._update_plots()
        elif self._last_voice is not None:
            voice = self._last_voice
            self._draw_antennas(self._last_voice)

        if self.simon.active:
            gate = bool(voice.gate) if voice is not None else False
            prev = self.simon.phase
            self.simon.tick(now, gate)
            phase = self.simon.phase
            if phase in (SimonGame.DEMO, SimonGame.FANFARE):
                self.synth.apply_voice(self.simon.demo_voice())
            elif phase == SimonGame.READY:
                self.synth.silence()
            elif phase == SimonGame.LISTEN:
                if voice is not None:
                    note = voice.note_name if (voice.gate and voice.note_name) else None
                    self.simon.feed(note, voice.gate, now, voice.pitch_mm)
                    self.synth.apply_voice(voice)
                else:
                    self.synth.silence()
            elif phase == SimonGame.SUCCESS:
                if voice is not None:
                    self.synth.apply_voice(voice)
                if prev != SimonGame.SUCCESS:
                    self.synth.trigger_hat()
                    self.synth.trigger_kick()
            elif phase == SimonGame.FAIL:
                self.synth.silence()
                if prev != SimonGame.FAIL:
                    self.synth.trigger_snare()
            self._status = self.simon.status
            if dpg.does_item_exist("simon_status"):
                dpg.set_value("simon_status", self.simon.status)
            if dpg.does_item_exist("hero_banner"):
                dpg.set_value("hero_banner", self.simon.banner or self._status)
            if dpg.does_item_exist("simon_streak"):
                dpg.set_value("simon_streak", f"Serie {self.simon.streak}x")
            self._refresh_simon_lights()
        else:
            if voice is not None:
                self.synth.apply_voice(voice)
            if dpg.does_item_exist("hero_banner") and not self.simon.banner:
                snap = bool(dpg.get_value("snap_pick")) if dpg.does_item_exist("snap_pick") else False
                if snap:
                    if voice and voice.retrigger and voice.note_name:
                        dpg.set_value("hero_banner", f"Ton  {voice.note_name}")
                    elif voice and voice.in_pitch_range:
                        dpg.set_value("hero_banner", "Machni a vytahni ruku…")
                    else:
                        pick = self.pipeline.last_pick or "-"
                        mode = "Kytara" if self._nastroj else ("Pistala" if self._pistala else "Ohraniceni")
                        dpg.set_value("hero_banner", f"{mode}  posledni {pick}")
                elif self._nastroj:
                    if voice and voice.gate:
                        dpg.set_value("hero_banner", f"Brnk  {voice.note_name or '-'}")
                    elif self.synth._amp > 0.02:
                        dpg.set_value("hero_banner", "Kytara — zni")
                    else:
                        dpg.set_value("hero_banner", "Kytara — dej ruku do noty")
                elif self._pistala:
                    if voice and voice.gate:
                        dpg.set_value("hero_banner", f"Fouk  {voice.note_name or '-'}")
                    elif voice and voice.in_pitch_range:
                        dpg.set_value("hero_banner", "Prstoklad OK — foukni levou")
                    else:
                        dpg.set_value("hero_banner", "Pistala — prsty + dech")
                else:
                    note = voice.note_name if voice and voice.gate else "-"
                    dpg.set_value("hero_banner", f"Hrajes  {note}" if voice and voice.gate else "Pripraven")
            self._refresh_simon_lights()

        # Always redraw pitch when simon lights change even without new frame
        if voice is None and self._last_voice is not None and self.simon.active:
            self._draw_antennas(self._last_voice)

        self._update_readouts()
        if now - self._last_lights_sync > 0.25:
            self._last_lights_sync = now
            self._sync_choice_lights()

    def _read_controls(self) -> None:
        if not dpg.does_item_exist("mute"):
            return

        def _b(tag: str, default: bool = False) -> bool:
            return bool(dpg.get_value(tag)) if dpg.does_item_exist(tag) else default

        ui = self._ui
        self.cfg.filter_mode = str(dpg.get_value("filter_mode")) if dpg.does_item_exist("filter_mode") else str(ui["filter_mode"])
        self.cfg.ema_alpha = float(ui["ema_alpha"])
        self.cfg.hysteresis_mm = float(ui["hysteresis"])
        self.cfg.invert_pitch = _b("invert_pitch")
        self.cfg.invert_volume = _b("invert_volume")
        self.cfg.continuous_pitch = _b("continuous")
        self.cfg.retrigger = _b("retrigger")
        self.cfg.volume_enabled = _b("volume_enabled", True)
        self.cfg.space_to_play = _b("space_to_play")
        self.cfg.muted = _b("mute")
        self.cfg.snap_pick = _b("snap_pick")
        self.cfg.pitch_magnet = float(ui.get("pitch_magnet", 0.0))
        self.cfg.hold_missing = int(ui.get("hold_missing", 8))
        self.cfg.default_amp = float(ui.get("default_amp", 0.55))
        self.cfg.nastroj_mode = self._nastroj
        self.cfg.guitar_mode = bool(self._nastroj)
        self.cfg.flute_mode = bool(self._pistala)
        self.cfg.pitch_min_mm = float(ui["pitch_min"])
        self.cfg.pitch_max_mm = max(self.cfg.pitch_min_mm + 80.0, float(ui["pitch_max"]))
        self.cfg.volume_min_mm = float(ui["volume_min"])
        self.cfg.volume_max_mm = max(self.cfg.volume_min_mm + 80.0, float(ui["volume_max"]))
        self.pipeline.sync_filters()

        self.simulator.jitter_mm = float(ui["sim_jitter"])
        self.simulator.dropout = float(ui["sim_dropout"])
        self.simulator.rate_hz = float(ui["sim_rate"])
        self.simulator.sweep_pitch = bool(ui["sweep_pitch"])
        self.simulator.sweep_volume = bool(ui["sweep_volume"])
        if not self.simulator.sweep_pitch and self.deck.melody_id is None:
            self.simulator.pitch_target_mm = float(ui["sim_pitch"])
        if not self.simulator.sweep_volume and self.deck.melody_id is None:
            self.simulator.volume_target_mm = float(ui["sim_volume"])

        if dpg.does_item_exist("timbre"):
            name = dpg.get_value("timbre")
            if self._nastroj:
                name = "Kytara"
                if dpg.get_value("timbre") != "Kytara":
                    dpg.set_value("timbre", "Kytara")
            elif self._pistala:
                name = "Pistala"
                if dpg.get_value("timbre") != "Pistala":
                    dpg.set_value("timbre", "Pistala")
            if name != self.synth.timbre:
                self.synth.apply_timbre(name)
            self.synth.brightness = float(ui["jas"])
        self.synth.master = float(ui["master_vol"])
        self.synth.muted = _b("mute")
        if self._nastroj:
            self.synth.guitar_active = True
            self.synth.flute_active = False
            self.cfg.continuous_pitch = False
            if dpg.does_item_exist("continuous"):
                dpg.set_value("continuous", False)
        elif self._pistala:
            self.synth.guitar_active = False
            self.synth.flute_active = True
            self.cfg.continuous_pitch = False
            if dpg.does_item_exist("continuous"):
                dpg.set_value("continuous", False)
        else:
            self.synth.guitar_active = False
            self.synth.flute_active = False
        self.simon.cfg.tolerance = int(ui["simon_tol"])
        self.simon.cfg.length = int(ui["simon_len"])
        if dpg.does_item_exist("scale"):
            scale = dpg.get_value("scale")
            if scale != self.cfg.scale_name:
                self.pipeline.apply_scale(scale)
                self._sync_zone_combos()
            else:
                self._poll_zone_edits()
        self.deck.note_mm = note_positions(
            self.pipeline.mapper.notes,
            self.cfg.pitch_min_mm,
            self.cfg.pitch_max_mm,
            self.cfg.invert_pitch,
        )
        self.deck.bpm = float(ui["bpm"])
        self.deck.kick = _b("drum_kick")
        self.deck.hat = _b("drum_hat")
        self.deck.snare = _b("drum_snare")
        self.synth.drum_gain = float(ui["drum_gain"])

    def _push_history(self, frame: SensorFrame, voice: Voice) -> None:
        t = time.perf_counter() - self._t0
        self._hist_t.append(t)
        self._hist_pr.append(voice.pitch_raw_mm if voice.pitch_raw_mm is not None else float("nan"))
        self._hist_pf.append(voice.pitch_mm if voice.pitch_mm is not None else float("nan"))
        self._hist_vr.append(voice.volume_raw_mm if voice.volume_raw_mm is not None else float("nan"))
        self._hist_vf.append(voice.volume_mm if voice.volume_mm is not None else float("nan"))

    def _update_plots(self) -> None:
        xs = list(self._hist_t)
        dpg.set_value("s_pitch_raw", [xs, list(self._hist_pr)])
        dpg.set_value("s_pitch_filt", [xs, list(self._hist_pf)])
        dpg.set_value("s_vol_raw", [xs, list(self._hist_vr)])
        dpg.set_value("s_vol_filt", [xs, list(self._hist_vf)])
        if xs:
            dpg.set_axis_limits("pitch_x", xs[-1] - 5.0, xs[-1] + 0.05)
            dpg.set_axis_limits("vol_x", xs[-1] - 5.0, xs[-1] + 0.05)

    def _update_readouts(self) -> None:
        v = self._last_voice
        st = self.pipeline.stats
        note = v.note_name if v and v.note_name else "-"
        if dpg.does_item_exist("read_note"):
            dpg.set_value("read_note", note)
        if dpg.does_item_exist("now_note_big"):
            dpg.set_value("now_note_big", note if v and v.gate else "-")
        if dpg.does_item_exist("read_pitch_raw"):
            dpg.set_value("read_pitch_raw", _mm(v.pitch_raw_mm if v else None))
            dpg.set_value("read_pitch_filt", _mm(v.pitch_mm if v else None))
            dpg.set_value("read_vol_raw", _mm(v.volume_raw_mm if v else None))
            dpg.set_value("read_vol_filt", _mm(v.volume_mm if v else None))
            dpg.set_value("read_amp", f"{(v.amplitude if v else 0.0):.2f}")
        gate = "ON" if v and v.gate else "off"
        if dpg.does_item_exist("read_gate"):
            dpg.set_value("read_gate", gate)
        if dpg.does_item_exist("now_gate"):
            dpg.set_value("now_gate", gate)
        if dpg.does_item_exist("now_simon_target"):
            dpg.set_value("now_simon_target", self.simon.target_note or "-")
        if dpg.does_item_exist("diag_rate"):
            dpg.set_value("diag_rate", f"{st.rate_hz:.1f} Hz")
            dpg.set_value("diag_jitter_p", f"{st.pitch_jitter_mm:.1f} mm")
            dpg.set_value("diag_jitter_v", f"{st.volume_jitter_mm:.1f} mm")
            dpg.set_value("diag_drop_p", f"{st.invalid_pitch}/{st.invalid_volume}")
        if dpg.does_item_exist("diag_dt"):
            dpg.set_value("diag_dt", f"{st.last_dt_s * 1000:.1f} ms")
            dpg.set_value("diag_drop_v", str(st.invalid_volume))
            dpg.set_value("diag_strikes", str(st.strikes))
            dpg.set_value("diag_latency", f"~{256 / 48.0:.1f} ms")
            dpg.set_value("diag_log", f"recording {self.logger.rows}" if self.logger else "idle")
        if dpg.does_item_exist("status"):
            dpg.set_value("status", self.serial.error or self.replay.error or self._status)
        if dpg.does_item_exist("read_strike"):
            dpg.set_value("read_strike", "STRIKE" if v and v.strike else "")

    def _draw_antennas(self, voice: Voice) -> None:
        self._draw_volume(voice)
        self._draw_pitch(voice)

    def _draw_volume(self, voice: Voice) -> None:
        parent = "vol_draw"
        if not dpg.does_item_exist(parent):
            return
        dpg.delete_item(parent, children_only=True)
        dpg.draw_rectangle((0, 0), (ANT_W, ANT_H), fill=(14, 18, 22, 255), color=(14, 18, 22, 255), parent=parent)
        lo, hi = self.cfg.volume_min_mm, self.cfg.volume_max_mm
        # Full 0-50 cm guide strip
        for cm in (0, 10, 20, 30, 40, 50):
            mm = cm * 10.0
            y = _mm_to_y(mm)
            dpg.draw_line((8, y), (ANT_W - 8, y), color=(55, 65, 72, 255), thickness=1, parent=parent)
            dpg.draw_text((6, y - 7), f"{cm}", size=11, color=(130, 145, 150, 255), parent=parent)
        y0, y1 = _mm_to_y(hi), _mm_to_y(lo)
        bands = 10
        for i in range(bands):
            fa = i / bands
            fb = (i + 1) / bands
            ya = y0 + (y1 - y0) * fa
            yb = y0 + (y1 - y0) * fb
            loud = fb if self.cfg.invert_volume else (1.0 - fa)
            fill = (18, int(50 + 100 * loud), int(55 + 90 * loud), 200)
            dpg.draw_rectangle((36, ya), (ANT_W - 14, yb), fill=fill, color=(10, 12, 14, 255), parent=parent)
        if self._nastroj:
            dpg.draw_text((40, 6), "50 cm - drz", size=12, color=(160, 190, 175, 255), parent=parent)
            dpg.draw_text(
                (40, ANT_H - 20),
                "0 cm - zhasni" if not self.cfg.invert_volume else "0 cm - drz",
                size=12,
                color=(160, 190, 175, 255),
                parent=parent,
            )
            # Live sustain readout from left hand (closer = shorter)
            raw = float(voice.amplitude) if voice.amplitude > 0 else 0.55
            sust = 1.0 - max(0.0, min(1.0, raw))
            secs = 0.06 + 5.0 * sust
            dpg.draw_text(
                (40, ANT_H // 2 - 8),
                f"delka {secs:.1f}s",
                size=14,
                color=(220, 255, 200, 255),
                parent=parent,
            )
        elif self._pistala:
            dpg.draw_text((40, 6), "50 cm - bez dechu", size=12, color=(160, 190, 175, 255), parent=parent)
            dpg.draw_text(
                (40, ANT_H - 20),
                "0 cm - foukej" if not self.cfg.invert_volume else "0 cm - bez dechu",
                size=12,
                color=(160, 190, 175, 255),
                parent=parent,
            )
            breath = float(voice.amplitude) if voice.in_volume_range else 0.0
            dpg.draw_text(
                (40, ANT_H // 2 - 8),
                f"dech {int(breath * 100)}%",
                size=14,
                color=(220, 255, 200, 255),
                parent=parent,
            )
        else:
            dpg.draw_text((40, 6), "50 cm - tise", size=12, color=(160, 190, 175, 255), parent=parent)
            dpg.draw_text(
                (40, ANT_H - 20),
                "0 cm - nahlas" if not self.cfg.invert_volume else "0 cm - tise",
                size=12,
                color=(160, 190, 175, 255),
                parent=parent,
            )
        self._marker(parent, voice.volume_raw_mm, (120, 160, 150, 140), 5)
        self._marker(parent, voice.volume_mm, (100, 255, 180, 255), 9)

    def _draw_pitch(self, voice: Voice) -> None:
        parent = "pitch_draw"
        if not dpg.does_item_exist(parent):
            return
        dpg.delete_item(parent, children_only=True)
        dpg.draw_rectangle((0, 0), (ANT_W, ANT_H), fill=(14, 16, 20, 255), color=(14, 16, 20, 255), parent=parent)
        # cm ruler on the left edge
        for cm in (0, 10, 20, 30, 40, 50):
            y = _mm_to_y(cm * 10.0)
            dpg.draw_line((4, y), (18, y), color=(70, 80, 90, 255), thickness=1, parent=parent)
            dpg.draw_text((2, y - 6), f"{cm}", size=10, color=(120, 130, 140, 255), parent=parent)
        n = len(self.pipeline.mapper.notes)
        lo, hi = self.cfg.pitch_min_mm, self.cfg.pitch_max_mm
        lit = self.simon.lit_notes if self.simon.active else set()
        target = self.simon.target_note if self.simon.active else None
        continuous = bool(self.cfg.continuous_pitch) and not self.simon.active
        palette = [
            (56, 120, 168),
            (48, 140, 150),
            (52, 150, 120),
            (90, 150, 90),
            (140, 140, 70),
            (170, 120, 70),
            (170, 95, 95),
            (140, 90, 140),
            (100, 100, 160),
            (70, 110, 160),
        ]
        label_size = 14 if n <= 6 else 12 if n <= 8 else 11
        for i, (name, _) in enumerate(self.pipeline.mapper.notes):
            if self.cfg.invert_pitch:
                a, b = (n - i - 1) / n, (n - i) / n
            else:
                a, b = i / n, (i + 1) / n
            d0 = lo + a * (hi - lo)
            d1 = lo + b * (hi - lo)
            ya, yb = _mm_to_y(d1), _mm_to_y(d0)
            playing = voice.note_name == name and voice.in_pitch_range and voice.gate
            is_target = name == target
            is_lit = name in lit
            col = palette[i % len(palette)]
            if continuous:
                fill = (*col, 70)
                text_col = (180, 190, 200, 160)
            elif is_target:
                fill = (255, 196, 72, 255)
                text_col = (20, 18, 12, 255)
            elif is_lit:
                fill = (255, 160, 60, 220)
                text_col = (20, 18, 12, 255)
            elif playing:
                fill = (*col, 245)
                text_col = (250, 250, 245, 255)
            else:
                fill = (*col, 160)
                text_col = (230, 235, 240, 230)
            dpg.draw_rectangle((28, ya), (ANT_W - 12, yb), fill=fill, color=(8, 10, 12, 255), parent=parent)
            if is_target:
                dpg.draw_rectangle((24, ya + 2), (ANT_W - 8, yb - 2), color=(255, 255, 255, 230), thickness=2, parent=parent)
            dpg.draw_text((36, (ya + yb) * 0.5 - 8), name, size=label_size, color=text_col, parent=parent)
        if continuous:
            dpg.draw_text((30, 6), "spojita vyska", size=12, color=(255, 200, 100, 255), parent=parent)
            # Continuous play head — full-width thin bar at filtered pitch
            if voice.pitch_mm is not None and voice.in_pitch_range:
                y = _mm_to_y(voice.pitch_mm)
                dpg.draw_line((26, y), (ANT_W - 10, y), color=(255, 230, 120, 230), thickness=3, parent=parent)
                if voice.gate and voice.frequency_hz > 0:
                    dpg.draw_text(
                        (32, y - 16),
                        f"{voice.note_name or '-'}  {voice.frequency_hz:.0f} Hz",
                        size=12,
                        color=(255, 240, 180, 255),
                        parent=parent,
                    )
        else:
            dpg.draw_text((30, 6), "50 cm", size=11, color=(150, 160, 170, 255), parent=parent)
        dpg.draw_text((30, ANT_H - 18), "0 cm", size=11, color=(150, 160, 170, 255), parent=parent)
        self._marker(parent, voice.pitch_raw_mm, (200, 180, 120, 120), 4)
        self._marker(parent, voice.pitch_mm, (255, 230, 120, 255), 8)

    def _marker(self, parent: str, mm: float | None, color: tuple[int, int, int, int], radius: int) -> None:
        if mm is None:
            return
        y = _mm_to_y(mm)
        # Side pointer only (not a full-width line that looks like a slider)
        x1 = ANT_W - 22
        dpg.draw_triangle((x1, y), (ANT_W - 4, y - 8), (ANT_W - 4, y + 8), color=color, fill=color, parent=parent)
        dpg.draw_circle((ANT_W * 0.55, y), radius, color=color, fill=color, parent=parent)

    def _set_source(self, label: str) -> None:
        self.source.stop()
        if label == "Serial":
            self.serial.port = dpg.get_value("port_combo")
            self.serial.baud = int(self._ui.get("baud", DEFAULT_BAUD))
            self.serial.start()
            self.source = self.serial
            self._status = self.serial.error or f"Serial {self.serial.port}"
        elif label == "Replay":
            path = dpg.get_value("replay_path")
            if path and self.replay.load(path):
                self.replay.start()
                self.source = self.replay
                self._status = f"Replay {Path(path).name}"
            else:
                self.source = self.simulator
                self.simulator.start()
                dpg.set_value("source_radio", "Simulator")
                self._status = self.replay.error or "Replay failed"
        else:
            self.source = self.simulator
            self.simulator.start()
            self._status = "Simulator ready"
        self.pipeline.reset()
        self._hist_t.clear()
        self._hist_pr.clear()
        self._hist_pf.clear()
        self._hist_vr.clear()
        self._hist_vf.clear()
        self._last_voice = None
        self.synth.silence()

    def _toggle_log(self) -> None:
        if self.logger is not None:
            self._stop_log()
            dpg.set_item_label("record_btn", "Record CSV")
            self._status = "Recording stopped"
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path("logs") / f"capture_{stamp}.csv"
        self.logger = CsvLogger(path)
        dpg.set_item_label("record_btn", "Stop recording")
        self._status = f"Recording {path}"

    def _stop_log(self) -> None:
        if self.logger is not None:
            self.logger.close()
            self.logger = None

    def _refresh_ports(self) -> None:
        ports = list_serial_ports()
        dpg.configure_item("port_combo", items=ports)
        if ports and dpg.get_value("port_combo") not in ports:
            dpg.set_value("port_combo", ports[0])

    def _pick_csv(self, _sender, app_data) -> None:
        selection = app_data.get("selections") or {}
        if not selection:
            return
        path = next(iter(selection.values()))
        dpg.set_value("replay_path", path)
        dpg.set_value("source_radio", "Replay")
        self._set_source("Replay")

    def _build(self) -> None:
        with dpg.window(tag="root", label="Sonar Theremin", no_title_bar=True):
            # Top bar
            with dpg.group(horizontal=True):
                dpg.add_text("SONAR THEREMIN", color=(255, 200, 90, 255))
                dpg.add_spacer(width=20)
                dpg.add_radio_button(
                    ("Simulator", "Serial", "Replay"),
                    horizontal=True,
                    tag="source_radio",
                    callback=lambda _s, a: self._set_source(a),
                )
                dpg.add_combo(items=list_serial_ports(), tag="port_combo", width=100)
                dpg.add_button(label="Connect", callback=lambda: (dpg.set_value("source_radio", "Serial"), self._set_source("Serial")))
                dpg.add_button(label="Refresh", callback=lambda: self._refresh_ports())
                dpg.add_spacer(width=12)
                dpg.add_checkbox(label="MUTE", tag="mute", default_value=False)
                dpg.add_spacer(width=8)
                dpg.add_button(label="Kytara", tag="btn_nastroj", callback=self._toggle_nastroj)
                dpg.add_button(label="Pistala", tag="btn_pistala", callback=self._toggle_pistala)
                dpg.add_checkbox(
                    label="Ohraniceni",
                    tag="snap_pick",
                    default_value=False,
                )
                dpg.add_text("Hlasitost")
                dpg.add_button(label="Ticho", tag="btn_vol_quiet", user_data=0.12, callback=self._set_master)
                dpg.add_button(label="Normal", tag="btn_vol_norm", user_data=0.4, callback=self._set_master)
                dpg.add_button(label="Nahlas", tag="btn_vol_loud", user_data=0.65, callback=self._set_master)

            # Hero banner
            dpg.add_spacer(height=6)
            dpg.add_text("Pripraven", tag="hero_banner", color=(255, 210, 100, 255))
            dpg.add_text("", tag="status", color=(150, 160, 170, 255))

            self._build_simon_panel()
            self._build_sound_strip()

            with dpg.group(horizontal=True):
                self._build_menu_column()
                self._build_antenna("LEVA RUKA - cm", "vol_draw", (90, 200, 160))
                self._build_antenna("PRAVA RUKA - noty", "pitch_draw", (255, 200, 90))
                self._build_now_playing()

            with dpg.collapsing_header(label="Grafy senzoru", default_open=False):
                with dpg.group(horizontal=True):
                    self._build_plot("Vyska (mm)", "pitch_x", "pitch_y", "s_pitch_raw", "s_pitch_filt")
                    self._build_plot("Hlasitost (mm)", "vol_x", "vol_y", "s_vol_raw", "s_vol_filt")

            with dpg.collapsing_header(label="Replay / zaznam", default_open=False):
                with dpg.group(horizontal=True):
                    dpg.add_input_text(tag="replay_path", hint="CSV pro replay", width=420)
                    dpg.add_button(label="Open CSV…", callback=lambda: dpg.show_item("csv_dialog"))
                    dpg.add_button(label="Record CSV", tag="record_btn", callback=lambda: self._toggle_log())

        with dpg.file_dialog(
            directory_selector=False,
            show=False,
            callback=self._pick_csv,
            tag="csv_dialog",
            width=720,
            height=420,
        ):
            dpg.add_file_extension(".csv", color=(150, 255, 180, 255))

        with dpg.handler_registry():
            dpg.add_key_press_handler(dpg.mvKey_Spacebar, callback=lambda: self._space(True))
            dpg.add_key_release_handler(dpg.mvKey_Spacebar, callback=lambda: self._space(False))
            dpg.add_key_press_handler(dpg.mvKey_M, callback=self._toggle_mute)

        self._sync_zone_combos()
        self._refresh_simon_lights()
        self._paint_scales(None)
        self._sync_choice_lights()

    def _set_master(self, _s=None, _a=None, user_data=0.4) -> None:
        self._ui["master_vol"] = float(user_data)
        self.synth.master = float(user_data)
        self._sync_choice_lights()

    def _toggle_nastroj(self) -> None:
        if self._nastroj:
            self._set_nastroj(False)
        else:
            self._set_nastroj(True)

    def _toggle_pistala(self) -> None:
        if self._pistala:
            self._set_pistala(False)
        else:
            self._set_pistala(True)

    def _snapshot_play_settings(self) -> dict[str, object]:
        return {
            "continuous": bool(dpg.get_value("continuous")) if dpg.does_item_exist("continuous") else False,
            "volume_enabled": bool(dpg.get_value("volume_enabled")) if dpg.does_item_exist("volume_enabled") else True,
            "filter_mode": str(dpg.get_value("filter_mode")) if dpg.does_item_exist("filter_mode") else "median_ema",
            "ema_alpha": float(self._ui["ema_alpha"]),
            "hold_missing": int(self._ui.get("hold_missing", 8)),
            "pitch_magnet": float(self._ui.get("pitch_magnet", 0.0)),
            "retrigger": bool(dpg.get_value("retrigger")) if dpg.does_item_exist("retrigger") else False,
            "timbre": str(dpg.get_value("timbre")) if dpg.does_item_exist("timbre") else self.synth.timbre,
            "jas": float(self._ui["jas"]),
        }

    def _restore_play_settings(self, prev: dict[str, object]) -> None:
        if dpg.does_item_exist("continuous"):
            dpg.set_value("continuous", bool(prev.get("continuous", False)))
        if dpg.does_item_exist("volume_enabled"):
            dpg.set_value("volume_enabled", bool(prev.get("volume_enabled", True)))
        if dpg.does_item_exist("retrigger"):
            dpg.set_value("retrigger", bool(prev.get("retrigger", False)))
        if dpg.does_item_exist("filter_mode"):
            dpg.set_value("filter_mode", str(prev.get("filter_mode", "median_ema")))
        self._ui["ema_alpha"] = float(prev.get("ema_alpha", 0.35))
        self._ui["hold_missing"] = int(prev.get("hold_missing", 8))
        self._ui["pitch_magnet"] = float(prev.get("pitch_magnet", 0.0))
        self._ui["filter_mode"] = str(prev.get("filter_mode", "median_ema"))
        self._ui["jas"] = float(prev.get("jas", 0.55))
        timbre = str(prev.get("timbre", "Pistala"))
        if dpg.does_item_exist("timbre"):
            dpg.set_value("timbre", timbre)
        self.synth.apply_timbre(timbre)

    def _set_nastroj(self, on: bool) -> None:
        if on and not self._nastroj:
            if self._pistala:
                self._set_pistala(False)
            self._before_nastroj = self._snapshot_play_settings()
            self._nastroj = True
            if dpg.does_item_exist("continuous"):
                dpg.set_value("continuous", False)
            if dpg.does_item_exist("volume_enabled"):
                dpg.set_value("volume_enabled", True)
            if dpg.does_item_exist("retrigger"):
                dpg.set_value("retrigger", True)
            if dpg.does_item_exist("filter_mode"):
                dpg.set_value("filter_mode", "median_ema")
            self._ui["ema_alpha"] = 0.4
            self._ui["hold_missing"] = 6
            self._ui["pitch_magnet"] = 0.0
            self._ui["filter_mode"] = "median_ema"
            self._ui["jas"] = 0.7
            self._ui["default_amp"] = 0.65
            if dpg.does_item_exist("scale"):
                dpg.set_value("scale", "Pentatonika C")
                self.pipeline.apply_scale("Pentatonika C")
            if dpg.does_item_exist("timbre"):
                dpg.set_value("timbre", "Kytara")
            self.synth.apply_timbre("Kytara")
            self.synth.guitar_active = True
            self.synth.flute_active = False
            self.cfg.guitar_mode = True
            self.cfg.flute_mode = False
            self.synth.pluck_test(220.0)
            self._status = "Kytara ON"
            if dpg.does_item_exist("hero_banner"):
                dpg.set_value("hero_banner", "Kytara TEST")
        elif not on and self._nastroj:
            self._nastroj = False
            self.cfg.guitar_mode = False
            self.synth.guitar_active = False
            self.synth.silence()
            prev = self._before_nastroj or {}
            self._before_nastroj = None
            self._restore_play_settings(prev)
            self._status = "Kytara vypnuta"
            if dpg.does_item_exist("hero_banner") and not self.simon.active:
                dpg.set_value("hero_banner", "Pripraven")
        self._sync_choice_lights()

    def _set_pistala(self, on: bool) -> None:
        if on and not self._pistala:
            if self._nastroj:
                self._set_nastroj(False)
            self._before_pistala = self._snapshot_play_settings()
            self._pistala = True
            if dpg.does_item_exist("continuous"):
                dpg.set_value("continuous", False)
            if dpg.does_item_exist("volume_enabled"):
                dpg.set_value("volume_enabled", True)
            if dpg.does_item_exist("retrigger"):
                dpg.set_value("retrigger", False)
            if dpg.does_item_exist("filter_mode"):
                dpg.set_value("filter_mode", "median_ema")
            self._ui["ema_alpha"] = 0.38
            self._ui["hold_missing"] = 6
            self._ui["pitch_magnet"] = 0.0
            self._ui["filter_mode"] = "median_ema"
            self._ui["jas"] = 0.6
            self._ui["default_amp"] = 0.5
            if dpg.does_item_exist("scale"):
                dpg.set_value("scale", "C dur")
                self.pipeline.apply_scale("C dur")
            if dpg.does_item_exist("timbre"):
                dpg.set_value("timbre", "Pistala")
            self.synth.apply_timbre("Pistala")
            self.synth.guitar_active = False
            self.synth.flute_active = True
            self.cfg.guitar_mode = False
            self.cfg.flute_mode = True
            self.synth.silence()
            self._status = "Pistala ON — prava prstoklad, leva fouka"
            if dpg.does_item_exist("hero_banner"):
                dpg.set_value("hero_banner", "Pistala — foukej")
        elif not on and self._pistala:
            self._pistala = False
            self.cfg.flute_mode = False
            self.synth.flute_active = False
            self.synth.silence()
            prev = self._before_pistala or {}
            self._before_pistala = None
            self._restore_play_settings(prev)
            self._status = "Pistala vypnuta"
            if dpg.does_item_exist("hero_banner") and not self.simon.active:
                dpg.set_value("hero_banner", "Pripraven")
        self._sync_choice_lights()

    def _force_discrete_for_game(self) -> None:
        """Simon / demo need note zones, not continuous glide."""
        if self._nastroj:
            self._set_nastroj(False)
        if self._pistala:
            self._set_pistala(False)
        if dpg.does_item_exist("continuous"):
            dpg.set_value("continuous", False)
        self._ui["pitch_magnet"] = 0.0

    def _set_hidden_int(self, _s=None, _a=None, user_data=None) -> None:
        if not user_data:
            return
        tag, value = user_data
        self._ui[tag] = value
        self._sync_choice_lights()

    def _nudge_ui(self, key: str, delta: float, lo: float, hi: float) -> None:
        self._ui[key] = min(hi, max(lo, float(self._ui[key]) + delta))
        self._sync_choice_lights()

    def _set_sim_mm(self, _s=None, _a=None, user_data=None) -> None:
        if not user_data:
            return
        tag, mm = user_data
        self._ui[tag] = float(mm)
        if tag == "sim_pitch":
            self.simulator.pitch_target_mm = float(mm)
            self._ui["sweep_pitch"] = False
            self.deck.stop_melody()
        else:
            self.simulator.volume_target_mm = float(mm)
            self._ui["sweep_volume"] = False
        self._sync_choice_lights()

    def _paint_scales(self, voice: Voice | None) -> None:
        if voice is None:
            voice = Voice(
                frequency_hz=0.0,
                amplitude=0.0,
                note_name=None,
                gate=False,
                retrigger=False,
                pitch_raw_mm=None,
                pitch_mm=None,
                volume_raw_mm=None,
                volume_mm=None,
                pitch_velocity_mm_s=None,
                strike=False,
                in_pitch_range=False,
                in_volume_range=False,
            )
        self._draw_antennas(voice)

    def _build_simon_panel(self) -> None:
        with dpg.child_window(height=118, border=True, tag="simon_panel"):
            with dpg.group(horizontal=True):
                dpg.add_text("SIMON RIKA", color=(255, 190, 80, 255))
                dpg.add_spacer(width=16)
                dpg.add_button(label="Start", callback=self._simon_start)
                dpg.add_button(label="Zopakuj", callback=self._simon_repeat)
                dpg.add_button(label="Stop", callback=self._simon_stop)
                dpg.add_spacer(width=12)
                dpg.add_text("Delka")
                for n in (2, 3, 4, 5):
                    dpg.add_button(label=str(n), tag=f"btn_slen_{n}", user_data=("simon_len", n), callback=self._set_hidden_int)
                dpg.add_spacer(width=8)
                dpg.add_text("Tolerance")
                dpg.add_button(label="Presne", tag="btn_stol_0", user_data=("simon_tol", 0), callback=self._set_hidden_int)
                dpg.add_button(label="Snadno", tag="btn_stol_2", user_data=("simon_tol", 2), callback=self._set_hidden_int)
                dpg.add_button(label="Volne", tag="btn_stol_3", user_data=("simon_tol", 3), callback=self._set_hidden_int)
                dpg.add_spacer(width=12)
                dpg.add_text("Serie 0x", tag="simon_streak", color=(140, 200, 160, 255))
            dpg.add_text("-", tag="simon_status", color=(180, 200, 180, 255))
            with dpg.group(horizontal=True, tag="simon_lights_row"):
                dpg.add_text("Cil:")
                for i in range(8):
                    dpg.add_button(label="-", tag=f"simon_light_{i}", width=52)

    def _refresh_simon_lights(self) -> None:
        notes = [n for n, _ in self.pipeline.mapper.notes]
        target = self.simon.target_note if self.simon.active else None
        lit = self.simon.lit_notes if self.simon.active else set()
        seq = " → ".join(self.simon.sequence) if self.simon.sequence else ""
        for i in range(8):
            tag = f"simon_light_{i}"
            if not dpg.does_item_exist(tag):
                continue
            if i < len(notes):
                name = notes[i]
                dpg.configure_item(tag, show=True)
                if name == target:
                    dpg.configure_item(tag, label=">" + name)
                    self._bind_choice(tag, True)
                elif name in lit:
                    dpg.configure_item(tag, label="*" + name)
                    self._bind_choice(tag, True)
                else:
                    dpg.configure_item(tag, label=name)
                    self._bind_choice(tag, False)
            else:
                dpg.configure_item(tag, show=False)
        if dpg.does_item_exist("simon_status") and seq and self.simon.phase == SimonGame.DEMO:
            pass

    def _build_sound_strip(self) -> None:
        with dpg.group(horizontal=True):
            dpg.add_text("Stupnice")
            dpg.add_combo(list(SCALE_NAMES), default_value="C dur", tag="scale", width=130)
            dpg.add_text("Zvuk")
            dpg.add_combo(list(VOICE_NAMES), default_value="Pistala", tag="timbre", width=110)
            for name in VOICE_NAMES[:5]:
                dpg.add_button(label=name, tag=f"btn_voice_{name}", user_data=name, callback=self._set_timbre)
            dpg.add_spacer(width=8)
            dpg.add_checkbox(label="Kick", tag="drum_kick")
            dpg.add_checkbox(label="Hat", tag="drum_hat")
            dpg.add_checkbox(label="Snare", tag="drum_snare")
            dpg.add_button(label="Beat", callback=self._enable_beat)
            dpg.add_button(label="Ticho beat", callback=self._silence_beat)
        # hidden zone editors for custom scale
        with dpg.group(horizontal=True, show=False):
            for i in range(self._zone_count):
                dpg.add_combo(list(NOTE_CHOICES), tag=f"zone_{i}", width=58)

    def _build_menu_column(self) -> None:
        with dpg.child_window(width=280, height=ANT_H + 42, border=True):
            with dpg.collapsing_header(label="Jak hrat", default_open=True):
                dpg.add_text(
                    "Kytara: prava ton, leva delka (bliz=umlc).\n"
                    "Pistala: prava prstoklad, leva FOUKA.\n"
                    "Ohraniceni: u vsech — machni = 1 ton.",
                    color=(160, 170, 180, 255),
                )
                dpg.add_button(label="Zapnout Kytaru", tag="btn_nastroj_menu", callback=self._toggle_nastroj, width=240)
                dpg.add_button(label="Zapnout Pistalu", tag="btn_pistala_menu", callback=self._toggle_pistala, width=240)
                dpg.add_text(
                    "Ohraniceni zapnes nahore vedle nastroju.",
                    color=(160, 170, 180, 255),
                )
                dpg.add_checkbox(label="Hlasitost druhou rukou", tag="volume_enabled", default_value=True)
                dpg.add_checkbox(label="Bliz = vyssi ton", tag="invert_pitch", default_value=False)
                dpg.add_checkbox(label="Dal = hlasiteji", tag="invert_volume", default_value=False)
                dpg.add_checkbox(label="Spojita vyska", tag="continuous", default_value=False)
                dpg.add_checkbox(label="Novy ton pri zmene", tag="retrigger", default_value=False)
                dpg.add_checkbox(label="Jen s mezernikem", tag="space_to_play", default_value=False)

            with dpg.collapsing_header(label="Demo melodie", default_open=False):
                for key, label in DEMO_BUTTONS:
                    dpg.add_button(label=label, user_data=key, callback=self._play_demo, width=240)
                dpg.add_button(label="Stop melodie", callback=self._stop_demo, width=240)

            with dpg.collapsing_header(label="Simulator (bez hardwaru)", default_open=False):
                dpg.add_text("Vyska ruky")
                with dpg.group(horizontal=True):
                    for mm in (100, 200, 300, 400, 500):
                        dpg.add_button(label=f"{mm}", tag=f"btn_sp_{mm}", user_data=("sim_pitch", mm), callback=self._set_sim_mm)
                dpg.add_text("Hlasitost ruky")
                with dpg.group(horizontal=True):
                    for mm in (80, 140, 220, 350, 480):
                        dpg.add_button(label=f"{mm}", tag=f"btn_sv_{mm}", user_data=("sim_volume", mm), callback=self._set_sim_mm)
                dpg.add_button(label="Rychly skok vysky", callback=self._flick_pitch)
                dpg.add_button(label="Prohodit ruce", callback=self._swap_hands)

            with dpg.collapsing_header(label="Pokrocile / filtr", default_open=False):
                dpg.add_combo(list(FILTER_MODES), default_value="median_ema", tag="filter_mode", width=160)
                dpg.add_text("Dosah: 8-50 cm (pevne)", color=(140, 150, 160, 255))
                dpg.add_button(label="Jas -", callback=lambda: self._nudge_ui("jas", -0.1, 0.0, 1.0))
                dpg.add_button(label="Jas +", callback=lambda: self._nudge_ui("jas", 0.1, 0.0, 1.0))
                for label, bpm in (("90 BPM", 90), ("108 BPM", 108), ("120 BPM", 120), ("140 BPM", 140)):
                    dpg.add_button(label=label, tag=f"btn_bpm_{bpm}", user_data=("bpm", bpm), callback=self._set_hidden_int)

            with dpg.collapsing_header(label="Diagnostika", default_open=False):
                _kv("Nota", "read_note")
                _kv("Gate", "read_gate")
                _kv("Vyska raw", "read_pitch_raw")
                _kv("Vyska filtr", "read_pitch_filt")
                _kv("Hlasitost raw", "read_vol_raw")
                _kv("Hlasitost filtr", "read_vol_filt")
                _kv("Amplituda", "read_amp")
                _kv("Snimku/s", "diag_rate")
                _kv("Jitter vysky", "diag_jitter_p")
                _kv("Jitter hlas.", "diag_jitter_v")
                _kv("Vypadky V/H", "diag_drop_p")
                dpg.add_text("", tag="read_strike", color=(255, 120, 80, 255))
                dpg.add_text("-", tag="diag_dt", show=False)
                dpg.add_text("-", tag="diag_drop_v", show=False)
                dpg.add_text("-", tag="diag_strikes", show=False)
                dpg.add_text("-", tag="diag_latency", show=False)
                dpg.add_text("-", tag="diag_log", show=False)

    def _build_antenna(self, title: str, draw_tag: str, tint: tuple[int, int, int]) -> None:
        with dpg.child_window(
            width=ANT_W + 24,
            height=ANT_H + 42,
            border=True,
            no_scrollbar=True,
            no_scroll_with_mouse=True,
        ):
            dpg.add_text(title, color=(*tint, 255))
            dpg.add_drawlist(width=ANT_W, height=ANT_H, tag=draw_tag)

    def _build_now_playing(self) -> None:
        with dpg.child_window(
            width=220,
            height=ANT_H + 42,
            border=True,
            no_scrollbar=True,
            no_scroll_with_mouse=True,
        ):
            dpg.add_text("TED")
            dpg.add_text("-", tag="now_note_big", color=(255, 220, 120, 255))
            dpg.add_spacer(height=8)
            dpg.add_text("Gate")
            dpg.add_text("off", tag="now_gate", color=(160, 180, 170, 255))
            dpg.add_spacer(height=12)
            dpg.add_text("Simon cil")
            dpg.add_text("-", tag="now_simon_target", color=(255, 180, 70, 255))
            dpg.add_spacer(height=16)
            dpg.add_text("M = mute\nMezernik = gate", color=(120, 130, 140, 255))

    def _simon_repeat(self) -> None:
        self.deck.stop_melody()
        self.simon.repeat(time.perf_counter())
        self._status = self.simon.status
        if dpg.does_item_exist("simon_status"):
            dpg.set_value("simon_status", self.simon.status)
        if dpg.does_item_exist("hero_banner"):
            dpg.set_value("hero_banner", self.simon.banner or self.simon.status)

    def _space(self, down: bool) -> None:
        self.cfg.space_down = down

    def _toggle_mute(self) -> None:
        if dpg.does_item_exist("mute"):
            dpg.set_value("mute", not dpg.get_value("mute"))

    def _melody_volume_mm(self, vel: float) -> float:
        cfg = self.cfg
        if vel <= 0.02:
            if cfg.invert_volume:
                return max(MM_LO, cfg.volume_min_mm - 20.0)
            return cfg.volume_max_mm + 40.0
        t = 1.0 - vel
        if cfg.invert_volume:
            t = vel
        return cfg.volume_min_mm + t * (cfg.volume_max_mm - cfg.volume_min_mm)

    def _play_demo(self, _sender=None, _app_data=None, name: str | None = None) -> None:
        if name not in MELODY_SETUP:
            return
        self._force_discrete_for_game()
        if dpg.does_item_exist("source_radio"):
            self._source_before_demo = str(dpg.get_value("source_radio") or "Serial")
        else:
            self._source_before_demo = "Serial"
        dpg.set_value("source_radio", "Simulator")
        self._set_source("Simulator")
        self._ui["sweep_pitch"] = False
        self.simulator.sweep_pitch = False
        dpg.set_value("volume_enabled", True)
        self._ui["sim_volume"] = 120.0
        self.simulator.volume_target_mm = 120.0
        dpg.set_value("retrigger", False)
        dpg.set_value("continuous", False)
        setup = MELODY_SETUP[name]
        scale = setup.get("scale")
        if scale:
            dpg.set_value("scale", scale)
            self.pipeline.apply_scale(scale)
        bpm = setup.get("bpm")
        if bpm:
            self._ui["bpm"] = int(bpm)
        voice = setup.get("voice")
        if voice:
            dpg.set_value("timbre", voice)
            self.synth.apply_timbre(voice)
        self.deck.note_mm = note_positions(
            self.pipeline.mapper.notes,
            self.cfg.pitch_min_mm,
            self.cfg.pitch_max_mm,
            self.cfg.invert_pitch,
        )
        self.deck.start_melody(name)
        self._sync_zone_combos()
        title = setup.get("title", name)
        colour = voice or self.synth.timbre
        self._status = f"Demo: {title}. Zvuk {colour}. Stupnici i zvuk muzes zmenit rucne."

    def _stop_demo(self) -> None:
        self.deck.stop_melody()
        self.synth.silence()
        self._last_voice = None
        # Drop leftover demo pitch so it cannot keep singing.
        self._ui["sweep_pitch"] = False
        self._ui["sweep_volume"] = False
        self.simulator.sweep_pitch = False
        self.simulator.sweep_volume = False
        self._ui["sim_pitch"] = 200.0
        # Out of volume range => gate off until hands move again.
        quiet = float(self.cfg.volume_max_mm) + 60.0
        self._ui["sim_volume"] = quiet
        self.simulator.pitch_target_mm = 200.0
        self.simulator.volume_target_mm = quiet
        self.pipeline.reset()

        prev = self._source_before_demo or "Serial"
        self._source_before_demo = None
        ports = list_serial_ports()
        port = dpg.get_value("port_combo") if dpg.does_item_exist("port_combo") else ""
        want_serial = prev == "Serial" and bool(ports) and (port in ports or DEFAULT_PORT in ports)
        if want_serial:
            if not port or port not in ports:
                dpg.set_value("port_combo", DEFAULT_PORT if DEFAULT_PORT in ports else ports[0])
            dpg.set_value("source_radio", "Serial")
            self._set_source("Serial")
            self._status = "Melodie vypnuta - zpet na senzory"
        else:
            dpg.set_value("source_radio", "Simulator")
            self._set_source("Simulator")
            self._status = "Melodie vypnuta - rucni ovladani"
        self.synth.silence()
        if dpg.does_item_exist("hero_banner"):
            dpg.set_value("hero_banner", "Pripraven")

    def _simon_start(self) -> None:
        self.deck.stop_melody()
        self._force_discrete_for_game()
        self.simon.cfg.length = int(self._ui["simon_len"])
        self.simon.cfg.tolerance = int(self._ui["simon_tol"])
        notes = [name for name, _ in self.pipeline.mapper.notes]
        self.simon.start(notes, time.perf_counter(), self.deck.note_mm)
        self._status = self.simon.status
        if dpg.does_item_exist("simon_status"):
            dpg.set_value("simon_status", self.simon.status)
        if dpg.does_item_exist("hero_banner"):
            dpg.set_value("hero_banner", self.simon.banner or self.simon.status)

    def _simon_stop(self) -> None:
        self.simon.stop()
        self.synth.silence()
        self._status = "Simon vypnuty"
        if dpg.does_item_exist("simon_status"):
            dpg.set_value("simon_status", self.simon.status)
        if dpg.does_item_exist("hero_banner"):
            dpg.set_value("hero_banner", "Pripraven")
        if dpg.does_item_exist("simon_streak"):
            dpg.set_value("simon_streak", f"Serie {self.simon.streak}x")

    def _enable_beat(self) -> None:
        dpg.set_value("drum_kick", True)
        dpg.set_value("drum_hat", True)
        dpg.set_value("drum_snare", True)
        self._status = "Beat bezi. Theremin hraje pres nej."

    def _silence_beat(self) -> None:
        dpg.set_value("drum_kick", False)
        dpg.set_value("drum_hat", False)
        dpg.set_value("drum_snare", False)
        self._status = "Beat vypnuty"

    def _set_timbre(self, _sender=None, _app_data=None, name: str | None = None) -> None:
        if not name or name not in VOICE_NAMES:
            return
        dpg.set_value("timbre", name)
        self.synth.apply_timbre(name)
        self._status = f"Zvuk: {name}"
        self._sync_choice_lights()

    def _visible_zone_names(self) -> tuple[str, ...]:
        n = len(self.pipeline.mapper.notes)
        names: list[str] = []
        for i in range(n):
            tag = f"zone_{i}"
            if not dpg.does_item_exist(tag):
                return ()
            value = dpg.get_value(tag)
            if not value:
                return ()
            names.append(str(value))
        return tuple(names)

    def _sync_zone_combos(self) -> None:
        if not dpg.does_item_exist("zone_0"):
            return
        notes = self.pipeline.mapper.notes
        n = len(notes)
        for i in range(self._zone_count):
            tag = f"zone_{i}"
            if i < n:
                dpg.configure_item(tag, show=True)
                dpg.set_value(tag, notes[i][0])
            else:
                dpg.configure_item(tag, show=False)

    def _poll_zone_edits(self) -> None:
        names = self._visible_zone_names()
        current = tuple(note for note, _hz in self.pipeline.mapper.notes)
        if not names or names == current:
            return
        self.pipeline.apply_custom(names)
        dpg.set_value("scale", "Custom")
        self._status = "Stupnice: Custom. Zony jdou menit i behem hry."

    def _flick_pitch(self) -> None:
        self._ui["sweep_pitch"] = False
        self.simulator.sweep_pitch = False
        self.deck.stop_melody()
        self.simulator.flick_pitch()
        self._ui["sim_pitch"] = self.simulator.pitch_target_mm

    def _swap_hands(self) -> None:
        self.cfg.pitch_channel, self.cfg.volume_channel = self.cfg.volume_channel, self.cfg.pitch_channel
        self._status = f"Pitch=ch{self.cfg.pitch_channel}  Volume=ch{self.cfg.volume_channel}"

    def _stop_sweep_on_drag(self, sender, _app_data) -> None:
        if sender == "sim_pitch":
            self._ui["sweep_pitch"] = False
            self.simulator.sweep_pitch = False
            self.deck.stop_melody()
        if sender == "sim_volume":
            self._ui["sweep_volume"] = False
            self.simulator.sweep_volume = False

    def _build_plot(self, title: str, x_tag: str, y_tag: str, raw_tag: str, filt_tag: str) -> None:
        with dpg.plot(label=title, height=180, width=PLOT_W):
            dpg.add_plot_legend()
            dpg.add_plot_axis(dpg.mvXAxis, label="s", tag=x_tag)
            with dpg.plot_axis(dpg.mvYAxis, label="mm", tag=y_tag):
                dpg.add_line_series([], [], label="raw", tag=raw_tag)
                dpg.add_line_series([], [], label="filtered", tag=filt_tag)
            dpg.set_axis_limits(y_tag, 0, 450)

    def _theme(self) -> None:
        with dpg.theme(tag="app_theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 16, 14)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 10, 6)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 10, 8)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 8)
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 10)
                dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 6)
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (12, 14, 18, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (20, 24, 30, 255))
                dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (24, 28, 34, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (232, 236, 240, 255))
                dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, (120, 128, 136, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Button, (36, 48, 56, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (70, 90, 100, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (255, 140, 40, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Header, (36, 48, 56, 255))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (48, 64, 74, 255))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (255, 160, 50, 200))
                dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (12, 14, 18, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (28, 34, 42, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (50, 62, 74, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (255, 170, 50, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Border, (40, 48, 56, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Separator, (48, 56, 64, 255))
                dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (20, 24, 30, 255))
            with dpg.theme_component(dpg.mvCheckbox):
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (40, 50, 60, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (70, 90, 100, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (255, 180, 60, 255))
                dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (20, 18, 12, 255))
            with dpg.theme_component(dpg.mvRadioButton):
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (255, 170, 50, 255))
                dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (20, 18, 12, 255))

        with dpg.theme(tag="btn_on"):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (255, 175, 55, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (255, 195, 90, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (255, 150, 40, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (18, 16, 12, 255))

        with dpg.theme(tag="btn_off"):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (36, 48, 56, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (55, 70, 82, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (70, 90, 100, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (210, 215, 220, 255))

    def _bind_choice(self, tag: str, active: bool) -> None:
        if dpg.does_item_exist(tag):
            dpg.bind_item_theme(tag, "btn_on" if active else "btn_off")

    def _sync_choice_lights(self) -> None:
        vol = float(self._ui["master_vol"])
        for tag, val in (("btn_vol_quiet", 0.12), ("btn_vol_norm", 0.4), ("btn_vol_loud", 0.65)):
            self._bind_choice(tag, abs(vol - val) < 0.02)
        self._bind_choice("btn_nastroj", self._nastroj)
        self._bind_choice("btn_nastroj_menu", self._nastroj)
        self._bind_choice("btn_pistala", self._pistala)
        self._bind_choice("btn_pistala_menu", self._pistala)
        length = int(self._ui["simon_len"])
        for n in (2, 3, 4, 5):
            self._bind_choice(f"btn_slen_{n}", length == n)
        tol = int(self._ui["simon_tol"])
        for tag, v in (("btn_stol_0", 0), ("btn_stol_2", 2), ("btn_stol_3", 3)):
            self._bind_choice(tag, tol == v)
        bpm = int(self._ui["bpm"])
        for b in (90, 108, 120, 140):
            self._bind_choice(f"btn_bpm_{b}", bpm == b)
        pitch = float(self._ui["sim_pitch"])
        for mm in (100, 200, 300, 400, 500):
            self._bind_choice(f"btn_sp_{mm}", abs(pitch - mm) < 1)
        svol = float(self._ui["sim_volume"])
        for mm in (80, 140, 220, 350, 480):
            self._bind_choice(f"btn_sv_{mm}", abs(svol - mm) < 1)
        timbre = self.synth.timbre
        for name in VOICE_NAMES:
            tag = f"btn_voice_{name}"
            self._bind_choice(tag, name == timbre)


def _tip(item: int | str, text: str) -> None:
    with dpg.tooltip(item):
        dpg.add_text(text, wrap=320)


def _hint(text: str) -> None:
    dpg.add_text(text, wrap=280, color=(130, 140, 148, 255))


def _kv(label: str, tag: str, tip: str | None = None) -> None:
    with dpg.group(horizontal=True):
        caption = dpg.add_text(f"{label:<18}", color=(150, 158, 166, 255))
        dpg.add_text("-", tag=tag)
    if tip:
        _tip(caption, tip)
        _tip(tag, tip)


def _mm(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f} mm"


def _mm_to_y(mm: float) -> float:
    t = (mm - MM_LO) / (MM_HI - MM_LO)
    t = min(1.0, max(0.0, t))
    pad = 28.0
    return pad + (1.0 - t) * (ANT_H - 2 * pad)


def main() -> None:
    App().run()
