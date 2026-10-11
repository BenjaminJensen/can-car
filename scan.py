"""Broad OBD poll across all VW e-up ECUs/PIDs documented in OVMS v3 vehicle_vweup
(vweup_obd.h / vweup_obd.cpp). This is the exploratory counterpart to car.py: car.py
holds the small set of metrics with confirmed decode formulas; this script polls
everything the OVMS source defines so we can log real responses and fill in/correct
decoders together.

Usage: python scan.py [PORT] [gen]   e.g. python scan.py COM3 2
Pipe to a file to review: python scan.py COM3 2 > scan_log.txt  (output file name is up to you)
(gen only affects car.py's default poll set; the AC/DC-current charger PIDs for
both generations returned nothing in scan_log4-7, so they are no longer polled.)
"""
import sys

from car import (
    ELM327, Car, Poll, u8, u16, s16, u24, s32,
    BAT_MGMT, CHG_MGMT, CHG, MFD,
    dec_chgmode, dec_soc_limits, dec_timer_def,
)

# --- ECUs not already defined in car.py -----------------------------------
MOT_ELEC = (0x7E0, 0x7E8)    # 01 engine/motor electronics
BRK = (0x713, 0x77D)         # 03 brake electronics
BRKBOOST = (0x73B, 0x743)    # 23 brake boost
STEER = (0x712, 0x71A)       # 44 steering
ELD = (0x7E6, 0x7EE)         # 51 electric drive
INF = (0x773, 0x7DD)         # 5F info electronics
BRKSENS = (0x762, 0x76A)     # AD brake sensors


def dec_soh_cac(d):
    # Layout seen in scan_log3-7 (identical every time): 4-byte header
    # (29 6e 00 54, last byte 0x54 = 84 = cell count), then one u8 per cell
    # (213-216 here), then 0xAA ISO-TP padding. Scale to Ah/% is UNKNOWN.
    n = d[3]
    cells = list(d[4:4 + n])
    if len(cells) < n:
        return None
    lo, hi = min(cells), max(cells)
    return {
        "cac_cells": n,
        "cac_min": lo,
        "cac_max": hi,
        "cac_mean": sum(cells) / n,
        "cac_weakest": [i + 1 for i, v in enumerate(cells) if v == lo],
    }


def dec_raw(d):
    """Fallback for PIDs with no confirmed decode formula yet: show raw hex
    so we can work out the formula from real readings."""
    return {"raw": d.hex(), "len": len(d)}


# --- confirmed decoders (formula taken directly from vweup_obd.cpp) -------

def dec_mot_soc_norm(d):
    return {"mot_soc_norm": u16(d) / 100.0}


def dec_soc_abs_u8_2_55(d):
    return {"soc_abs": u8(d) / 2.55}


def dec_bat_soc_abs(d):
    return {"bat_soc_abs": u8(d) / 2.5}


def dec_temp_amb(d):
    v = u8(d)
    return {} if v == 0 or v == 255 else {"temp_amb_c": v - 40.0}


def dec_temp_k10(d):
    # Kelvin*10 -> Celsius; confirmed for TEMP_DCDC / TEMP_PEM on MOT_ELEC
    return {"temp_c": u16(d) / 10.0 - 273.1}


def dec_speed(d):
    v = u8(d)
    return {} if v >= 250 else {"speed_kph": v}


def dec_power_mot(d):
    return {"power_mot_kw": s16(d) / 250.0}


def dec_accel(d):
    return {"accel_mps2": s16(d) / 1000.0}


def dec_vin(d):
    # byte0 is a non-ASCII prefix (data format id?); VIN is the next 17 bytes,
    # 'U' (0x55) padded after. Confirmed against a real VIN in scan_log2.txt.
    return {"vin": d[1:18].decode("ascii", errors="replace")}


def dec_bat_temp(d):
    return {"bat_temp_c": s16(d) / 64.0}


def dec_cell_v(d):
    return {"cell_v": u16(d) / 4096.0}


def dec_bat_soc_abs_mgmt(d):
    # byte0 only (0x028C); distinct scale from MOT_ELEC_SOC_ABS
    return {"bat_soc_abs": u8(d) / 2.5}


def dec_bat_odometer(d):
    v = u24(d, 1)
    return {} if v >= 10_000_000 else {"odometer_km": v}


def dec_energy_counters(d):
    return {
        "coulomb_recd_ah": s32(d, 0) * 0.0018204444,
        "coulomb_used_ah": -s32(d, 4) * 0.0018204444,
        "energy_recd_kwh": s32(d, 8) * 0.0001165084,
        "energy_used_kwh": -s32(d, 12) * 0.0001165084,
    }


def dec_temp_s16_64(key):
    # s16/64 confirmed for ELD_TEMP_MOT; sibling ELD temps (dcdc2/3, pew/pev/peu,
    # stat) land in the same tight 13.2-13.8C cluster under this formula in
    # scan_log2.txt, so treating them as confirmed too.
    return lambda d: {key: s16(d) / 64.0}


dec_eld_temp_mot = dec_temp_s16_64("eld_temp_mot_c")


def dec_bat_temp_extreme(key_temp, key_cell):
    # s16/64 for the temperature (matches dec_bat_temp) + a u16 cell index;
    # confirmed by cross-check: both max/min land tight around bat_temp in
    # scan_log2.txt (12.09C @ cell 9, 11.47C @ cell 13 vs bat_temp 11.77C).
    return lambda d: {key_temp: s16(d, 0) / 64.0, key_cell: u16(d, 2)}


def dec_mfd_odometer(d):
    # Guessed: u16*10, resolution ~10km. Cross-checked against bat_odometer
    # (69470 vs 69478 actual) in scan_log2.txt - MFD likely rounds to 10km.
    return {"mfd_odometer_km": u16(d) * 10}


def dec_dcdc_u(d):
    return {"dcdc_v": u16(d) / 512.0}


def dec_dcdc_i(d):
    return {"dcdc_a": u16(d) / 16.0}


def dec_chg_power_eff(d):
    v = u8(d)
    return {} if v > 250 else {"chg_eff_pct": v / 10.0 + 75.0}


def dec_chg_power_loss(d):
    return {"chg_loss_kw": u8(d) * 20.0 / 1000.0}


def dec_chg_temp_cooler(d):
    return {"chg_temp_cooler_c": u8(d) - 40.0}


def dec_serv_range(d):
    v = u16(d)
    return {} if v == 0 else {"serv_range_km": v}


def dec_serv_time(d):
    v = u16(d)
    return {} if v == 0 else {"serv_time_days": v}


def dec_ccs_status(d):
    return {"ccs_v": u16(d, 4) / 10.0, "ccs_a": u16(d, 10) / 10.0}


# --- GUESSED decoders: pattern matches a confirmed decoder on another PID
# of the same ECU, but the exact formula for THIS pid is not confirmed in
# vweup_obd.cpp. Flagged so we know to double check against the log.

def dec_guess_minus40(d):
    return {"temp_c_guess": u8(d) - 40.0}


def dec_guess_k10(d):
    return {"temp_c_guess": u16(d) / 10.0 - 273.1}


def dec_temp_k10_sentinel(key):
    # Same Kelvin*10 formula as dec_temp_k10, but treats exactly -40C as the
    # "sensor not fitted" sentinel (VW's standard bottom-of-range invalid
    # marker, same convention as the u8 -40 fields). Confirmed by cool2
    # returning the identical raw bytes (091b -> -40.0) in both an idle and
    # a driving log in scan_log2/3.txt, while cool5/cool_bat gave plausible
    # 12.2C/11.5C readings under the same formula.
    def f(d):
        v = u16(d) / 10.0 - 273.1
        return {} if v <= -39.99 else {key: v}
    return f


def dec_temp_k10_fixed_invalid(key):
    # mot_temp_cool1/3/4 read ~76.6C (raw ~0x0DA8-0x0DA9) in every log where they
    # answered (3, 5, 6) regardless of a 10C ambient: a fixed invalid code, not a
    # temperature. Everything else uses the normal Kelvin*10 formula.
    def f(d):
        raw = u16(d)
        v = raw / 10.0 - 273.1
        return {} if v <= -39.99 or 0x0DA0 <= raw <= 0x0DB0 else {key: v}
    return f


# --- Poll table -------------------------------------------------------------
# name, ecu, pid, decoder. decoder=dec_raw where vweup_obd.cpp didn't give us
# (or this fetch didn't confirm) an exact formula yet.

ALL_POLLS = [
    # Battery management (8C)
    Poll("bat_u", *BAT_MGMT, 0x1E3B, lambda d: {"bat_v": u16(d) / 4.0}),
    Poll("bat_i", *BAT_MGMT, 0x1E3D, lambda d: {"bat_a": -((u16(d) - 2044.0) / 4.0)}),
    Poll("bat_soc_abs", *BAT_MGMT, 0x028C, dec_bat_soc_abs_mgmt),
    Poll("bat_odometer", *BAT_MGMT, 0x02BD, dec_bat_odometer),
    Poll("bat_energy_counters", *BAT_MGMT, 0x1E32, dec_energy_counters),
    Poll("bat_cell_max", *BAT_MGMT, 0x1E33, dec_cell_v),
    Poll("bat_cell_min", *BAT_MGMT, 0x1E34, dec_cell_v),
    Poll("bat_temp", *BAT_MGMT, 0x2A0B, dec_bat_temp),
    Poll("bat_temp_max", *BAT_MGMT, 0x1E0E, dec_bat_temp_extreme("bat_temp_max_c", "bat_temp_max_cell")),
    Poll("bat_temp_min", *BAT_MGMT, 0x1E0F, dec_bat_temp_extreme("bat_temp_min_c", "bat_temp_min_cell")),
    Poll("bat_hist18", *BAT_MGMT, 0x74CF, dec_raw),         # charge history blob
    Poll("bat_soh_cac", *BAT_MGMT, 0x74CB, dec_soh_cac),    # per-cell capacity, u8 x 84
    Poll("bat_soh_hist", *BAT_MGMT, 0x74CC, dec_raw),       # Ah history blob
    Poll("bat_hist01", *BAT_MGMT, 0x74BE, dec_raw),         # standing time blob
    Poll("bat_hist19", *BAT_MGMT, 0x74D0, dec_raw),         # charge time blob

    # Charge management (BD)
    Poll("soc", *CHG_MGMT, 0x1DD0, lambda d: {"soc": u8(d) / 2.0}),
    Poll("chg_mode", *CHG_MGMT, 0x1DD6, dec_chgmode),
    Poll("soc_limits", *CHG_MGMT, 0x1DD1, dec_soc_limits),
    Poll("timer_def", *CHG_MGMT, 0x1DD4, dec_timer_def),
    Poll("chg_remain", *CHG_MGMT, 0x1DE4, lambda d: {} if u8(d) == 127 else {"chg_remaining_min": u8(d) * 5}),
    Poll("chg_lv_pwrstate", *CHG_MGMT, 0x1DEC, dec_raw),
    Poll("chg_lv_autochg", *CHG_MGMT, 0x1DED, dec_raw),
    Poll("chg_ccs_status", *CHG_MGMT, 0x1DEF, dec_ccs_status),
    Poll("chg_test_1dd7", *CHG_MGMT, 0x1DD7, dec_raw),
    Poll("chg_test_1dda", *CHG_MGMT, 0x1DDA, dec_raw),
    Poll("chg_test_1de6", *CHG_MGMT, 0x1DE6, dec_raw),

    # HV charger (C6)
    Poll("chg_heater_i", *CHG, 0x150B, dec_raw),
    Poll("chg_power_eff", *CHG, 0x15D6, dec_chg_power_eff),
    Poll("chg_power_loss", *CHG, 0x15E1, dec_chg_power_loss),
    Poll("chg_temp_brd", *CHG, 0x15D8, dec_guess_minus40),
    Poll("chg_temp_pfccoil", *CHG, 0x15D9, dec_guess_minus40),
    Poll("chg_temp_dcdccoil", *CHG, 0x15DA, dec_guess_minus40),
    Poll("chg_temp_cooler", *CHG, 0x15E2, dec_chg_temp_cooler),
    Poll("chg_temp_dcdcpl", *CHG, 0x15EC, dec_guess_minus40),
    Poll("chg_socket", *CHG, 0x1DDA, dec_raw),
    Poll("chg_sock_stats", *CHG, 0x1DE8, dec_raw),
    # DC bus voltage seen at the charger: ~pack V when the HV bus is live, 0 when not.
    # (AC mains V/I and DC current PIDs for gen1 and gen2 never answered, incl. while charging.)
    Poll("chg_dc_u", *CHG, 0x1DA7, lambda d: {"dc_v": u16(d)}),

    # Multi-function display (17)
    Poll("range", *MFD, 0x22E0, lambda d: {"range_km": u16(d)}),
    Poll("range_cap", *MFD, 0x22E4, lambda d: {} if u16(d) == 511 else {"usable_kwh": u16(d) / 10.0}),
    Poll("mfd_odometer", *MFD, 0x2203, dec_mfd_odometer),
    Poll("mfd_serv_range", *MFD, 0x2260, dec_serv_range),
    Poll("mfd_serv_time", *MFD, 0x2261, dec_serv_time),

    # Motor electronics (01)
    Poll("mot_state", *MOT_ELEC, 0x14D9, dec_raw),
    Poll("mot_drivemode", *MOT_ELEC, 0x1616, dec_raw),
    Poll("mot_soc_norm", *MOT_ELEC, 0x1164, dec_mot_soc_norm),
    Poll("mot_soc_abs", *MOT_ELEC, 0xF45B, dec_soc_abs_u8_2_55),
    Poll("mot_temp_amb", *MOT_ELEC, 0xF446, dec_temp_amb),
    Poll("mot_temp_dcdc", *MOT_ELEC, 0x116F, dec_temp_k10),
    Poll("mot_temp_pem", *MOT_ELEC, 0x1116, dec_temp_k10),
    # Kelvin*10. cool5 (matches PEM temp) and cool_bat (matches pack temp) are
    # confirmed; cool2 is a "not fitted" sentinel; cool1/3/4 are a fixed
    # ~76.6C invalid code (see dec_temp_k10_fixed_invalid).
    Poll("mot_temp_cool1", *MOT_ELEC, 0x1611, dec_temp_k10_fixed_invalid("mot_temp_cool1_c")),
    Poll("mot_temp_cool2", *MOT_ELEC, 0x1612, dec_temp_k10_sentinel("mot_temp_cool2_c")),
    Poll("mot_temp_cool3", *MOT_ELEC, 0x1613, dec_temp_k10_fixed_invalid("mot_temp_cool3_c")),
    Poll("mot_temp_cool4", *MOT_ELEC, 0x1614, dec_temp_k10_fixed_invalid("mot_temp_cool4_c")),
    Poll("mot_temp_cool5", *MOT_ELEC, 0x1615, dec_temp_k10_sentinel("mot_temp_cool5_c")),
    Poll("mot_temp_cool_bat", *MOT_ELEC, 0x1169, dec_temp_k10_sentinel("mot_temp_cool_bat_c")),
    Poll("mot_vin", *MOT_ELEC, 0xF802, dec_vin),       # multi-frame; verify reassembly
    Poll("mot_speed", *MOT_ELEC, 0xF40D, dec_speed),
    Poll("mot_power", *MOT_ELEC, 0x147E, dec_power_mot),
    Poll("mot_accel", *MOT_ELEC, 0x12B0, dec_accel),

    # Brake electronics (03)
    Poll("brk_tpms", *BRK, 0x1821, dec_raw),

    # Brake boost (23)
    Poll("brkboost_temp_ecu", *BRKBOOST, 0x028D, dec_guess_minus40),
    Poll("brkboost_temp_acc", *BRKBOOST, 0x4E06, dec_guess_minus40),

    # Steering (44)
    Poll("steer_temp", *STEER, 0x0295, dec_guess_minus40),

    # Electric drive (51)
    # single byte, u8-40; 0xFF seen consistently across idle and driving logs - treat as "not fitted" sentinel
    Poll("eld_temp_cool", *ELD, 0xF405, lambda d: {} if u8(d) == 0xFF else {"eld_temp_cool_c": u8(d) - 40.0}),
    Poll("eld_temp_mot", *ELD, 0x3E94, dec_eld_temp_mot),
    Poll("eld_temp_dcdc1", *ELD, 0x3EB5, dec_temp_s16_64("eld_temp_dcdc1_c")),
    Poll("eld_temp_dcdc2", *ELD, 0x464E, dec_temp_s16_64("eld_temp_dcdc2_c")),
    Poll("eld_temp_dcdc3", *ELD, 0x464F, dec_temp_s16_64("eld_temp_dcdc3_c")),
    Poll("eld_temp_pew", *ELD, 0x4662, dec_temp_s16_64("eld_temp_pew_c")),
    Poll("eld_temp_pev", *ELD, 0x4663, dec_temp_s16_64("eld_temp_pev_c")),
    Poll("eld_temp_peu", *ELD, 0x4664, dec_temp_s16_64("eld_temp_peu_c")),
    Poll("eld_temp_stat", *ELD, 0x4672, dec_temp_s16_64("eld_temp_stat_c")),
    Poll("eld_dcdc_u", *ELD, 0x465C, dec_dcdc_u),
    Poll("eld_dcdc_i", *ELD, 0x465B, dec_dcdc_i),

    # Info electronics (5F)
    Poll("inf_temp_pcb", *INF, 0x1863, dec_guess_minus40),
    Poll("inf_temp_audio", *INF, 0x1866, dec_guess_minus40),

    # Brake sensors (AD)
    Poll("brksens_temp", *BRKSENS, 0x1024, dec_guess_minus40),
]

if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else "COM5"
    gen = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    car = Car(ELM327(port), gen=gen)
    car.polls = ALL_POLLS
    car.setup()

    RETRIES = 2   # the motor ECU randomly drops single PIDs while awake
    for p in car.polls:
        r = car.read(p)
        for _ in range(RETRIES):
            if r is not None:
                break
            r = car.read(p)
        print(f"{p.name:20s} tx={p.tx:03X} rx={p.rx:03X} pid={p.pid:04X} ->", r)
