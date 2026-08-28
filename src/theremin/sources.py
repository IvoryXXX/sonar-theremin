from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread

from theremin.types import SensorFrame, parse_mm


class DistanceSource(ABC):
    name = "source"

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    @abstractmethod
    def poll(self) -> SensorFrame | None:
        raise NotImplementedError


class SimulatorSource(DistanceSource):
    name = "simulator"

    def __init__(self) -> None:
        self.pitch_target_mm = 200.0
        self.volume_target_mm = 140.0
        self.jitter_mm = 3.0
        self.dropout = 0.02
        self.rate_hz = 40.0
        self.sweep_pitch = False
        self.sweep_volume = False
        self._last_t = 0.0
        self._rng_state = 0xC0FFEE

    def start(self) -> None:
        self._last_t = 0.0

    def poll(self) -> SensorFrame | None:
        now = time.perf_counter()
        interval = 1.0 / max(self.rate_hz, 1.0)
        if self._last_t == 0.0:
            self._last_t = now
            return self._make_frame()
        if now - self._last_t < interval:
            return None
        # Catch up at most one extra frame so the UI cannot flood the pipeline.
        self._last_t += interval
        if now - self._last_t > interval:
            self._last_t = now
        return self._make_frame()

    def flick_pitch(self) -> None:
        self.sweep_pitch = False
        self.pitch_target_mm = 90.0 if self.pitch_target_mm > 200.0 else 340.0

    def _make_frame(self) -> SensorFrame:
        if self.sweep_pitch:
            self.pitch_target_mm = _triangle(time.perf_counter(), 0.22, 90.0, 360.0)
        if self.sweep_volume:
            self.volume_target_mm = _triangle(time.perf_counter(), 0.13, 70.0, 300.0)
        pitch = self._measure(self.pitch_target_mm)
        volume = self._measure(self.volume_target_mm)
        return SensorFrame(time.time_ns(), (pitch, volume), source=self.name)

    def _measure(self, target: float) -> float | None:
        if self._rand() < self.dropout:
            return None
        noise = (self._rand() * 2.0 - 1.0) * self.jitter_mm
        return max(20.0, target + noise)

    def _rand(self) -> float:
        # Tiny independent RNG so numpy's global state is not a dependency here.
        self._rng_state = (1103515245 * self._rng_state + 12345) & 0x7FFFFFFF
        return self._rng_state / 0x7FFFFFFF


class SerialSource(DistanceSource):
    name = "serial"

    def __init__(self) -> None:
        self.port = ""
        self.baud = 115200
        self._queue: Queue[SensorFrame] = Queue(maxsize=64)
        self._stop = Event()
        self._thread: Thread | None = None
        self._error: str | None = None
        self._ser = None

    @property
    def error(self) -> str | None:
        return self._error

    def start(self) -> None:
        self.stop()
        self._error = None
        if not self.port:
            self._error = "No COM port selected"
            return
        try:
            import serial
        except ImportError:
            self._error = "pyserial is not installed"
            return
        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=0.05)
        except Exception as exc:  # noqa: BLE001 — surface any port error in the UI
            self._error = str(exc)
            self._ser = None
            return
        self._stop.clear()
        self._thread = Thread(target=self._read_loop, name="serial-source", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.4)
            self._thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break

    def poll(self) -> SensorFrame | None:
        try:
            return self._queue.get_nowait()
        except Empty:
            return None

    def _read_loop(self) -> None:
        assert self._ser is not None
        buf = ""
        while not self._stop.is_set():
            try:
                waiting = self._ser.in_waiting
                chunk = self._ser.read(waiting or 1)
            except Exception as exc:  # noqa: BLE001
                self._error = str(exc)
                break
            if not chunk:
                continue
            buf += chunk.decode("ascii", errors="ignore")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                frame = parse_serial_line(line)
                if frame is None:
                    continue
                if self._queue.full():
                    try:
                        self._queue.get_nowait()
                    except Empty:
                        pass
                self._queue.put(frame)


class ReplaySource(DistanceSource):
    name = "replay"

    def __init__(self) -> None:
        self.path: Path | None = None
        self.speed = 1.0
        self._frames: list[SensorFrame] = []
        self._index = 0
        self._t0_ns = 0
        self._wall0 = 0.0
        self._error: str | None = None

    @property
    def error(self) -> str | None:
        return self._error

    def load(self, path: str | Path) -> bool:
        self.path = Path(path)
        self._error = None
        try:
            self._frames = load_replay_csv(self.path)
        except Exception as exc:  # noqa: BLE001
            self._frames = []
            self._error = str(exc)
            return False
        if not self._frames:
            self._error = "CSV has no frames"
            return False
        self._index = 0
        self._t0_ns = self._frames[0].t_ns
        self._wall0 = time.perf_counter()
        return True

    def start(self) -> None:
        self._index = 0
        if self._frames:
            self._t0_ns = self._frames[0].t_ns
            self._wall0 = time.perf_counter()

    def poll(self) -> SensorFrame | None:
        if self._index >= len(self._frames):
            return None
        frame = self._frames[self._index]
        elapsed = (time.perf_counter() - self._wall0) * max(self.speed, 0.05)
        due = (frame.t_ns - self._t0_ns) / 1e9
        if elapsed < due:
            return None
        self._index += 1
        return SensorFrame(time.time_ns(), frame.ranges_mm, source=self.name)


def parse_serial_line(line: str) -> SensorFrame | None:
    line = line.strip()
    if not line:
        return None
    parts = [p for p in line.replace(";", ",").split(",") if p != ""]
    if not parts:
        return None
    ranges = tuple(parse_mm(part) for part in parts)
    if all(v is None for v in ranges) and not any(ch.isdigit() for ch in line):
        return None
    return SensorFrame(time.time_ns(), ranges, source="serial")


def load_replay_csv(path: Path) -> list[SensorFrame]:
    frames: list[SensorFrame] = []
    text = path.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return frames
    start = 1 if "timestamp" in lines[0].lower() else 0
    for line in lines[start:]:
        cols = line.split(",")
        if len(cols) < 3:
            continue
        try:
            t_ns = int(float(cols[0]))
        except ValueError:
            continue
        if t_ns < 10_000_000_000:
            t_ns *= 1_000_000  # timestamp_ms -> ns
        pitch = parse_mm(cols[1])
        volume = parse_mm(cols[2])
        frames.append(SensorFrame(t_ns, (pitch, volume), source="replay"))
    return frames


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [p.device for p in list_ports.comports()]


def _triangle(t: float, hz: float, lo: float, hi: float) -> float:
    phase = (t * hz) % 1.0
    tri = 1.0 - abs(2.0 * phase - 1.0)
    return lo + tri * (hi - lo)
