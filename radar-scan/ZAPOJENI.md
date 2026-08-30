# Radar scan — PCA9685 + 2 serva

Cast projektu mimo hudbu. Tady se zapojuje **PCA9685** a **2 serva** na **ESP32**.
Sonary zatim nemusis pripojovt.

## Co potrebujes

- ESP32 (stejna deska jako u thereminu)
- PCA9685 (16-kanalovy PWM driver)
- 2x servo (SG90 / MG90S / podobne)
- **externi zdroj 5 V** pro serva (ne USB / ne 5V pin ESP32)
- spolecna zem (GND)

## Kolik voltu a kolik proudu

| Co | Napeti | Poznamka |
|-----|--------|----------|
| Logika PCA9685 + I2C | **3.3 V** z ESP32 `3V3` | jen cipy, male proudy |
| Serva (V+ na PCA9685) | **5.0 V** z externiho zdroje | **nikdy** z ESP32 `VIN` / `5V` |
| ESP32 | USB 5 V | jen deska, ne motory |

**Proud (dva SG90):**

- klid / pomaly pohyb: cca **200–400 mA**
- obe serva najednou (start / zasek): **1.0–1.5 A**
- bezpecna rezerva: zdroj **5 V / 2 A**

Pokud mas MG996 / vetsi serva: **5 V / 3 A** a vic.

**Dulezite:** jumper `VCC` na PCA9685 (maly konektor vedle V+) **odpoj** (odeber),
aby se 5 V serv **nenapajelo** do 3.3 V logiky. Logiku napaj z ESP32 `3V3` na pin `VCC` / `VCC` (logicky, ne V+).

Na Adafruit / cinskych clonech:

- `V+` (svorka se sroubkem) = **jen serva, 5 V externi**
- `VCC` (pin vedle SDA/SCL) = **3.3 V z ESP32**
- jumper mezi `VCC` a `V+` = **pryc**

## Tvoje deska (piny: GND OE SCL SDA VCC + ext 5 V)

Tohle je presne ten radek vedle I2C:

| Pin na PCA | Kam | Proc |
|------------|-----|------|
| **VCC** | ESP32 **3V3** | napajeni cipu (logika) |
| **SDA** | ESP32 **GPIO 21** | data I2C |
| **SCL** | ESP32 **GPIO 25** | hodiny I2C |
| **OE** | ESP32 **GND** | **povinne** — jinak PWM vypnute, serva se nehnou |
| **GND** | ESP32 **GND** + GND zdroje | spolecna zem |

**Ext 5 V** (svorka / V+):

| Ext zdroj | PCA |
|-----------|-----|
| **+5 V** | svorka **5V / V+** (ta u ktere jsi psal „ext zdroj“) |
| **GND** | **GND** na PCA (stejny GND jako u I2C radku) |

`VCC` **neni** 5 V. `VCC` = 3.3 V z ESP32. Serva ziji jen z **ext 5 V**.

`OE` **dej na GND**. Volny OE na cinskych deskach = vypnute vystupy.

Pokud na desce je maly **jumper** u napajeni, nech ho **rozpojeny**,
aby se 5 V serv nespojilo s `VCC` (3.3 V).

## Kam to zapojit (ESP32)

PCA9685 I2C (logika):

| PCA9685 | ESP32 |
|---------|--------|
| VCC     | **3V3** |
| GND     | **GND** |
| SDA     | **GPIO 21** |
| SCL     | **GPIO 25** |
| OE      | **GND** (povinne, jinak serva stoji) |
| A0–A5   | nechat nezapojene = adresa **0x40** |

GPIO **18, 19, 22, 23** nech sonarum.
I2C: `Wire.begin(21, 25)`. Dalsi volna dvojice az kdyby 21/25 padly: **32 + 33**.

**Spolecna zem (povinne):**

```
GND externiho 5V zdroje  --+--  GND ESP32  --+--  GND PCA9685
                            |                 |
                            +-----------------+
```

Bez spojene zeme se serva trepou nebo vubec nejedou.

## Externi 5 V na PCA9685

| Zdroj 5 V | PCA9685 |
|-----------|---------|
| +5 V      | **V+** (sroubovaci svorka, casto zeleny blok) |
| GND       | **GND** (stejna svorka / pin GND) |

Serva **nesmi** jit z USB notebooku. USB umi 0.5 A, serva to prekoci a ESP32 se resetuje.

## Kam zapojit 2 serva

Posledni **ctverice** portu na PCA = kanaly **12, 13, 14, 15**.

| Servo | Kanal PCA9685 | Rozsah |
|-------|----------------|--------|
| Servo A (plny pohyb) | **12** | **0–180°** |
| Servo B (rameno omezuje) | **15** | **45–135°** (stred 90) |

Kdyz kanal 15 naraží, v kodu zmensi `LIM_MIN` / `LIM_MAX` (treba 60–120).

## Co zatim NEZAPOJOVAT

- HC-SR04 muzes nechat odpojene
- ESP32 **5V / VIN** na serva **nepouzivej**
- 6 V / 7.4 V do SG90 nepatří (spalis servo)

## Kontrola pred prvnim zapnutim

1. Jumper VCC–V+ je **odebrany**
2. `3V3` ESP32 → `VCC` PCA9685
3. Externi **5 V / min. 2 A** → `V+` PCA9685
4. Vsechny **GND** spojene
5. Serva: kanal **12** (plnych 180) a **15** (omezeny)
6. Nejdriv zapni **externi 5 V**, pak USB k ESP32

Posuvniky z PC: `serva.bat` (po nahrani tohoto sketch).
