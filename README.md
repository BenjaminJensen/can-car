# can-car

A custom in-car display for a VW e-up, built around a Raspberry Pi Pico 2 W (RP2350 + WiFi) driving two GC9A01 240x240 round LCDs.

Inspired by [Open Vehicle Monitoring System 3](https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3) (OVMS), in particular its `vehicle_vweup` module, which documents the e-up's ECU addresses, PIDs and scaling.

## Plan

1. **Prototype on PC in Python (current stage):** talk to the car through an ELM327 v1.5 adapter (COM5), and work out which values we can read and how to decode them.
2. **Firmware in modern C++ on the Pico 2 W (Pico SDK, no MicroPython):** reimplement the proven poll table and decoders in C++, replace the ELM327 serial transport with `can2040`, and add the display code.

## Hardware

| Part | Notes |
|------|-------|
| Raspberry Pi Pico 2 W | RP2350, no built-in CAN controller |
| 2x GC9A01 round LCD, 240x240 | SPI; share SCK/MOSI, separate CS per display |
| CAN interface | `can2040` (PIO software CAN) with MCP2562 transceivers (built-in level shifter via VIO, so 3.3 V logic from the Pico) |
| Power | 12 V from OBD pin 16 (always live) via buck converter to 5 V; must sleep or power down when the car is off |
| ELM327 v1.5 | Prototyping only; serial 38400 8N1 on COM5 (clones may use 9600) |

## PIO allocation

The RP2350 has three PIO blocks:

- **CAN:** `can2040` on one PIO, serving two CAN buses (OBD port and, later, comfort CAN).
- **Displays:** a separate PIO drives the two GC9A01 screens.

To verify when wiring up: `can2040` is one instance per bus and takes over the state machines of the PIO block it runs on, so check that two buses really fit on a single PIO block and, if not, that the display PIO still has room. `can2040` is C and links directly into the C++ Pico SDK firmware.

## VW e-up notes

- The OBD port is behind VW's diagnostic gateway: no broadcast traffic (`ATMA` shows nothing useful). Data comes from UDS requests to individual ECUs (BMS, charger, motor controller, ...).
- OVMS also reads the comfort CAN via the T26A connector behind the dash; that is a possible later addition.
- Ignition must be on to query the car.

## Architecture

```
poll table (request ID, response ID, service/PID, decoder)
        |
   transport  <-- ELM327 serial now, CAN driver on the Pico later
        |
   metrics dict  (SoC, pack voltage, current, temperatures, ...)
        |
   display code (reads metrics only)
```

The Python code is a prototype and reference; it is not shipped. To make the port straightforward, keep the poll table as plain data (request ID, response ID, service/PID, byte offset, length, signedness, scale, offset, unit) and keep decoders as small pure functions of bytes, so they translate directly to C++ (`constexpr` tables, no dynamic allocation).

## Getting started

```
pip install pyserial
python elm.py COM5 38400
```

[elm.py](elm.py) is a minimal ELM327 wrapper. Running it resets the adapter and prints its ID, the battery voltage (`ATRV`) and a `0100` supported-PIDs query.

Last test (adapter not on a car): the ELM327 identified as v1.5, `ATRV` read 0.0 V and `0100` returned `SEARCHING...`.

## TODO

- [ ] Plug into the car, confirm ELM327 communication with ignition on
- [ ] Choose the first values to read (SoC, pack voltage/current, temperatures, charge power, range)
- [ ] Extract ECU addresses, PIDs and scaling from OVMS `vehicle_vweup`
- [ ] Poll table and decoders, with UDS handling (`ATSH`, `ATCRA`, `ATFCSH`)
- [ ] Wire up `can2040` + MCP2562 on the Pico; confirm PIO allocation for two buses plus displays
- [ ] Set up the C++ firmware project (Pico SDK, CMake, `can2040`)
- [ ] Display driver and UI for two GC9A01 screens
- [ ] Power management (sleep when the car is off)
