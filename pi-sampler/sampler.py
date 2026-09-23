#!/usr/bin/env python3
"""House power sampler — Pi side.

Reads two 0-5 V DC CTs (one per 120 V mains leg) via the Waveshare ADS1256
over SPI and publishes a versioned JSON reading at a fixed rate:

    {"v": 1, "ts": 1727123456.123, "leg1_A": 12.34, "leg2_A": 11.02,
     "range_setting": 100, "host": "adcpi1"}

Transport: MQTT first, HTTP POST fallback. The sampling loop never blocks on
the network: readings go through a bounded queue (oldest dropped on
backpressure) drained by a publisher thread. UI polling can never slow sampling.

Calibration (Loulensy clamps, NOT SCT-013 — no burden/bias math):
    volts = raw * 5.0 / 0x7FFFFF        # single-ended, positive half only
    amps  = volts / 5.0 * RANGE_AMPS    # RANGE_AMPS must match the clamp jumper
Power (computed downstream, never here):  W = 120 * I per leg.

Config: environment, optionally from /etc/house-power/sampler.env.
"""

import json
import logging
import os
import queue
import signal
import socket
import sys
import threading
import time
import urllib.request

SCHEMA_VERSION = 1
FULL_SCALE = 0x7FFFFF
VREF = 5.0
VALID_RANGES = (100, 150, 200)

log = logging.getLogger("sampler")


def getenv(name, default=None):
    return os.environ.get(name, default)


def load_env_file(path="/etc/house-power/sampler.env"):
    """Load KEY=VALUE lines (no secrets in git; this file lives on the Pi)."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    except FileNotFoundError:
        pass


def raw_to_volts(raw):
    """ADC code -> volts. Clamps to the meaningful 0..5 V single-ended range."""
    raw = max(0, min(FULL_SCALE, int(raw)))
    return raw * VREF / FULL_SCALE


def volts_to_amps(volts, range_amps, calibration=1.0):
    """Loulensy 0-5 V DC transducer: amps = volts / 5 * jumper range.

    calibration is a measured scale correction (default 1.0 = none).
    Set it from a known load: CALIBRATION = true_amps / reported_amps.
    E.g. 1500 W kettle at 124 V mains = 12.9 A true vs 9.8 A reported
    -> CALIBRATION ~= 1.3 (verify with a plug meter before trusting).
    """
    return max(0.0, volts / VREF * range_amps * calibration)


def parse_channels(spec, default):
    try:
        chs = [int(c) for c in spec.split(",")]
        if all(0 <= c <= 7 for c in chs) and chs:
            return chs
    except (ValueError, AttributeError):
        pass
    return default


class SamplerConfig:
    def __init__(self):
        self.mqtt_host = getenv("MQTT_HOST", "localhost")
        self.mqtt_port = int(getenv("MQTT_PORT", "1883"))
        self.mqtt_topic = getenv("MQTT_TOPIC", "house/power/v1")
        self.http_url = getenv("HTTP_URL", "")  # e.g. http://srv:8000/api/v1/readings
        self.range_amps = int(getenv("RANGE_AMPS", "100"))
        if self.range_amps not in VALID_RANGES:
            raise ValueError(f"RANGE_AMPS must be one of {VALID_RANGES}")
        self.publish_hz = float(getenv("PUBLISH_HZ", "1"))
        self.adc_drate = int(getenv("ADC_DRATE", "0x82"), 16)  # 100 SPS default
        self.leg1_ch = parse_channels(getenv("LEG1_CHANNELS"), [0, 2, 4, 6])
        self.leg2_ch = parse_channels(getenv("LEG2_CHANNELS"), [1, 3, 5, 7])
        self.calibration = float(getenv("CALIBRATION", "1.0"))
        if not 0.5 <= self.calibration <= 2.0:
            raise ValueError("CALIBRATION must be within 0.5..2.0")
        self.host = getenv("HOST_OVERRIDE", socket.gethostname())


def build_payload(ts, leg1_a, leg2_a, range_amps, host):
    return {
        "v": SCHEMA_VERSION,
        "ts": ts,
        "leg1_A": round(leg1_a, 3),
        "leg2_A": round(leg2_a, 3),
        "range_setting": range_amps,
        "host": host,
    }


class ADCReader:
    """Thin wrapper over the vendored Waveshare driver (lazy import: tests mock it).

    Robustness: each clamp output is fanned out to 4 ADC channels carrying the
    SAME voltage, but a share of single conversions come back as code 0 (stale
    / collided SPI transfer — the vendor driver maps its negative-code quirk
    to 0 too). So per channel we retry once on exact 0, and per leg we take
    the MEDIAN of the 4 fanned channels instead of the mean: one bad read (0
    or a bit-flip spike) can no longer move the published value.
    """

    def __init__(self, drate):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "ads1256_vendor"))
        import ADS1256  # noqa: E402

        self._mod = ADS1256
        self.adc = ADS1256.ADS1256()
        # NOTE: some boards (incl. ours) fail the chip-ID check yet read fine —
        # the legacy loop ignored init()'s return for years. Warn, don't die.
        if self.adc.ADS1256_init() != 0:
            log.warning("ADS1256_init reported failure; continuing anyway (legacy behavior)")
        self.adc.ADS1256_ConfigADC(0, drate)
        log.info("ADS1256 ready (drate=0x%02x)", drate)
        self.zeros_retried = 0
        self.zeros_kept = 0

    def read_channel(self, ch):
        # A driven 0-5 V input never converts to exactly 0 (1 LSB ~ 0.6 uV,
        # noise floor is far above that), so code 0 means a bad transfer.
        code = self.adc.ADS1256_GetChannalValue(ch)
        if code == 0:
            self.zeros_retried += 1
            code = self.adc.ADS1256_GetChannalValue(ch)
            if code == 0:
                self.zeros_kept += 1
        return code

    def read_leg_amps(self, channels, range_amps, calibration=1.0):
        volts = sorted(raw_to_volts(self.read_channel(c)) for c in channels)
        n = len(volts)
        med = volts[n // 2] if n % 2 else (volts[n // 2 - 1] + volts[n // 2]) / 2
        return volts_to_amps(med, range_amps, calibration)


class Publisher(threading.Thread):
    """Drains the queue: MQTT primary, HTTP fallback. Never raises into the loop."""

    def __init__(self, cfg):
        super().__init__(daemon=True, name="publisher")
        self.cfg = cfg
        self.q = queue.Queue(maxsize=300)
        self._stop = threading.Event()
        self._mqtt = None

    def publish(self, payload):
        try:
            self.q.put_nowait(payload)
        except queue.Full:
            try:
                self.q.get_nowait()  # drop oldest, keep the loop realtime
                self.q.put_nowait(payload)
            except queue.Empty:
                pass

    def stop(self):
        self._stop.set()

    def _mqtt_ensure(self):
        if self._mqtt is not None:
            return True
        try:
            import paho.mqtt.client as mqtt

            client = mqtt.Client()
            client.will_set(self.cfg.mqtt_topic + "/status",
                            json.dumps({"host": self.cfg.host, "online": False}))
            client.connect(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=30)
            client.loop_start()
            self._mqtt = client
            log.info("MQTT connected to %s:%d", self.cfg.mqtt_host, self.cfg.mqtt_port)
            return True
        except Exception as e:  # noqa: BLE001 - must survive without MQTT
            log.warning("MQTT unavailable (%s); using HTTP fallback", e)
            self._mqtt = None
            return False

    def _send_http(self, payload):
        if not self.cfg.http_url:
            return False
        try:
            req = urllib.request.Request(
                self.cfg.http_url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                return 200 <= r.status < 300
        except Exception as e:  # noqa: BLE001
            log.warning("HTTP publish failed: %s", e)
            return False

    def run(self):
        while not self._stop.is_set():
            try:
                payload = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            body = json.dumps(payload)
            sent = False
            if self._mqtt_ensure():
                try:
                    self._mqtt.publish(self.cfg.mqtt_topic, body, qos=0)
                    sent = True
                except Exception as e:  # noqa: BLE001
                    log.warning("MQTT publish failed (%s); closing", e)
                    try:
                        self._mqtt.loop_stop()
                    finally:
                        self._mqtt = None
            if not sent:
                self._send_http(payload)
        if self._mqtt is not None:
            try:
                self._mqtt.loop_stop()
            except Exception:  # noqa: BLE001, S110
                pass


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    load_env_file()
    cfg = SamplerConfig()
    log.info("range=%dA cal=%.3f leg1=%s leg2=%s mqtt=%s:%d http=%s",
             cfg.range_amps, cfg.calibration, cfg.leg1_ch, cfg.leg2_ch,
             cfg.mqtt_host, cfg.mqtt_port, cfg.http_url or "(none)")

    reader = ADCReader(cfg.adc_drate)
    pub = Publisher(cfg)
    pub.start()

    stop = threading.Event()

    def _sig(*_):
        stop.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    period = 1.0 / cfg.publish_hz
    n = 0
    try:
        while not stop.is_set():
            t0 = time.time()
            try:
                leg1 = reader.read_leg_amps(cfg.leg1_ch, cfg.range_amps, cfg.calibration)
                leg2 = reader.read_leg_amps(cfg.leg2_ch, cfg.range_amps, cfg.calibration)
                pub.publish(build_payload(t0, leg1, leg2, cfg.range_amps, cfg.host))
                n += 1
                if n % 60 == 0:
                    log.info("reads=%d zero_retries=%d zero_kept=%d leg1=%.2fA leg2=%.2fA",
                             n, reader.zeros_retried, reader.zeros_kept, leg1, leg2)
            except Exception:  # noqa: BLE001 - sampler loop must never die
                log.exception("read failed")
            dt = time.time() - t0
            if dt < period:
                stop.wait(period - dt)
            else:
                log.warning("loop overrun: %.3fs > %.3fs period", dt, period)
    finally:
        pub.stop()
        try:
            import RPi.GPIO as GPIO

            GPIO.cleanup()
        except (ImportError, RuntimeError):
            pass


if __name__ == "__main__":
    main()
