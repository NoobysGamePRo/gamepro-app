"""
GamePRo Controller — serial communication wrapper.

Wraps pyserial to send button commands and read sensor data from the
GamePRo Arduino (GamePRo_Standard_4.4+, 9600 baud).

Arduino command protocol (single character sent via serial):
  'a' = press A        'b' = press B       'x' = press X      'y' = press Y
  'e' = press Up       'd' = press Down    's' = press Left   'f' = press Right
  '8' = hold Up        '2' = hold Down     '4' = hold Left    '6' = hold Right
  '0' = release all    'S' = soft reset 1  'Z' = soft reset 2  'W' = wonder trade
  'C' = read light value (0-255 raw byte)

ACK protocol (v4.4+):
  Every action command returns 'K' when the servo has finished moving.
  _send() blocks until that 'K' is received (or the 1-second timeout).
  This replaces all fixed time.sleep() delays in the Python scripts for
  button presses — timing is now servo-accurate rather than PC-sleep-accurate.
"""

import serial
import serial.tools.list_ports
import threading


class GameProController:
    """Serial communication wrapper for the GamePRo Arduino hardware."""

    def __init__(self, port: str, baud: int = 9600):
        self._serial = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1,          # 1-second read timeout for ACK and sensor reads
            dsrdtr=False,       # keep DTR low — prevents Arduino auto-reset on connect
        )
        self._lock = threading.Lock()
        self._ldr_display_callback = None   # set by app to update the LDR dial

    # ── Button commands ──────────────────────────────────────────────────────

    def press_a(self):      self._send('a')
    def press_b(self):      self._send('b')
    def press_x(self):      self._send('x')
    def press_y(self):      self._send('y')

    def press_up(self):     self._send('e')
    def press_down(self):   self._send('d')
    def press_left(self):   self._send('s')
    def press_right(self):  self._send('f')

    def hold_up(self):      self._send('8')
    def hold_down(self):    self._send('2')
    def hold_left(self):    self._send('4')
    def hold_right(self):   self._send('6')

    def release_all(self):  self._send('0')
    def soft_reset(self):   self._send('S')
    def soft_reset_z(self): self._send('Z')
    def wonder_trade(self): self._send('W')

    # ── Calibration backup / restore (firmware v4.5+ / Switch v1.2+) ──────────

    def read_calibration(self) -> 'Optional[str]':
        """Request all calibration values from the Arduino.

        Sends 'q'; the Arduino responds with a comma-separated string of 25
        integers terminated by \\n (e.g. "80,100,90,...,200\\r\\n").

        Returns the stripped CSV string, or None if the firmware does not
        support the command (old firmware) or the read times out.
        """
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write(b'q')
            line = self._serial.readline()   # blocks until \\n or port timeout
            decoded = line.decode('ascii', errors='ignore').strip()
            return decoded if decoded else None

    def write_calibration(self, csv_str: str) -> bool:
        """Send calibration values to the Arduino and save them to EEPROM.

        Sends 'w' immediately followed by csv_str + '\\n'.  The Arduino parses
        the 25 comma-separated values, applies them, writes to EEPROM, then
        sends ACK 'K'.

        csv_str — raw CSV string previously returned by read_calibration().
        Returns True if the Arduino acknowledged with 'K'.
        Requires firmware v4.5+ (3DS) or Switch v1.2+.
        """
        if not csv_str:
            return False
        payload = ('w' + csv_str + '\n').encode('ascii')
        with self._lock:
            self._serial.write(payload)
            ack = self._serial.read(1)
            return ack == b'K'

    # ── Sensor reading ────────────────────────────────────────────────────────

    def read_light_value(self) -> int:
        """
        Ask Arduino to read the LDR and return a value in the range 0-1020.

        The Arduino averages 4 ADC samples (each 0-1023), divides by 4 to
        fit in one byte (0-255), and sends it as a raw byte via Serial.write().
        We multiply by 4 here to restore the approximate original scale.

        Returns 0 on timeout.
        """
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write(b'C')
            data = self._serial.read(1)
            value = data[0] * 4 if data else 0
        if self._ldr_display_callback:
            try:
                self._ldr_display_callback(value)
            except Exception:
                pass
        return value

    # ── Connection management ─────────────────────────────────────────────────

    def is_open(self) -> bool:
        return self._serial.is_open

    def close(self):
        if self._serial.is_open:
            self._serial.close()

    # ── Port discovery ────────────────────────────────────────────────────────

    @staticmethod
    def list_ports() -> list:
        """
        Return list of (device, description) tuples sorted by port number.
        Example: [('COM3', 'Arduino Nano (COM3)'), ('COM9', 'USB Serial Device (COM9)')]
        """
        ports = serial.tools.list_ports.comports()
        return sorted(
            [(p.device, p.description or p.device) for p in ports],
            key=lambda t: int(t[0].replace('COM', '')) if t[0].startswith('COM') else 0
        )

    @staticmethod
    def test_port(port: str) -> bool:
        """Try to open a port briefly to verify it is accessible. Returns True if OK."""
        try:
            s = serial.Serial(port, 9600, timeout=0.5)
            s.close()
            return True
        except serial.SerialException:
            return False

    # ── Internal ──────────────────────────────────────────────────────────────

    def fire(self, char: str):
        """
        Send a command byte without waiting for the ACK.  Use for manual
        one-off button presses where low latency matters more than
        synchronisation.  Flushes the input buffer first so stale ACK bytes
        from any previous no-wait send don't confuse a later _send() call.
        """
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write(char.encode('ascii'))

    def _send(self, char: str):
        """
        Send a single command byte and wait for the ACK byte ('K') from
        the Arduino. Blocks until ACK is received or the 1-second timeout
        expires (which means the Arduino did not respond — harmless but the
        caller's timing will be off).
        """
        with self._lock:
            self._serial.write(char.encode('ascii'))
            self._serial.read(1)    # wait for ACK 'K'
