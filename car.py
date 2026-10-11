"""VW e-up poll table and decoders (prototype; port to constexpr C++ tables later).

Source: OVMS v3 vehicle_vweup (vweup_obd.h / vweup_obd.cpp). Not yet tested on a car.
All requests are UDS ReadDataByIdentifier (service 0x22) over ISO-TP on the OBD port.
"""
from dataclasses import dataclass
from typing import Callable, Optional

from elm import ELM327


def u8(d, o=0):
    return d[o]


def u16(d, o=0):
    return (d[o] << 8) | d[o + 1]


@dataclass(frozen=True)
class Poll:
    name: str
    tx: int                      # request CAN ID
    rx: int                      # response CAN ID
    pid: int                     # UDS data identifier
    decode: Callable[[bytes], dict]


# ECU request/response IDs
BAT_MGMT = (0x7E5, 0x7ED)        # ECU 8C battery management
CHG_MGMT = (0x765, 0x7CF)        # ECU BD HV charge management
CHG = (0x744, 0x7AE)             # ECU C6 HV charger
MFD = (0x714, 0x77E)             # ECU 17 multi-function display


def dec_soc(d):
    # Matches the instrument cluster SoC; 0.5 % steps
    return {"soc": u8(d) / 2.0}


def dec_range(d):
    # Displayed range [km]; OVMS only polls this while driving (ignition on)
    return {"range_km": u16(d)}


def dec_range_cap(d):
    v = u16(d)
    return {} if v == 511 else {"usable_kwh": v / 10.0}


def dec_bat_u(d):
    return {"bat_v": u16(d) / 4.0}


def dec_bat_i(d):
    # Positive = battery discharging, negative = charging
    return {"bat_a": -((u16(d) - 2044.0) / 4.0)}


def dec_chgmode(d):
    # byte0: 0=none, 1..3=AC, >=4=DC; byte1: timer (scheduled charge) mode active
    mode = u8(d, 0)
    kind = "none" if mode == 0 else ("dc" if mode >= 4 else "ac")
    return {"chg_mode_raw": mode, "chg_kind": kind, "chg_timer_mode": bool(u8(d, 1))}


def dec_soc_limits(d):
    # Scheduled-charge (timer mode) SoC limits. byte0 = min, byte1 = max
    return {"timer_soc_min": u8(d, 0), "timer_soc_max": u8(d, 1)}


def dec_timer_def(d):
    return {"chg_timer_defined": u8(d) != 0}


def dec_remaining(d):
    v = u8(d)
    return {} if v == 127 else {"chg_remaining_min": v * 5}


# Charging power is computed from voltage * current (see below). Gen2 (2020+) PIDs:
def dec_ac_u_gen2(d):
    return {"ac1_v": u16(d, 0), "ac2_v": u16(d, 2)}


def dec_ac_i_gen2(d):
    return {"ac1_a": u8(d, 0) / 10.0, "ac2_a": u8(d, 1) / 10.0}


def dec_ac_u_gen1(d):
    return {"ac1_v": u16(d, 0)}


def dec_ac_i_gen1(d):
    return {"ac1_a": u8(d, 0) / 10.0}


def dec_dc_u(d):
    return {"dc_v": u16(d)}


def dec_dc_i_gen1(d):
    return {"dc_a": (u16(d) - 510.0) / 5.0}


def dec_dc_i_gen2(d):
    # NOTE: OVMS decodes VWUP2_CHG_DC_I elsewhere; verify scaling on a car.
    return {"dc_a": (u16(d) - 510.0) / 5.0}


COMMON = [
    Poll("soc",         *CHG_MGMT, 0x1DD0, dec_soc),
    Poll("bat_u",       *BAT_MGMT, 0x1E3B, dec_bat_u),
    Poll("bat_i",       *BAT_MGMT, 0x1E3D, dec_bat_i),
    Poll("range",       *MFD,      0x22E0, dec_range),
    Poll("range_cap",   *MFD,      0x22E4, dec_range_cap),
    Poll("chg_mode",    *CHG_MGMT, 0x1DD6, dec_chgmode),
    Poll("soc_limits",  *CHG_MGMT, 0x1DD1, dec_soc_limits),
    Poll("timer_def",   *CHG_MGMT, 0x1DD4, dec_timer_def),
    Poll("chg_remain",  *CHG_MGMT, 0x1DE4, dec_remaining),
]

GEN1 = [   # e-up before 2020
    Poll("ac_u", *CHG, 0x1DA9, dec_ac_u_gen1),
    Poll("ac_i", *CHG, 0x1DA8, dec_ac_i_gen1),
    Poll("dc_u", *CHG, 0x1DA7, dec_dc_u),
    Poll("dc_i", *CHG, 0x4237, dec_dc_i_gen1),
]

GEN2 = [   # e-up from 2020
    Poll("ac_u", *CHG, 0x41FC, dec_ac_u_gen2),
    Poll("ac_i", *CHG, 0x41FB, dec_ac_i_gen2),
    Poll("dc_u", *CHG, 0x41F8, dec_dc_u),
    Poll("dc_i", *CHG, 0x41F9, dec_dc_i_gen2),
]


def derive(m):
    """Derived metrics: charging power [kW] from the raw voltages/currents."""
    ac = (m.get("ac1_v", 0) * m.get("ac1_a", 0) + m.get("ac2_v", 0) * m.get("ac2_a", 0)) / 1000.0
    dc = m.get("dc_v", 0) * m.get("dc_a", 0) / 1000.0
    kind = m.get("chg_kind")
    m["chg_power_kw"] = round(dc if kind == "dc" else ac if kind == "ac" else 0.0, 2)
    if "bat_v" in m and "bat_a" in m:
        m["bat_power_kw"] = round(m["bat_v"] * m["bat_a"] / 1000.0, 2)
    return m


def hex_bytes(lines):
    """Flatten ELM327 response lines (ATH0, ATS0) to bytes; handles multi-frame '0:' lines."""
    out = bytearray()
    for line in lines:
        if ":" in line:
            line = line.split(":", 1)[1]
        elif len(line) <= 3 and all(c in "0123456789ABCDEF" for c in line):
            continue  # multi-frame length line, e.g. "00B"
        try:
            out += bytes.fromhex(line.replace(" ", ""))
        except ValueError:
            return None   # NO DATA, ?, CAN ERROR, etc.
    return bytes(out)


class Car:
    def __init__(self, elm: ELM327, gen: int = 2):
        self.elm = elm
        self.polls = COMMON + (GEN2 if gen == 2 else GEN1)
        self.metrics = {}
        self._cur = None

    def setup(self):
        e = self.elm
        for c in ("ATZ", "ATE0", "ATL0", "ATH0", "ATS0", "ATSP6"):   # 6 = ISO 15765-4, 11-bit, 500k
            e.cmd(c)
        self._cur = None

    def read(self, p: Poll) -> Optional[dict]:
        e = self.elm
        if self._cur != (p.tx, p.rx):
            e.cmd(f"ATSH{p.tx:03X}")
            e.cmd(f"ATCRA{p.rx:03X}")
            e.cmd(f"ATFCSH{p.tx:03X}")
            self._cur = (p.tx, p.rx)
        resp = hex_bytes(e.cmd(f"22{p.pid:04X}"))
        if not resp or resp[0] != 0x62 or u16(resp, 1) != p.pid:
            return None   # negative response (7F ...), no data, or wrong frame
        return p.decode(resp[3:])

    def poll_all(self):
        for p in self.polls:
            r = self.read(p)
            if r:
                self.metrics.update(r)
        return derive(self.metrics)


if __name__ == "__main__":
    import sys
    port = sys.argv[1] if len(sys.argv) > 1 else "COM5"
    gen = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    car = Car(ELM327(port), gen)
    car.setup()
    for p in car.polls:
        print(f"{p.name:11s} ->", car.read(p))
    print(car.poll_all())
