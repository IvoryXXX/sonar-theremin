from __future__ import annotations

import time
from collections import deque
from datetime import datetime
from pathlib import Path

import dearpygui.dearpygui as dpg

from theremin.logger import CsvLogger
from theremin.pipeline import FILTER_MODES, NOTE_CHOICES, SCALE_NAMES, Pipeline, RuntimeConfig, note_positions
from theremin.deck import Deck
from theremin.songs import DEMO_BUTTONS, DEMO_TIPS, MELODY_SETUP
from theremin.sources import ReplaySource, SerialSource, SimulatorSource, list_serial_ports
from theremin.synth import VOICE_NAMES, Synth
from theremin.types import SensorFrame, Voice

MM_LO = 40.0
MM_HI = 420.0
ANT_W, ANT_H = 168, 420
HISTORY = 240
PLOT_W = 520


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
        self.logger: CsvLogger | None = None
        self._t0 = time.perf_counter()
        self._hist_t: deque[float] = deque(maxlen=HISTORY)
        self._hist_pr: deque[float] = deque(maxlen=HISTORY)
        self._hist_pf: deque[float] = deque(maxlen=HISTORY)
        self._hist_vr: deque[float] = deque(maxlen=HISTORY)
        self._hist_vf: deque[float] = deque(maxlen=HISTORY)
        self._last_voice: Voice | None = None
        self._status = "Simulator ready"
        self._zone_count = 10

    def run(self) -> None:
        self.simulator.start()
        self.synth.start()
        if self.synth.error:
            self._status = f"Audio: {self.synth.error}"

        dpg.create_context()
        self._theme()
        self._build()
        dpg.create_viewport(title="Sonar Theremin", width=1320, height=1040)
        dpg.setup_dearpygui()
        dpg.bind_theme("app_theme")
        dpg.set_primary_window("root", True)
        dpg.show_viewport()

        try:
            while dpg.is_dearpygui_running():
                self._tick()
                dpg.render_dearpygui_frame()
        finally:
            self._shutdown()
            dpg.destroy_context()

    def _shutdown(self) -> None:
        self._stop_log()
        self.source.stop()
        self.serial.stop()
        self.synth.stop()

    def _tick(self) -> None:
        self._read_controls()
        events = self.deck.update(time.perf_counter())
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
                dpg.set_value("sim_pitch", self.deck.pitch_mm)
            vol_mm = self._melody_volume_mm(self.deck.velocity)
            self.simulator.volume_target_mm = vol_mm
            dpg.set_value("sim_volume", min(max(vol_mm, MM_LO), MM_HI))
            if self.deck.snap:
                self.pipeline.snap_to(self.deck.pitch_mm, vol_mm, self.deck.current_note)
        frame = self.source.poll()
        if self.simulator.sweep_pitch:
            dpg.set_value("sim_pitch", self.simulator.pitch_target_mm)
        if self.simulator.sweep_volume:
            dpg.set_value("sim_volume", self.simulator.volume_target_mm)
        if frame is not None:
            voice = self.pipeline.push(frame)
            self._last_voice = voice
            self.synth.apply_voice(voice)
            if self.logger is not None:
                self.logger.write(frame, voice)
            self._push_history(frame, voice)
            self._draw_antennas(voice)
            self._update_plots()
        elif self._last_voice is not None:
            self._draw_antennas(self._last_voice)
        self._update_readouts()

    def _read_controls(self) -> None:
        if not dpg.does_item_exist("filter_mode"):
            return
        self.cfg.filter_mode = dpg.get_value("filter_mode")
        self.cfg.ema_alpha = dpg.get_value("ema_alpha")
        self.cfg.hysteresis_mm = dpg.get_value("hysteresis")
        self.cfg.invert_pitch = dpg.get_value("invert_pitch")
        self.cfg.invert_volume = dpg.get_value("invert_volume")
        self.cfg.continuous_pitch = dpg.get_value("continuous")
        self.cfg.retrigger = dpg.get_value("retrigger")
        self.cfg.volume_enabled = dpg.get_value("volume_enabled")
        self.cfg.space_to_play = dpg.get_value("space_to_play")
        self.cfg.muted = dpg.get_value("mute")
        self.pipeline.sync_filters()

        self.simulator.jitter_mm = dpg.get_value("sim_jitter")
        self.simulator.dropout = dpg.get_value("sim_dropout")
        self.simulator.rate_hz = dpg.get_value("sim_rate")
        self.simulator.sweep_pitch = dpg.get_value("sweep_pitch")
        self.simulator.sweep_volume = dpg.get_value("sweep_volume")
        if not self.simulator.sweep_pitch and self.deck.melody_id is None:
            self.simulator.pitch_target_mm = dpg.get_value("sim_pitch")
        if not self.simulator.sweep_volume and self.deck.melody_id is None:
            self.simulator.volume_target_mm = dpg.get_value("sim_volume")

        if dpg.does_item_exist("timbre"):
            name = dpg.get_value("timbre")
            if name != self.synth.timbre:
                self.synth.apply_timbre(name)
            self.synth.brightness = float(dpg.get_value("jas"))
        self.synth.muted = dpg.get_value("mute")
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
        if dpg.does_item_exist("bpm"):
            self.deck.bpm = float(dpg.get_value("bpm"))
            self.deck.kick = bool(dpg.get_value("drum_kick"))
            self.deck.hat = bool(dpg.get_value("drum_hat"))
            self.deck.snare = bool(dpg.get_value("drum_snare"))
            self.synth.drum_gain = float(dpg.get_value("drum_gain"))

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
        dpg.set_value("read_note", v.note_name if v and v.note_name else "-")
        dpg.set_value("read_pitch_raw", _mm(v.pitch_raw_mm if v else None))
        dpg.set_value("read_pitch_filt", _mm(v.pitch_mm if v else None))
        dpg.set_value("read_vol_raw", _mm(v.volume_raw_mm if v else None))
        dpg.set_value("read_vol_filt", _mm(v.volume_mm if v else None))
        amp = v.amplitude if v else 0.0
        dpg.set_value("read_amp", f"{amp:.2f}")
        gate = "ON" if v and v.gate else "off"
        dpg.set_value("read_gate", gate)
        dpg.set_value("diag_rate", f"{st.rate_hz:.1f} Hz")
        dpg.set_value("diag_dt", f"{st.last_dt_s * 1000:.1f} ms")
        dpg.set_value("diag_jitter_p", f"{st.pitch_jitter_mm:.1f} mm")
        dpg.set_value("diag_jitter_v", f"{st.volume_jitter_mm:.1f} mm")
        dpg.set_value("diag_drop_p", str(st.invalid_pitch))
        dpg.set_value("diag_drop_v", str(st.invalid_volume))
        dpg.set_value("diag_strikes", str(st.strikes))
        audio_ms = 256 / 48.0
        dpg.set_value("diag_latency", f"~{audio_ms:.1f} ms audio buf")
        log = f"recording {self.logger.rows} rows" if self.logger else "idle"
        dpg.set_value("diag_log", log)
        extra = self.serial.error or self.replay.error
        dpg.set_value("status", extra or self._status)
        if v and v.strike:
            dpg.set_value("read_strike", "STRIKE")
        else:
            dpg.set_value("read_strike", "")

    def _draw_antennas(self, voice: Voice) -> None:
        self._draw_volume(voice)
        self._draw_pitch(voice)

    def _draw_volume(self, voice: Voice) -> None:
        parent = "vol_draw"
        if not dpg.does_item_exist(parent):
            return
        dpg.delete_item(parent, children_only=True)
        dpg.draw_rectangle((0, 0), (ANT_W, ANT_H), fill=(18, 22, 26, 255), color=(18, 22, 26, 255), parent=parent)
        lo, hi = self.cfg.volume_min_mm, self.cfg.volume_max_mm
        y0, y1 = _mm_to_y(hi), _mm_to_y(lo)
        # Gradient bands: closer (bottom) louder unless inverted.
        bands = 12
        for i in range(bands):
            fa = i / bands
            fb = (i + 1) / bands
            ya = y0 + (y1 - y0) * fa
            yb = y0 + (y1 - y0) * fb
            loud = fb if self.cfg.invert_volume else (1.0 - fa)
            fill = (20, int(40 + 90 * loud), int(48 + 80 * loud), 255)
            dpg.draw_rectangle((28, ya), (ANT_W - 28, yb), fill=fill, color=fill, parent=parent)
        dpg.draw_text((36, 8), "dal", size=13, color=(170, 190, 180, 255), parent=parent)
        dpg.draw_text(
            (32, ANT_H - 22),
            "bliz = hlasiteji" if not self.cfg.invert_volume else "bliz = tiseji",
            size=11,
            color=(140, 160, 150, 255),
            parent=parent,
        )
        self._marker(parent, voice.volume_raw_mm, (120, 160, 150, 160), 5)
        self._marker(parent, voice.volume_mm, (120, 255, 190, 255), 9)

    def _draw_pitch(self, voice: Voice) -> None:
        parent = "pitch_draw"
        if not dpg.does_item_exist(parent):
            return
        dpg.delete_item(parent, children_only=True)
        dpg.draw_rectangle((0, 0), (ANT_W, ANT_H), fill=(18, 22, 26, 255), color=(18, 22, 26, 255), parent=parent)
        n = len(self.pipeline.mapper.notes)
        lo, hi = self.cfg.pitch_min_mm, self.cfg.pitch_max_mm
        palette = [
            (70, 90, 140),
            (70, 110, 150),
            (60, 130, 140),
            (60, 140, 110),
            (90, 140, 90),
            (130, 140, 80),
            (150, 120, 70),
            (150, 100, 90),
            (130, 90, 120),
            (90, 90, 140),
        ]
        label_size = 13 if n <= 6 else 11 if n <= 8 else 10
        for i, (name, _) in enumerate(self.pipeline.mapper.notes):
            if self.cfg.invert_pitch:
                a, b = (n - i - 1) / n, (n - i) / n
            else:
                a, b = i / n, (i + 1) / n
            d0 = lo + a * (hi - lo)
            d1 = lo + b * (hi - lo)
            ya, yb = _mm_to_y(d1), _mm_to_y(d0)
            active = voice.note_name == name and voice.in_pitch_range
            col = palette[i % len(palette)]
            fill = (*col, 230 if active else 120)
            dpg.draw_rectangle((28, ya), (ANT_W - 28, yb), fill=fill, color=(10, 12, 16, 255), parent=parent)
            dpg.draw_text((36, (ya + yb) * 0.5 - 7), name, size=label_size, color=(240, 240, 230, 255) if active else (200, 205, 210, 180), parent=parent)
        dpg.draw_text(
            (36, 8),
            "dal = vys" if not self.cfg.invert_pitch else "dal = niz",
            size=13,
            color=(180, 190, 210, 255),
            parent=parent,
        )
        self._marker(parent, voice.pitch_raw_mm, (200, 180, 120, 150), 5)
        self._marker(parent, voice.pitch_mm, (255, 220, 90, 255), 9)

    def _marker(self, parent: str, mm: float | None, color: tuple[int, int, int, int], radius: int) -> None:
        if mm is None:
            return
        y = _mm_to_y(mm)
        dpg.draw_circle((ANT_W * 0.5, y), radius, color=color, fill=color, parent=parent)
        dpg.draw_line((18, y), (ANT_W - 18, y), color=color, thickness=1, parent=parent)

    def _set_source(self, label: str) -> None:
        self.source.stop()
        if label == "Serial":
            self.serial.port = dpg.get_value("port_combo")
            self.serial.baud = int(dpg.get_value("baud"))
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
            with dpg.group(horizontal=True):
                dpg.add_text("SONAR THEREMIN")
                dpg.add_spacer(width=24)
                dpg.add_radio_button(
                    ("Simulator", "Serial", "Replay"),
                    horizontal=True,
                    tag="source_radio",
                    callback=lambda _s, a: self._set_source(a),
                )
                _tip(
                    "source_radio",
                    "Odkud bereme vzdalenosti rukou.\n"
                    "Simulator - hrajes slidery, bez hardwaru.\n"
                    "Serial - ziva data z Arduina/ESP32 pres COM.\n"
                    "Replay - prehraje ulozeny CSV zaznam.",
                )
                dpg.add_spacer(width=16)
                dpg.add_combo(items=list_serial_ports(), tag="port_combo", width=110, label="COM")
                _tip("port_combo", "Seriovy port, na kterem visi mikrokontroler.")
                dpg.add_input_int(tag="baud", default_value=115200, width=90, label="baud")
                _tip("baud", "Rychlost seriove linky. Musi sedet s firmwarem (115200).")
                refresh = dpg.add_button(label="Refresh", callback=lambda: self._refresh_ports())
                _tip(refresh, "Znovu nacte seznam COM portu. Zastrc Arduino a klikni.")
                connect = dpg.add_button(
                    label="Connect",
                    callback=lambda: (dpg.set_value("source_radio", "Serial"), self._set_source("Serial")),
                )
                _tip(connect, "Otevre vybrany COM port a zacne cist vzdalenosti.")
                dpg.add_spacer(width=12)
                dpg.add_button(label="Record CSV", tag="record_btn", callback=lambda: self._toggle_log())
                _tip("record_btn", "Uklada surova i filtrovana data do logs/. Pak jde poustet pres Replay.")
                dpg.add_checkbox(label="MUTE", tag="mute", default_value=False)
                _tip("mute", "Okamzite ztlumi zvuk. Klavesa M dela totez.")
            _hint(
                "Dve ruce jako theremin: leva lista = hlasitost, prava = vyska tonu. "
                "Slider vedle listy je ruka nad senzorem - nahore dal, dole bliz. "
                "Najetim na ovladaci prvek se ukaze napoveda."
            )
            with dpg.group(horizontal=True):
                dpg.add_input_text(tag="replay_path", hint="cesta k CSV pro replay", width=480)
                _tip("replay_path", "Soubor z Record CSV. Po vyberu se prepne zdroj na Replay.")
                open_csv = dpg.add_button(label="Open CSV...", callback=lambda: dpg.show_item("csv_dialog"))
                _tip(open_csv, "Vybere ulozeny zaznam a prehraje ho stejnym zpracovanim.")
            dpg.add_text("", tag="status")
            self._build_deck()
            dpg.add_separator()

            with dpg.group(horizontal=True):
                self._build_controls()
                self._build_antenna(
                    "HLASITOST  (leva ruka)",
                    "vol_draw",
                    "sim_volume",
                    140.0,
                    (90, 180, 150),
                    "Leva ruka nad volume senzorem. Bliz k senzoru (dole) = hlasiteji, "
                    "dal nebo ruka pryc (nahore) = ticho.",
                )
                self._build_antenna(
                    "VYSKA  (prava ruka)",
                    "pitch_draw",
                    "sim_pitch",
                    200.0,
                    (230, 200, 90),
                    "Prava ruka nad pitch senzorem. Nahore = dal = vyssi ton. "
                    "Barevne pruhy jsou hudebni zony podle stupnice (C dur ma F i B).",
                )
                self._build_readouts()

            dpg.add_separator()
            with dpg.group(horizontal=True):
                self._build_plot("Vyska (mm)", "pitch_x", "pitch_y", "s_pitch_raw", "s_pitch_filt")
                self._build_plot("Hlasitost (mm)", "vol_x", "vol_y", "s_vol_raw", "s_vol_filt")
            _hint("Grafy: raw skace, filtered je to, z ceho se hraje. Kdyz filtered zaostava, filtr je moc silny.")

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
        dpg.set_value("source_radio", "Simulator")
        self._set_source("Simulator")
        dpg.set_value("sweep_pitch", False)
        self.simulator.sweep_pitch = False
        dpg.set_value("volume_enabled", True)
        dpg.set_value("sim_volume", 120.0)
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
            dpg.set_value("bpm", int(bpm))
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
        self._status = "Melodie zastavena"

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
        self._status = f"Zvuk: {name}. Hraj slidery, demo k tomu neni potreba."

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

    def _build_deck(self) -> None:
        with dpg.child_window(height=248, border=True):
            dpg.add_text("DECK")
            _hint(
                "Demo zapne stupnici i zvuk. Oboji muzes kdykoli zmenit rucne: stupnice v combu, "
                "zvuk tlacitky dole. Treti sonar (dve stupnice vedle sebe) je az na hardware."
            )
            with dpg.group(horizontal=True):
                dpg.add_text("Melodie")
                for key, label in DEMO_BUTTONS[:8]:
                    btn = dpg.add_button(label=label, user_data=key, callback=self._play_demo)
                    _tip(btn, DEMO_TIPS.get(key, label))
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=52)
                for key, label in DEMO_BUTTONS[8:]:
                    btn = dpg.add_button(label=label, user_data=key, callback=self._play_demo)
                    _tip(btn, DEMO_TIPS.get(key, label))
                stop_m = dpg.add_button(label="Stop", callback=self._stop_demo)
                _tip(stop_m, "Vrati vysku zpatky na slider.")
            with dpg.group(horizontal=True):
                dpg.add_text("Stupnice")
                dpg.add_combo(list(SCALE_NAMES), default_value="C dur", tag="scale", width=128)
                _tip(
                    "scale",
                    "Preset zony. Demo sem prepne samo. Custom = rucne upravene zony. "
                    "Pentatonika 6, C dur 8, C dur siroky 10, A/D moll 8.",
                )
                dpg.add_text("Zony")
                for i in range(self._zone_count):
                    dpg.add_combo(
                        list(NOTE_CHOICES),
                        tag=f"zone_{i}",
                        width=58,
                    )
                _tip("zone_0", "Kazdy pruh na prave liste je jedna zona / nota. Uprav a stupnice se prejmenuje na Custom.")
            with dpg.group(horizontal=True):
                dpg.add_text("ZVUK")
                dpg.add_combo(list(VOICE_NAMES), default_value="Pistala", tag="timbre", width=118)
                _tip(
                    "timbre",
                    "Prepni kdykoli, i bez dema. Stejne noty, jiny nastroj. "
                    "Pistala jemna, Varhany pisaly, 8-bit Tetris, Plocha hladka, "
                    "Sci-fi bzucak, Fanfara lesk, Bass tlusty, Brnk kratke.",
                )
                for name in VOICE_NAMES:
                    dpg.add_button(label=name, user_data=name, callback=self._set_timbre)
                dpg.add_slider_float(
                    label="Jas",
                    tag="jas",
                    default_value=0.55,
                    min_value=0.0,
                    max_value=1.0,
                    width=130,
                )
                _tip("jas", "Filtr. Doleva tmavsi, doprava ostrejsi. Funguje u vsech zvuku.")
            with dpg.group(horizontal=True):
                dpg.add_text("Rytmus")
                dpg.add_checkbox(label="Kick", tag="drum_kick")
                _tip("drum_kick", "Kopak na dobu.")
                dpg.add_checkbox(label="Hat", tag="drum_hat")
                _tip("drum_hat", "Hi-hat osminky.")
                dpg.add_checkbox(label="Snare", tag="drum_snare")
                _tip("drum_snare", "Snare na 2 a 4.")
                beat = dpg.add_button(label="Zapnout beat", callback=self._enable_beat)
                _tip(beat, "Kick + hat + snare najednou.")
                quiet = dpg.add_button(label="Ticho beat", callback=self._silence_beat)
                _tip(quiet, "Vypne vsechny bubny.")
                dpg.add_slider_int(label="BPM", tag="bpm", default_value=108, min_value=70, max_value=160, width=140)
                _tip("bpm", "Tempo beatu i demo melodie.")
                dpg.add_spacer(width=12)
                dpg.add_slider_float(
                    label="Drums",
                    tag="drum_gain",
                    default_value=0.7,
                    min_value=0.0,
                    max_value=1.2,
                    width=130,
                )
                _tip("drum_gain", "Hlasitost kick/hat/snare proti thereminu.")
        self._sync_zone_combos()

    def _build_controls(self) -> None:
        with dpg.child_window(width=300, height=ANT_H + 36, border=True):
            dpg.add_text("Jak to hraje")
            _hint("Ton zni, kdyz je vyska v zone a leva ruka drzi hlasitost. Ruka pryc od hlasitosti = ticho.")
            dpg.add_checkbox(label="Hlasitost druhou rukou", tag="volume_enabled", default_value=True)
            _tip("volume_enabled", "Vypni, pokud chces ladit jen vysku - hlasitost pak zustane naplno.")
            dpg.add_checkbox(label="Bliz = vyssi ton", tag="invert_pitch", default_value=False)
            _tip("invert_pitch", "Default je ruka vys (dal od senzoru) = vyssi ton. Zapni, pokud ma byt naopak jako u klasickeho thereminu.")
            dpg.add_checkbox(label="Dal = hlasiteji", tag="invert_volume", default_value=False)
            _tip("invert_volume", "Default: bliz k senzoru = hlasiteji, ruka pryc = ticho. Zapni pro klasicky theremin (bliz = tiseji).")
            dpg.add_checkbox(label="Spojita vyska (theremin)", tag="continuous", default_value=False)
            _tip("continuous", "Vypnuto: prostor je rozdeleny na noty (struny). Zapnuto: vzdalenost plynule meni vysku, jako skutecny theremin.")
            dpg.add_checkbox(label="Novy ton pri zmene zony", tag="retrigger", default_value=False)
            _tip("retrigger", "Vypnuto = legato, ton plynule prejede. Zapnuto = kazda nova zona znovu spusti envelope.")
            dpg.add_checkbox(label="Hrat jen pri mezerniku", tag="space_to_play", default_value=False)
            _tip("space_to_play", "Ruka jen vybira notu, zvuk spusti mezernik. Nahrada budouciho tlacitka / footswitche.")
            dpg.add_separator()
            dpg.add_text("Filtr senzoru")
            _hint("Vyhlazuje klepani HC-SR04. Silnejsi filtr = klidnejsi nota, ale pomalejsi reakce.")
            dpg.add_combo(list(FILTER_MODES), default_value="median_ema", tag="filter_mode", width=160)
            _tip(
                "filter_mode",
                "raw - bez upravy, nejrychlejsi, nejvic skace.\n"
                "median - zahodi ojedinele nesmyslne echa.\n"
                "ema - plynule vyhlazeni.\n"
                "median_ema - oboji, vychozi volba pro sonar.",
            )
            dpg.add_slider_float(label="EMA alpha", tag="ema_alpha", default_value=0.35, min_value=0.05, max_value=1.0, width=160)
            _tip("ema_alpha", "Vyssi = rychlejsi reakce a vic sumu. Nizsi = hladsi a pomalejsi.")
            dpg.add_slider_float(label="Hystereze mm", tag="hysteresis", default_value=12.0, min_value=0.0, max_value=40.0, width=160)
            _tip("hysteresis", "Rezerva na hranici dvou not. Bez ni by jitter 229/231 porad preskakoval E/G.")
            dpg.add_separator()
            dpg.add_text("Simulator senzoru")
            _hint("Napodobuje levny ultrazvuk, dokud nemas Arduino v ruce.")
            dpg.add_slider_float(label="Sum mm", tag="sim_jitter", default_value=3.0, min_value=0.0, max_value=25.0, width=160)
            _tip("sim_jitter", "Nahodne chveni vzdalenosti. HC-SR04 takhle v praxi vypada.")
            dpg.add_slider_float(label="Vypadky", tag="sim_dropout", default_value=0.02, min_value=0.0, max_value=0.25, width=160)
            _tip("sim_dropout", "Pravdepodobnost, ze echo neprijde (senzor vrati prazdne mereni).")
            dpg.add_slider_float(label="Snimku / s", tag="sim_rate", default_value=40.0, min_value=8.0, max_value=80.0, width=160)
            _tip("sim_rate", "Jak casto prichazi nove mereni. Realny sonar bude podobne kolem 20-40 Hz.")
            dpg.add_checkbox(label="Automaticky pohyb vysky", tag="sweep_pitch")
            _tip("sweep_pitch", "Ruka na vysce jezdi sama nahoru a dolu. Hodi se na ladeni filtru.")
            dpg.add_checkbox(label="Automaticky pohyb hlasitosti", tag="sweep_volume")
            _tip("sweep_volume", "Totez pro levou ruku / hlasitost.")
            flick = dpg.add_button(label="Rychly skok vysky", callback=self._flick_pitch)
            _tip(flick, "Okamzite prehodi vysku na druhy konec rozsahu. Test, jestli stihame rychly pohyb.")
            swap = dpg.add_button(label="Prohodit ruce", callback=self._swap_hands)
            _tip(swap, "Prohodi, ktery senzor je vyska a ktery hlasitost. Kdyz bude hardware zapojeny naopak.")

    def _flick_pitch(self) -> None:
        dpg.set_value("sweep_pitch", False)
        self.simulator.sweep_pitch = False
        self.deck.stop_melody()
        self.simulator.flick_pitch()
        dpg.set_value("sim_pitch", self.simulator.pitch_target_mm)

    def _swap_hands(self) -> None:
        self.cfg.pitch_channel, self.cfg.volume_channel = self.cfg.volume_channel, self.cfg.pitch_channel
        self._status = f"Pitch=ch{self.cfg.pitch_channel}  Volume=ch{self.cfg.volume_channel}"

    def _build_antenna(
        self,
        title: str,
        draw_tag: str,
        slider_tag: str,
        default: float,
        tint: tuple[int, int, int],
        tip: str,
    ) -> None:
        with dpg.child_window(width=ANT_W + 72, height=ANT_H + 36, border=True):
            title_item = dpg.add_text(title, color=(*tint, 255))
            _tip(title_item, tip)
            _hint("Slider = ruka   nahore dal   dole bliz")
            with dpg.group(horizontal=True):
                dpg.add_drawlist(width=ANT_W, height=ANT_H, tag=draw_tag)
                dpg.add_slider_float(
                    tag=slider_tag,
                    default_value=default,
                    min_value=MM_LO,
                    max_value=MM_HI,
                    vertical=True,
                    height=ANT_H,
                    width=28,
                    callback=self._stop_sweep_on_drag,
                )
                _tip(slider_tag, tip + "\n\nZluta / zelena cara na liste je odfiltrovana poloha, slabsi je raw.")

    def _stop_sweep_on_drag(self, sender, _app_data) -> None:
        if sender == "sim_pitch":
            dpg.set_value("sweep_pitch", False)
            self.simulator.sweep_pitch = False
            self.deck.stop_melody()
        if sender == "sim_volume":
            dpg.set_value("sweep_volume", False)
            self.simulator.sweep_volume = False

    def _build_readouts(self) -> None:
        with dpg.child_window(width=340, height=ANT_H + 36, border=True):
            dpg.add_text("Ted hraje")
            with dpg.group(horizontal=True):
                dpg.add_text("Nota")
                dpg.add_text("-", tag="read_note", color=(255, 220, 120, 255))
                _tip("read_note", "Aktualni zona / ton podle prave ruky.")
                dpg.add_spacer(width=16)
                dpg.add_text("Gate")
                dpg.add_text("off", tag="read_gate")
                _tip("read_gate", "ON = synth ma hrat. Off = ticho (ruka mimo, mute, nebo nepustil jsi mezernik).")
                dpg.add_text("", tag="read_strike", color=(255, 120, 80, 255))
            _tip("read_strike", "Rychly pohyb k senzoru. Zatim jen diagnostika, notu nespousti.")
            dpg.add_spacer(height=6)
            _kv("Vyska raw", "read_pitch_raw", "Surova vzdalenost prave ruky, jak prisla ze senzoru / simulatoru.")
            _kv("Vyska filtr", "read_pitch_filt", "Stejna vzdalenost po filtru. Podle ni se vybira nota.")
            _kv("Hlasitost raw", "read_vol_raw", "Surova vzdalenost leve ruky.")
            _kv("Hlasitost filtr", "read_vol_filt", "Vyhlazena vzdalenost leve ruky. Podle ni je hlasitost.")
            _kv("Amplituda", "read_amp", "Jak nahlas synth prave hraje (0 = ticho, 1 = naplno).")
            dpg.add_separator()
            dpg.add_text("Diagnostika")
            _hint("Meri chovani vstupu. Az bude hardware, tady uvidis, jestli je sonar pouzitelny.")
            _kv("Snimku / s", "diag_rate", "Kolik mereni za sekundu opravdu chodi.")
            _kv("Interval", "diag_dt", "Mezera mezi dvema poslednimi snimky.")
            _kv("Jitter vysky", "diag_jitter_p", "Jak moc skace surova vyska, i kdyz ruku drzis na miste.")
            _kv("Jitter hlasitosti", "diag_jitter_v", "Totez pro levou ruku.")
            _kv("Vypadky vysky", "diag_drop_p", "Kolikrat neprislo platne mereni vysky.")
            _kv("Vypadky hlasitosti", "diag_drop_v", "Kolikrat neprislo platne mereni hlasitosti.")
            _kv("Strike", "diag_strikes", "Kolikrat se poznal rychly pohyb k senzoru.")
            _kv("Latence", "diag_latency", "Jen audio buffer, ne cela cesta ruka->ucho. Tu poctive jednim cislem nemerime.")
            _kv("Zaznam", "diag_log", "Jestli se zrovna pise CSV a kolik radku uz padlo.")
            dpg.add_spacer(height=8)
            _hint("Mezernik = extra spoust (kdyz je zapnuta)     M = mute")

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
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 10)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 6)
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (14, 16, 20, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (22, 26, 32, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (220, 224, 228, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Button, (40, 70, 72, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (60, 110, 108, 255))
                dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (120, 220, 180, 255))
                dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (120, 200, 170, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (32, 38, 44, 255))


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
