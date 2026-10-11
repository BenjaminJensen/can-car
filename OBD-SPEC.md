# VW e-up OBD Charge/Status Spec

Implementation spec for reading charge status from the car over an ELM327 adapter.
Everything here was verified against a real car (VIN `WVWZZZAAZLD905445`, 2020 e-up, ~69.5k km) in `scan_log3.txt` to `scan_log7.txt`. Reference implementation: `car.py` (poll table, `Car.read`, `hex_bytes`) and `scan.py` (broader table). Background and unresolved items: `OBD-ANALYSIS.md`.

## 1. Required outputs

| Output | Source | Unit |
|---|---|---|
| SoC | `soc` (section 4) | % |
| Charge power | derived from `bat_v`, `bat_a` (section 5) | kW |
| Time remaining | `chg_remain` | minutes |
| Target SoC | `soc_limits` | % |
| Charging active (flag) | `chg_mode` and `chg_lv_pwrstate` | bool |

## 2. Transport

- Adapter: ELM327 over serial, 38400 baud, 3 s read timeout (`elm.py`).
- Protocol: ISO 15765-4, 11-bit CAN, 500 kbit/s.
- Init sequence, each sent as its own command: `ATZ`, `ATE0`, `ATL0`, `ATH0`, `ATS0`, `ATSP6`.
- Per-ECU addressing, sent only when the ECU changes:
  - `ATSH<tx>` (request CAN ID)
  - `ATCRA<rx>` (only accept this response ID)
  - `ATFCSH<tx>` (flow-control header, needed for multi-frame replies)
- Request: UDS ReadDataByIdentifier, `22 <pid hi> <pid lo>`, for example `221DD0`.
- Valid response: bytes `62 <pid hi> <pid lo> <data...>`. Check that byte 0 is `0x62` and bytes 1-2 equal the requested PID. Anything else (`7F ...` negative response, `NO DATA`, `?`, `CAN ERROR`, wrong PID) means no value.
- Multi-frame replies (long blobs): the ELM returns `0:`/`1:` prefixed lines and a short length line (for example `00B`). Strip the prefixes, skip the length line, concatenate. See `hex_bytes()` in `car.py`.
- Endianness: big-endian. `u16 = (b0 << 8) | b1`; signed variants use two's complement.

### Robustness requirements

- **Retry** a poll that returns no value up to 2 more times. The motor ECU randomly drops single PIDs while awake. The ECUs in this spec (below) are stable, but retries are cheap.
- **A valid-but-empty decode is not an error.** Sentinel values (below) mean "not available right now"; do not retry them and do not report 0.
- **Do not trust stale values.** When a metric returns nothing, clear it rather than keeping the last value, especially `chg_remain`, `chg_eff`, `chg_loss`.

## 3. Car state behaviour

| State | Which ECUs answer |
|---|---|
| Ignition on | all of those in section 4, plus motor ECU `7E0` |
| Ignition off, charging | battery `7E5`, charge mgmt `765`, charger `744`, MFD `714`, electric drive `7E6`. **Motor ECU `7E0` is silent** (every PID returns nothing). |
| Ignition off, not charging | not captured; assume the same as ignition off, charging |

Do not depend on any `7E0` PID for charge status. Everything required below works with ignition off.

## 4. Required PIDs

All verified in log 7 (ignition off, AC charging). "Raw" is the response payload after `62 <pid>`; raw values below are back-computed from the logged decoded value, so use them as test vectors, not as captured bytes.

| Name | tx / rx | PID | Decode | Log 7 value | Raw (test vector) |
|---|---|---|---|---|---|
| `soc` | `765` / `7CF` | `1DD0` | `u8 / 2.0` % (matches the dash; 0.5% steps) | 78.0 | `9C` |
| `bat_v` | `7E5` / `7ED` | `1E3B` | `u16 / 4.0` V | 335.0 | `053C` |
| `bat_a` | `7E5` / `7ED` | `1E3D` | `-((u16 - 2044) / 4.0)` A. **Positive = discharging, negative = charging.** | -19.0 | `0848` |
| `chg_mode` | `765` / `7CF` | `1DD6` | byte0: 0 = none, 1-3 = AC, >= 4 = DC. byte1: nonzero = timer mode active | 1 (ac), timer true | `01 xx` (xx nonzero) |
| `soc_limits` | `765` / `7CF` | `1DD1` | byte0 = timer SoC min, byte1 = timer SoC max, % | 100 / 100 | `64 64` |
| `chg_remain` | `765` / `7CF` | `1DE4` | `u8`; 127 = invalid; else `u8 * 5` minutes | 80 min | `10` |
| `chg_eff` | `744` / `7AE` | `15D6` | `u8`; > 250 = invalid; else `u8 / 10.0 + 75.0` % | 91.8 | `A8` |
| `chg_loss` | `744` / `7AE` | `15E1` | `u8 * 20 / 1000.0` kW | 0.52 | `1A` |
| `chg_lv_pwrstate` | `765` / `7CF` | `1DEC` | `u8`, undecoded. `0x04` while charging, `0x0f` when not (5 of 5 samples) | `0x04` | `04` |

Optional cross-checks, same ECUs: `bat_soc_abs` (`7E5`, `028C`, `u8 / 2.5`, 77.6 in log 7, consistently about 0.4 below `soc`), `range_km` (`714`/`77E`, `22E0`, `u16`), `usable_kwh` (`714`/`77E`, `22E4`, `u16 / 10.0`, 511 = invalid).

## 5. Derived values

```
pack_kw   = bat_v * abs(bat_a) / 1000            # log 7: 335.0 * 19.0 = 6.37 kW
charging  = chg_mode_raw != 0 and bat_a < 0      # see section 6
wall_kw   = pack_kw / (chg_eff / 100)            # log 7: 6.37 / 0.918 = 6.94 kW  (estimate)
```

- **Charge power** to display is `pack_kw` (power into the battery). `wall_kw` is an estimate only: the car does not report mains voltage or current (section 7).
- Only compute these while `charging` is true. When not charging, charge power is 0.
- Do not use `car.py`'s `derive()` `chg_power_kw`. It multiplies the AC/DC charger PIDs, which return nothing on this car, so it always gives 0. `bat_power_kw` in `derive()` is correct but is signed (negative while charging).

## 6. Charging detection

Report "charging" when these agree, and "unknown" when they disagree:

1. `chg_mode_raw != 0` (log 7: 1; non-charging logs: 0)
2. `bat_a < 0` (log 7: -19.0; non-charging: +0.75 to +1.25)
3. `chg_lv_pwrstate == 0x04` (undecoded, treat as a corroborating signal only)

`chg_mode` alone is the primary signal. Do not use the plug LED or `dc_v` (`744`, `1DA7`): `dc_v` reads the DC bus (332 V with the car awake and not charging, 0 V with ignition on and not charging, 336 V charging) and is not charge-specific.

## 7. Target SoC

`soc_limits` returned 100 / 100 in log 7. These are the **timer-mode** limits; `chg_timer_mode` was true. It is not confirmed that they bound an immediate (non-timer) charge, but the reported 80 min remaining is consistent with charging 78% to 100% at about 6.4 kW.

Implementation rule: report `timer_soc_max` as the target. If it is missing or 0, show the target as unknown. Do not hard-code 100.

## 8. What is NOT available

Do not poll these; none ever answered, including while charging:

- AC mains voltage and current, and DC current, for both generations: `744` PIDs `1DA9`, `1DA8`, `4237` (gen 1) and `41FC`, `41FB`, `41F8`, `41F9` (gen 2). `car.py`'s `GEN1` / `GEN2` lists contain these; skip them.
- Charger socket, heater current and temps `15D8`, `15D9`, `15DA`, `15EC`, `1DDA`, `1DE8`.
- `765` PIDs `1DEF` (CCS status, no DC fast charge captured yet), `1DD7`, `1DDA`, `1DE6`.
- Brake `713`, brake boost `73B`, steering `712`, brake sensors `762` ECUs: nothing.

## 9. Validity and sanity limits

| Metric | Valid range / rule |
|---|---|
| `soc` | 0-100, steps of 0.5 |
| `bat_v` | about 250-400 V; battery nominal near 335 V in the logs |
| `bat_a` | charging seen at -19 A; reject beyond about +/-200 A |
| `chg_remain` | 127 means invalid; empty when not charging |
| `chg_eff` | > 250 invalid; empty when not charging |
| time left vs power | cross-check only: 80 min at 6.4 kW is plausible |

## 10. Test vectors (log 7 decoded)

Feeding these into the decoders must reproduce the left column.

| Metric | Value |
|---|---|
| `soc` | 78.0 |
| `bat_v` | 335.0 |
| `bat_a` | -19.0 |
| `chg_kind` | `ac` |
| `chg_timer_mode` | true |
| `timer_soc_min` / `max` | 100 / 100 |
| `chg_remaining_min` | 80 |
| `chg_eff_pct` | 91.8 |
| `chg_loss_kw` | 0.52 |
| `pack_kw` | 6.37 |
| `wall_kw` | 6.94 |

Non-charging reference (log 6, ignition on): `chg_mode_raw` 0, `bat_a` +0.75, `chg_remain` empty, `chg_eff` empty, `chg_loss` 0.0, `chg_lv_pwrstate` `0x0f`.

## 11. Known gaps

- Mains power is estimated, not measured.
- Target SoC is the timer limit; behaviour for non-timer charging is unconfirmed.
- Only AC charging was captured. DC fast charging (`chg_mode_raw >= 4`) is untested, and `chg_remain`, `chg_eff` and the `dc_v` behaviour there are unknown.
- Gen 1 vs gen 2: this car is a 2020 e-up (gen 2 by `car.py`'s convention), but its DC bus voltage answers on the gen 1 PID `1DA7`.
