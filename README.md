# Sonar Theremin

Experimental Windows app: a cheap ultrasonic theremin / mini DJ deck. Two distance sensors (or a simulator) become two hands.

- **Left hand** = volume (closer = louder, out of range = mute)
- **Right hand** = pitch as scale zones, not a 12-note chromatic ladder
- Works **without hardware** via the built-in simulator

Later idea (not built yet): a third HC-SR04 far from the others so the right hand can play two scale strips side by side.

## Run

Python 3.11+ on Windows:

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -e .
theremin.bat
```

Or: `.venv\Scripts\python.exe -m theremin`

## Hardware (optional)

Firmware: `firmware/sonar_theremin.ino` for two HC-SR04 sensors.

- Pitch: TRIG 8, ECHO 9
- Volume: TRIG 10, ECHO 11
- Serial `115200`, line format `pitch_mm,volume_mm` (`-1` = invalid)

Pick the COM port in the app and switch the source to Serial.

## What is in the deck

- Scale presets (C major, pentatonic, A/D minor, custom zones)
- Demo tunes (Hobbits, Tetris, Doctor Who, Red Dwarf, …) — each sets a scale and a timbre you can also pick by hand
- Voices (whistle, organ, 8-bit, pad, sci-fi, brass, bass, pluck) plus a brightness slider
- Tiny drum machine under the melody
