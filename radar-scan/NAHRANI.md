# Nahrani firmware z editoru

PlatformIO projekt: tato slozka `radar-scan/` (soubor `platformio.ini`).

## VS Code nebo Cursor (doporuceno)

1. Rozsireni: **PlatformIO IDE** (`platformio.platformio-ide`)
2. **File → Open Folder** → `C:\Users\Admin\Desktop\sonar-theremin\radar-scan`
   (ne cely sonar-theremin — PlatformIO chce koren s `platformio.ini`)
3. Dole v status baru: **Upload** (sipka)
4. Port je v `platformio.ini`: `COM7`

Kod: `src/main.cpp` (stejny jako `firmware/pca9685_servo_test.ino`).

## Visual Studio 2022 (plne VS)

Arduino nahravani tam neni vestavene. Bud:

- nainstaluj **Visual Micro** (Arduino for Visual Studio), otevri `.ino`, deska ESP32, COM7, Upload
- nebo pouzij **VS Code / Cursor + PlatformIO** vyse (jednodussi)

## Po nahrani

Zavri Serial Monitor (COM7 uvolni) a spust `serva.bat`.
