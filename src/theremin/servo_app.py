"""Servo tuner — sliders on PC, ESP32+PCA9685 on serial."""

from __future__ import annotations

import time

import dearpygui.dearpygui as dpg

from theremin.sources import list_serial_ports

DEFAULT_PORT = "COM7"
DEFAULT_BAUD = 115200
CHANNELS = [str(i) for i in range(16)]


class ServoApp:
    def __init__(self) -> None:
        self._ser = None
        self._status = "Nahraj novy firmware, 5 V, Connect"
        self._last_send = 0.0
        self._pending: dict[int, int] = {}
        self._last_sent: dict[int, int] = {}
        self._log_buf = ""
        self._scan_next = -1
        self._scan_at = 0.0

    def run(self) -> None:
        dpg.create_context()
        with dpg.theme(tag="servo_theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (14, 16, 20, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (225, 228, 232, 255))
        self._build()
        dpg.create_viewport(title="Sonar Serva", width=740, height=620)
        dpg.setup_dearpygui()
        dpg.bind_theme("servo_theme")
        dpg.set_primary_window("servo_root", True)
        dpg.show_viewport()
        self._connect(DEFAULT_PORT)

        try:
            while dpg.is_dearpygui_running():
                self._tick()
                dpg.render_dearpygui_frame()
        finally:
            self._close()
            dpg.destroy_context()

    def _ch_a(self) -> int:
        return int(dpg.get_value("ch_a"))

    def _ch_b(self) -> int:
        return int(dpg.get_value("ch_b"))

    def _build(self) -> None:
        with dpg.window(tag="servo_root", no_close=True):
            with dpg.group(horizontal=True):
                dpg.add_text("SERVA", color=(255, 180, 90, 255))
                dpg.add_combo(
                    items=list_serial_ports() or [DEFAULT_PORT],
                    default_value=DEFAULT_PORT,
                    tag="port_combo",
                    width=100,
                )
                dpg.add_button(label="Connect", callback=lambda: self._connect(dpg.get_value("port_combo")))
                dpg.add_button(label="Dopredu (A90 / B110)", callback=self._mid)
                dpg.add_button(label="Ping I2C", callback=lambda: self._send("ping"))
            dpg.add_text("", tag="status", color=(160, 170, 180, 255))
            dpg.add_input_text(tag="log", multiline=True, readonly=True, height=90, width=680)
            dpg.add_spacer(height=6)

            dpg.add_text("1) OE na GND   2) nahraj tento firmware   3) Ping I2C musi rict found 0x40", color=(255, 160, 100, 255))

            dpg.add_text("Kanaly PCA", color=(255, 210, 120, 255))
            with dpg.group(horizontal=True):
                dpg.add_text("A vlevo-vpravo")
                dpg.add_combo(CHANNELS, default_value="12", tag="ch_a", width=60, callback=self._on_map)
                dpg.add_button(label="Klepni A", callback=lambda: self._nudge(self._ch_a()))
                dpg.add_spacer(width=16)
                dpg.add_text("B nahoru-dolu")
                dpg.add_combo(CHANNELS, default_value="15", tag="ch_b", width=60, callback=self._on_map)
                dpg.add_button(label="Klepni B", callback=lambda: self._nudge(self._ch_b()))
            dpg.add_button(label="Projdi 0-15 (kazdy kanal klepne)", callback=self._scan_all)
            dpg.add_text(
                "A = pan (vlevo/vpravo). B = tilt: 0 = strop, 159 = dolu.",
                color=(140, 150, 160, 255),
            )
            dpg.add_spacer(height=8)

            dpg.add_text("Servo A — vlevo / vpravo (kanal 12, 0-180)", color=(255, 210, 120, 255))
            dpg.add_slider_int(
                label="vlevo-vpravo",
                tag="ang_a",
                default_value=90,
                min_value=0,
                max_value=180,
                callback=self._on_ang_a,
            )
            dpg.add_spacer(height=10)

            dpg.add_text("Servo B — nahoru / dolu (kanal 15: 0 = strop)", color=(255, 210, 120, 255))
            dpg.add_slider_int(
                label="nahoru(0) ... dopredu(110) ... dolu(159)",
                tag="ang_b",
                default_value=110,
                min_value=0,
                max_value=180,
                callback=self._on_ang_b,
            )
            dpg.add_slider_int(
                label="min B (strop)",
                tag="min_b",
                default_value=0,
                min_value=0,
                max_value=170,
                callback=self._on_lim_b,
            )
            dpg.add_slider_int(
                label="max B (dolu)",
                tag="max_b",
                default_value=159,
                min_value=10,
                max_value=180,
                callback=self._on_lim_b,
            )
            dpg.add_text("A ch12 @90    B ch15 @90    lim 45-135", tag="readout")

    def _connect(self, port: str) -> None:
        self._close()
        try:
            import serial
        except ImportError:
            self._status = "pyserial chybi"
            self._set_status()
            return
        try:
            self._ser = serial.Serial(str(port), DEFAULT_BAUD, timeout=0.05)
            time.sleep(1.6)
            self._ser.reset_input_buffer()
            self._status = f"Serial {port}"
            self._send("ping")
            time.sleep(0.15)
            self._send_map()
            self._send("mid")
            self._on_lim_b()
        except Exception as exc:  # noqa: BLE001
            self._ser = None
            self._status = str(exc)
        self._set_status()

    def _close(self) -> None:
        if self._ser is None:
            return
        try:
            self._ser.close()
        except Exception:
            pass
        self._ser = None

    def _send_map(self) -> None:
        if not dpg.does_item_exist("ch_a"):
            return
        self._send(f"map {self._ch_a()} {self._ch_b()}")

    def _on_map(self) -> None:
        self._last_sent.clear()
        self._send_map()
        self._mid()

    def _mid(self) -> None:
        # A 90 = vodorovny stred. B 110 = primo dopredu (0 = strop).
        look = 110
        if dpg.does_item_exist("ang_a"):
            dpg.set_value("ang_a", 90)
            dpg.set_value("ang_b", look)
        self._send("mid")
        self._send(f"{self._ch_b()} {look}")
        self._last_sent[self._ch_a()] = 90
        self._last_sent[self._ch_b()] = look

    def _nudge(self, ch: int) -> None:
        self._send(f"nudge {ch}")

    def _scan_all(self) -> None:
        self._scan_next = 0
        self._scan_at = 0.0
        self._status = "scan 0…"
        self._set_status()

    def _on_ang_a(self) -> None:
        self._queue(self._ch_a(), int(dpg.get_value("ang_a")))

    def _on_ang_b(self) -> None:
        lo = int(dpg.get_value("min_b"))
        hi = int(dpg.get_value("max_b"))
        ang = int(dpg.get_value("ang_b"))
        clipped = max(lo, min(hi, ang))
        if clipped != ang:
            dpg.set_value("ang_b", clipped)
        self._queue(self._ch_b(), clipped)

    def _on_lim_b(self) -> None:
        if not dpg.does_item_exist("min_b"):
            return
        lo = int(dpg.get_value("min_b"))
        hi = int(dpg.get_value("max_b"))
        if hi < lo + 5:
            hi = lo + 5
            dpg.set_value("max_b", hi)
        ang = int(dpg.get_value("ang_b"))
        clipped = max(lo, min(hi, ang))
        dpg.set_value("ang_b", clipped)
        self._send(f"lim {lo} {hi}")
        self._queue(self._ch_b(), clipped)

    def _queue(self, ch: int, deg: int) -> None:
        self._pending[int(ch)] = int(deg)

    def _send(self, line: str) -> None:
        if self._ser is None:
            return
        try:
            self._ser.write((line.strip() + "\n").encode("ascii"))
        except Exception as exc:  # noqa: BLE001
            self._status = str(exc)
            self._set_status()

    def _tick(self) -> None:
        now = time.perf_counter()
        if 0 <= self._scan_next <= 15 and now >= self._scan_at:
            ch = self._scan_next
            self._send(f"nudge {ch}")
            self._status = f"scan kanal {ch}"
            self._set_status()
            self._scan_next += 1
            self._scan_at = now + 0.9
            if self._scan_next > 15:
                self._scan_next = -1
                self._status = "scan hotovo"

        if self._pending and now - self._last_send >= 0.04:
            for ch, deg in list(self._pending.items()):
                if self._last_sent.get(ch) == deg:
                    continue
                self._send(f"{ch} {deg}")
                self._last_sent[ch] = deg
            self._pending.clear()
            self._last_send = now

        if self._ser is not None:
            try:
                waiting = self._ser.in_waiting
                if waiting:
                    chunk = self._ser.read(waiting).decode("ascii", errors="ignore")
                    if chunk:
                        self._log_buf = (self._log_buf + chunk)[-2500:]
                        if dpg.does_item_exist("log"):
                            dpg.set_value("log", self._log_buf)
                    last = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
                    if last:
                        self._status = last[-1]
                        self._set_status()
            except Exception:
                pass

        if dpg.does_item_exist("readout"):
            a = dpg.get_value("ang_a")
            b = dpg.get_value("ang_b")
            lo = dpg.get_value("min_b")
            hi = dpg.get_value("max_b")
            dpg.set_value(
                "readout",
                f"A pan ch{self._ch_a()} @{a}    B tilt ch{self._ch_b()} @{b} (0=strop)    lim {lo}-{hi}",
            )

    def _set_status(self) -> None:
        if dpg.does_item_exist("status"):
            dpg.set_value("status", self._status)


def main() -> None:
    ServoApp().run()
