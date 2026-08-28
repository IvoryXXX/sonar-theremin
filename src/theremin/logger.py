from __future__ import annotations

from pathlib import Path

from theremin.types import SensorFrame, Voice

HEADER = (
    "timestamp_ns,raw_pitch_mm,raw_vol_mm,filt_pitch_mm,filt_vol_mm,"
    "note,hz,amp,gate,vy_mm_s,strike"
)


class CsvLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("w", encoding="utf-8", newline="\n")
        self._file.write(HEADER + "\n")
        self.rows = 0

    def write(self, frame: SensorFrame, voice: Voice) -> None:
        self._file.write(
            ",".join(
                [
                    str(frame.t_ns),
                    _fmt(voice.pitch_raw_mm),
                    _fmt(voice.volume_raw_mm),
                    _fmt(voice.pitch_mm),
                    _fmt(voice.volume_mm),
                    voice.note_name or "",
                    f"{voice.frequency_hz:.3f}",
                    f"{voice.amplitude:.4f}",
                    "1" if voice.gate else "0",
                    _fmt(voice.pitch_velocity_mm_s),
                    "1" if voice.strike else "0",
                ]
            )
            + "\n"
        )
        self.rows += 1
        if self.rows % 8 == 0:
            self._file.flush()

    def close(self) -> None:
        try:
            self._file.flush()
            self._file.close()
        except Exception:
            pass


def _fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.3f}"
