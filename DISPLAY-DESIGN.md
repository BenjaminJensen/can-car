# Charge Display Design

Approach for the charge display: State of Charge, charge power, charge limit, time to limit. Builds on `OBD-SPEC.md` (PIDs, decoding, charging detection). Nothing here is implemented yet. Display target (terminal, GUI, embedded) is undecided.

## 1. Outputs

| Output | Source | Notes |
|---|---|---|
| SoC | `soc` (`765`/`1DD0`) | See `OBD-SPEC.md` section 4 |
| Charge power | `pack_kw = bat_v * abs(bat_a) / 1000` | Power into the battery, not mains. 0 when not charging |
| Charge limit | display-side setting, car timer max overrides | Section 2 |
| Time to limit | own estimate, car's `chg_remain` as cross-check | Section 3 |

## 2. Charge limit

The car exposes no readable limit for an immediate charge. This matches what OVMS found (checked in `vehicle_vweup.cpp` and `vweup_obd.cpp`, OVMS v3 master):

- OVMS reads only the **timer-mode** limits (`1DD1`, min and max). It has no write path to the car's limit: no UDS `0x2E`, no `0x27` security access, no write session.
- Its effective limit is a **software setting**, `xvu chg_soclimit` (default 80). It is used when the timer max is 0 or 100.
- OVMS enforces it by stopping the charge itself over T26 (comfort CAN), via `StartStopChargeT26(false)`. Without T26 enforcement it only labels the state "topoff".
- Our log 7 read `1DD1` = 100/100 with timer mode active. Read as "no timer limit", not as "the car stops at 100".

Rule:

```
limit = timer_soc_max     if timer mode active and 0 < timer_soc_max < 100
      = configured_limit  otherwise            # display-side setting, default 80
```

- `configured_limit` is a value in config. 100 means no limit.
- The display only **shows** the limit. It does not make the car stop. Enforcement needs T26 (section 5).
- When the displayed limit is our own, label it as such (for example "display limit") so it is not mistaken for a car setting.
- Unverified: whether `1DD1` / `1DE4` follow a timer max below 100. See section 6.

## 3. Time to limit

```
remaining_kwh = (limit - soc) / 100 * capacity_kwh
minutes       = remaining_kwh / pack_kw * 60
```

- Only while `charging` (`OBD-SPEC.md` section 6). Show nothing when not charging.
- If `soc >= limit`, show 0 / "at limit".
- `capacity_kwh`: `usable_kwh` (`714`/`22E4`, `u16 / 10`, 511 = invalid) if valid, else a fixed e-up value (about 32 kWh usable, from memory, not verified against the logs).
- `pack_kw`: smooth over a short window (the current is steady near 19 A but tapers at the end of a charge).
- Cross-check against the car's `chg_remain` (`765`/`1DE4`, `u8 * 5` min, 127 = invalid). It is the time to the car's own end of charge, so it is comparable only when `limit` is 100 (or the timer max). Use it as a fallback when `pack_kw` is unusable, and to sanity-check the estimate.
- Do not hold stale values. Clear the estimate when inputs are missing (`OBD-SPEC.md` section 2).

## 4. Poll set

Only what the display needs, all answering with ignition off:

| Name | tx / rx | PID |
|---|---|---|
| `soc` | `765` / `7CF` | `1DD0` |
| `bat_v` | `7E5` / `7ED` | `1E3B` |
| `bat_a` | `7E5` / `7ED` | `1E3D` |
| `chg_mode` | `765` / `7CF` | `1DD6` |
| `soc_limits` | `765` / `7CF` | `1DD1` |
| `chg_remain` | `765` / `7CF` | `1DE4` |
| `usable_kwh` | `714` / `77E` | `22E4` |

Group polls by ECU to limit `ATSH` / `ATCRA` / `ATFCSH` switches. Retry and validity rules are in `OBD-SPEC.md` section 2. Skip everything listed in `OBD-SPEC.md` section 8.

## 5. T26 (later)

Tapping the comfort CAN (T26) is a possible follow-up. Source: OVMS v3 `vehicle_vweup/src/vweup_t26.cpp` and the OVMS vweup docs (local clone read). Nothing below has been tested on our car.

### Purpose

- Stop or restart the charge at the limit, which would make the displayed limit the car's actual behaviour.
- Possibly read the car's own charge limit (profile byte 14, see below).
- T26 does **not** replace the OBD data. OVMS notes that power and current are not available over T26, so SoC, voltage and current still come from OBD.

### Hardware

- Comfort CAN ("T26A"), 100 kbit/s (OBD is 500 kbit/s). Needs its own CAN interface with raw frame access and writes; an ELM327 will not do.
- Connector under the front passenger seat (reachable by removing the OCU, or tilting the seat forward). Twisted pair, shielded CAT-5/6 cable, ground connected.
- The T26 numbering is not the usual IT scheme: top row 1-13, bottom row 14-26, both left to right.

| T26 pin | Signal |
|---|---|
| 1 | +12 V |
| 2 | CAN L |
| 14 | CAN H |
| 26 | GND |

- The bus sleeps. It wakes on events (door opening) and OVMS can wake it over CAN.

### Wake-up (needed before any profile access)

- `0x69E`: `14 51` (time request, wakes `0x400`).
- `0x43D` ring registration: `1D 02 02 00 00 14 00 00`, then after 50 ms `00 01 02 04 00 14 00 00`. Answer ring frames with `0x1D` in byte 0 on `0x43D`.
- Heartbeat every 1 s: `0x5A9` (8 zero bytes) and `0x5A7` (`data[0]` = `0x60` or `0x00`, `data[1]` = `0x16`).
- OVMS waits up to about 15 s for the wake-up.

### Charging profile ("profile0", PID `0x959`)

OVMS has no "set SoC limit". It reads and writes a 44-byte charging profile on `0x69E` (replies on `0x69C`):

- **Read request:** `90 04 19 59 27 00 00 01`. The reply is 8-byte frames, accepted only if bytes 34-39 read "Option".
- **Write:** header `90 20 29 59 2c 00 00 01`, then frames `d0`-`d3`. It echoes the profile it read with one byte changed, then re-reads it to confirm.

| Profile byte | Meaning (per OVMS) |
|---|---|
| 11 | Mode: 1 = charging allowed, 5 = also climate on battery, +2 = climate on |
| 13 | Max charge current in A |
| 14 | Logged by OVMS as "min SoC". Copied through, never changed |
| 25 | Remote climate temperature, `(T - 10) * 10` |

- OVMS's "official" start/stop (mode byte) sometimes reverts. OVMS works around it by setting max current to 1 A to stop and restoring the previous value to start. It keeps a minimum of 4 A so a charge restarts reliably.
- Byte 14 is the best lead for a car-side charge limit. Unconfirmed.

### Design consequences

- Reading the profile is read-only but still needs the wake-up and heartbeat.
- Writing is a read-modify-write of the whole profile. It can change charge current and climate settings if done wrong. Always verify by reading back, as OVMS does, and do not write first.
- Keep the limit as a value plus an optional "enforce" action, so T26 can be added without changing the display logic.
- The OVMS docs list charge control as "tba", while the code has working start/stop. Treat both as unverified until tested.

## 6. Open items

1. Capture a charge with a timer SoC max below 100 set in the car. Check whether `1DD1` and `1DE4` follow it.
2. Confirm pack capacity (`usable_kwh` at various SoC, or a fixed value) for the time estimate.
3. Compare the own time estimate with `chg_remain` over a full charge, including the taper.
4. Pick the display target.
5. Comfort-bus access: get a 100 kbit/s CAN interface and the T26 cable.
6. Read-only experiment on T26: wake the bus, read profile0, and check whether byte 14 follows a limit set in the car or the app. Compare with `1DD1`.
7. Only after that, decide on enforcement (current drop to 1 A as OVMS does).
