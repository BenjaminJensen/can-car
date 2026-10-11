# OBD Scan Analysis

What `scan.py` has established so far, from `scan_log3.txt` to `scan_log7.txt`. Not reviewed: `scan_log2.txt`, `log1.txt` (UTF-16; several decoder comments in `scan.py` cite log2).

| Log | Car state |
|---|---|
| 3 | awake, speed ~2 km/h, motor ECU answering |
| 4 | AC charging (gen 2 charger PIDs) |
| 5 | awake, **not** charging (gen 1 charger PIDs) |
| 6 | ignition on, not charging |
| 7 | ignition off, AC charging |

## Confirmed (sane values, cross-checked)

| Area | Parameters |
|---|---|
| **Battery (7E5)** | pack V (`/4`), pack A (`-(raw-2044)/4`, positive = discharge), SoC abs (`/2.5`), odometer, energy and coulomb counters, cell max/min V (`/4096`), pack temp, temp max/min with cell index (`s16/64`) |
| **Charge mgmt (765)** | SoC (`/2`), `chg_mode` (none/ac), SoC limits, timer defined, remaining minutes (`x5`, 127 = invalid) |
| **Charger (744)** | efficiency (91.8%), loss (0.52 kW), cooler temp, DC bus voltage (`1DA7`) |
| **MFD (714)** | range, usable kWh, odometer (`x10`), service range and days |
| **Motor ECU (7E0)** | SoC abs, SoC norm (`u16/100`, 78.6), ambient temp, DC-DC and PEM temps (Kelvin x10), speed, power (`s16/250`), accel, VIN |
| **Electric drive (7E6)** | motor temp, stator, PEU/PEV/PEW, dcdc3 (`s16/64`), DC-DC V and A. Cooler `0xFF` is a "not fitted" sentinel. |
| **Coolant temps** | `cool5` (16.0, equals PEM temp) and `cool_bat` (11.5, near pack temp) are confirmed. `cool2` is a "not fitted" sentinel. |

## Charging (log 7 vs log 6)

| | Log 6 (ignition on, idle) | Log 7 (ignition off, AC charging) |
|---|---|---|
| `chg_mode` | 0 (none) | 1 (ac) |
| battery current | +0.75 A | -19.0 A at 335 V, about 6.4 kW into the pack |
| `chg_remain` | empty | 80 min |
| `chg_power_eff` / `loss` | empty / 0.0 | 91.8% / 0.52 kW |
| `chg_lv_pwrstate` | `0x0f` | `0x04` |
| `dc_u` (`1DA7`) | 0 V | 336 V |
| DC-DC (12 V) | 12.9 V, 0 A | 13.2 V, 9.25 A |

- **Derived mains power:** 6.4 kW at the pack / 91.8% = about **6.9 kW from the wall** (roughly 30 A at 230 V single-phase). This is the only mains figure we have.
- **Charging marker:** `chg_lv_pwrstate` = `0x04` in both charging logs and `0x0f` in all three non-charging logs.
- **Cell voltage under charge:** about 3.99 V against 3.97 V at rest, with a 6.6 mV spread.
- **`1DA7` (`dc_u`):** tracks the DC bus. It reads 0 in log 6, 332 in log 5, 336 while charging. It is a bus-live indicator, not charge-specific.

## Ignition off

The motor ECU (7E0) is completely silent. The electric drive ECU (7E6), charger, charge management and battery ECUs keep answering.

## Battery SoH: `bat_soh_cac` (`0x74CB`, 94 bytes)

- 4-byte header `29 6e 00 54`; the last byte `0x54` = 84, apparently the cell count.
- Then one **u8 per cell** (84 bytes), each between `0xd5` and `0xd8` (213-216).
- Then 6 bytes of `0xAA` ISO-TP padding.
- Identical in logs 3-7, so it is a stored table, not a live reading.
- Spread is about 1.4%. Lowest values (`d5`) are at cells 49, 61 and 84.
- **Unknown:** the scale to Ah or %. A guess is raw = 2 x Ah (~108 Ah, ~90% of a 120 Ah cell), which is unverified. Compare against a VCDS or similar capacity reading to pin it down.
- `scan.py` now decodes it as cell count, min, max, mean and weakest cell indices.

An earlier version of this doc said "about 88 u16 values"; that was wrong.

## Fixed-invalid coolant temps

`mot_temp_cool1`, `cool3` and `cool4` read about 76.6 C (raw ~`0x0DA8`-`0x0DA9`) in every log where they answered, on a 10 C day. That is a fixed invalid code, not a temperature. `scan.py` now returns nothing for them.

## Guessed or unconfirmed

- **`inf_temp_pcb` / `inf_temp_audio`:** 52-56 C under the `u8-40` guess; identical to each other. Check under load.
- **`mfd_odometer`:** the `x10` is a guess (69470 against 69478 on the battery ECU).
- **`eld_temp_dcdc1` / `dcdc2`:** never respond; siblings do.
- **Charger temps `brd`, `pfccoil`, `dcdccoil`, `dcdcpl`:** never respond.
- **State bytes:** `mot_state` read `0x04` (log 3), `0x03` (log 5), `0x07` (log 6). `mot_drivemode` is always `0x01`. `chg_lv_autochg` is always `0x00`. Meanings unknown.
- **`bat_hist19`:** contains counters that only increase between logs (e.g. `...12463x` to `...12464a`, `...0b80d5` to `...0b80d6`); likely cumulative charge time or energy. Needs a longer charge capture with timestamps.
- **Other raw blobs:** `bat_hist18`, `bat_hist01`, `bat_soh_hist` (570 B, repeating 40-byte-ish records) have no decoder.

## Never answered

- **Brake electronics, brake boost, steering, brake sensors:** TPMS and all temps. They may need a different session or an extended diagnostic mode, or the addresses may be wrong.
- **Charger:** heater current, socket, socket stats, all `chg_test_*` PIDs.
- **CCS status:** no DC fast charge logged yet.
- **AC mains voltage and current, and DC current:** gen 1 (`1DA9`, `1DA8`, `4237`) and gen 2 (`41FC`, `41FB`, `41F8`, `41F9`) both returned nothing, including while charging (logs 4 and 7), even though the charger ECU answers its other PIDs. They are not supported on this car and were removed from `scan.py`.
- **Intermittent drops:** the motor ECU randomly drops single PIDs while awake (`mot_soc_norm`, `mot_temp_dcdc`, `cool1`). `scan.py` now retries each failed poll up to twice.

## Open items

1. Pin down the `bat_soh_cac` scale.
2. Drive log at speed, to check the infotainment temps and motor values under load.
3. DC fast charge capture for the CCS status PID.
4. Longer timestamped charge capture to decode `bat_hist19`.
