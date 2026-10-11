"""Minimal ELM327 interface over a serial port."""
import sys
import serial


class ELM327:
    def __init__(self, port="COM5", baud=38400, timeout=3):
        self.ser = serial.Serial(port, baud, timeout=timeout)

    def cmd(self, command):
        """Send an AT/OBD command, return response lines (without echo/prompt)."""
        self.ser.reset_input_buffer()
        self.ser.write((command + "\r").encode("ascii"))
        buf = b""
        while not buf.endswith(b">"):
            chunk = self.ser.read(1)
            if not chunk:
                break  # timeout
            buf += chunk
        lines = [l.strip() for l in buf.decode("ascii", "replace").replace(">", "").split("\r")]
        return [l for l in lines if l and l != command]

    def init(self):
        self.cmd("ATZ")      # reset
        self.cmd("ATE0")     # echo off
        self.cmd("ATL0")     # linefeeds off
        self.cmd("ATH1")     # show CAN headers
        self.cmd("ATSP0")    # auto protocol

    def close(self):
        self.ser.close()


if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else "COM5"
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 38400
    elm = ELM327(port, baud)
    print("ATZ  ->", elm.cmd("ATZ"))
    elm.init()
    print("ATI  ->", elm.cmd("ATI"))
    print("AT@1 ->", elm.cmd("AT@1"))
    print("ATRV ->", elm.cmd("ATRV"))   # battery voltage
    print("0100 ->", elm.cmd("0100"))   # supported PIDs (needs ignition on)
    elm.close()
