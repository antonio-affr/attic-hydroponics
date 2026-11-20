# Wiring: the Arduino Nano sensor hub

The Nano sits on its screw-terminal shield and collects the wired sensors. It
reaches the Pi over one USB cable (power and data). The sketch is in
`nano/attic_nano/`, how to upload it in `nano/README.md`.

Final set of sensors on the Nano:

| Sensor | Quantity | Bus / pin | Status |
|---|---|---|---|
| BH1750 light (ARCELI dome) | 3, one per growing area | I2C, each on its own pair of pins | 1 tested on the desk |
| DS18B20 water temperature (steel probe) | 1, in the tank | 1-Wire on D4 | tested on the desk |
| Ultrasonic water height, on the tank lid | 1 | serial on D10 | exact model to confirm |

```
                        USB (power + data)
   Raspberry Pi  <--------------------------------  Nano + shield
                                                    |  5V    -> WAGO +5V  -> every sensor's VCC
                                                    |  GND   -> WAGO GND  -> every sensor's GND
                                                    |  A4/A5 -> light 1
                                                    |  D2/D3 -> light 2
                                                    |  D5/D6 -> light 3
                                                    |  D4    -> water temperature
                                                    |  D10   <- tank height
```

Rules:

- **Unplug the USB before touching any wire.**
- Power goes through the WAGOs; signal wires go straight to the shield.
- Red wires (and the resistor leg) only in the +5V WAGOs, black wires only in
  the GND WAGOs. The two never touch.
- Go by the labels printed on each sensor's board, never by wire colour alone.
- D0 and D1 stay empty: they are the USB serial link.
- Bare tinned ends are fine on the desk; in the attic, crimp ferrules on them.

## 1. Power: Nano to the WAGOs

| From (shield) | Wire | To |
|---|---|---|
| **5V** (on the A0 to A7 side, past A7) | red | +5V WAGO A, slot 1 |
| **GND** | black | GND WAGO A, slot 1 |

Each WAGO 221-415 has 5 slots: one wire in, four out. The final set needs
**6 on the +5V side and 5 on GND**, so each side is two WAGOs joined by a short
wire (A slot 5 to B slot 1), which gives 7 free slots. A single 8-slot WAGO
221-418 per side does the same.

| Slot | +5V side | GND side |
|---|---|---|
| A1 | feed from shield 5V | feed from shield GND |
| A2 | light 1 red | light 1 black |
| A3 | light 2 red | light 2 black |
| A4 | light 3 red | light 3 black |
| A5 | link wire to B1 | link wire to B1 |
| B2 | water probe red | water probe black |
| B3 | 4.7 kOhm resistor, second leg | tank height sensor GND |
| B4 | tank height sensor VCC | spare |
| B5 | spare | spare |

## 2. Light sensors (3 x BH1750)

All three BH1750 answer at the same address (`0x23`), so they cannot share
one bus. Instead each gets its own pair of Nano pins: light 1 on the Nano's
hardware I2C (A4/A5), lights 2 and 3 on software I2C (D2/D3, D5/D6). No hub
is needed, and each sensor's cable is a separate short bus, which matters for
the long runs up to the shelves.

Wire colours on the ARCELI dome module, confirmed from the board's printed
labels (1 VCC, 2 SCL, 3 DAT, 4 GND, 5 ADDR):

| Wire | Light 1 (sits on Tray2Grow 2) | Light 2 (sits on Tray2Grow 1) | Light 3 (Grow area 3) |
|---|---|---|---|
| red (VCC) | +5V WAGO | +5V WAGO | +5V WAGO |
| black (GND) | GND WAGO | GND WAGO | GND WAGO |
| white (SDA, "DAT") | shield **A4** | shield **D2** | shield **D5** |
| brown (SCL) | shield **A5** | shield **D3** | shield **D6** |
| yellow (ADDR) | nothing, taped | nothing, taped | nothing, taped |

Each sits in the middle of its growing area, at plant height, dome facing up.
In the attic, lights 1 and 2 ended up on each other's tray; rather than
rewiring, `config/collector.pi.yaml` maps Tray2Grow 1 to `light_2_lux` and
Tray2Grow 2 to `light_1_lux`. If you ever move them back, swap those two
`field:` lines too.

Do **not** wire ADDR to 5V to get a second address: the BH1750 chip itself
runs on about 3.3 V, and 5 V on that pin may exceed its rating.

## 3. Water temperature (DS18B20 probe)

| Probe wire / part | To |
|---|---|
| red (VCC) | +5V WAGO |
| black (GND) | GND WAGO |
| yellow (DATA) | shield **D4** |
| 4.7 kOhm resistor (yellow-violet-red bands) | one leg in **D4** with the yellow wire, the other in the +5V WAGO |

Without the resistor reaching 5 V the data line idles LOW and the probe is
never found (`water_temperature_c` stays `null`). Placed in the tank, below
the lowest water level, away from the inlet.

## 4. Tank water height (ultrasonic): model to confirm

Planned: an A02YYUW-type waterproof ultrasonic sensor on the tank lid, facing
straight down at the water. Typical wiring for that model (check against the
label of the one you have before connecting):

| Sensor wire | To |
|---|---|
| red (VCC) | +5V WAGO |
| black (GND) | GND WAGO |
| white / TX (sensor transmits) | shield **D10** |
| yellow / RX (mode) | nothing, taped (continuous output) |

The sensor cannot see the first few centimetres in front of it (blind zone),
so the lid must sit at least that far above the highest water level. The
tank's inside dimensions turn the distance into litres; they are not known
yet.

## Status

- Tested on the desk: light 1 (A4/A5 directly, before the hub), water
  temperature (D4), the WAGO power path.
- To buy: WAGOs for 6 slots per side (two more 221-415, or two 221-418).
- To confirm: the ultrasonic sensor's model and wires; the tank's inside
  dimensions; the cable length from the enclosure to each tray.
