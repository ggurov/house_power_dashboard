"""Sampler unit tests — hardware is stubbed (spidev/RPi.GPIO/ADC), so these run
on the workstation. Verifies calibration math, schema, channel mapping,
and the never-block publish path."""

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(__file__))

# ---- Stub Pi-only hardware modules before importing sampler ----
spidev = types.ModuleType("spidev")
spidev.SpiDev = lambda *a: types.SimpleNamespace(  # noqa: E731
    writebytes=lambda *a: None, readbytes=lambda n: [0] * n,
    max_speed_hz=0, mode=0)
sys.modules["spidev"] = spidev

gpio = types.ModuleType("RPi.GPIO")
for name in ("BCM", "OUT", "IN", "HIGH", "LOW", "PUD_UP"):
    setattr(gpio, name, name)
gpio.setmode = gpio.setwarnings = gpio.setup = gpio.output = lambda *a, **k: None
gpio.input = lambda *a: 0
gpio.cleanup = lambda: None
rpi = types.ModuleType("RPi")
rpi.GPIO = gpio
sys.modules["RPi"] = rpi
sys.modules["RPi.GPIO"] = gpio

os.environ.setdefault("RANGE_AMPS", "100")
import sampler  # noqa: E402


class FakeADC:
    """Scriptable stand-in: codes maps channel -> list of successive values."""

    def __init__(self, codes=None):
        self.calls = []
        self.drate = None
        self._codes = codes or {}

    def ADS1256_init(self):
        return 0

    def ADS1256_ConfigADC(self, gain, drate):
        self.drate = drate

    def _default_code(self, ch):
        v = 2.5 if ch % 2 == 0 else 1.0
        return int(v * sampler.FULL_SCALE / sampler.VREF)

    def ADS1256_GetChannalValue(self, ch):
        self.calls.append(ch)
        vals = self._codes.get(ch)
        if vals:
            return vals.pop(0)
        return self._default_code(ch)


def make_reader(monkeypatch, codes=None):
    monkeypatch.setenv("RANGE_AMPS", "100")
    reader = sampler.ADCReader.__new__(sampler.ADCReader)
    reader.adc = FakeADC(codes)
    reader.zeros_retried = 0
    reader.zeros_kept = 0
    return reader


def test_raw_to_volts_endpoints():
    assert sampler.raw_to_volts(0) == 0.0
    assert sampler.raw_to_volts(sampler.FULL_SCALE) == pytest.approx(5.0)
    assert sampler.raw_to_volts(-99) == 0.0  # vendor negative-code quirk
    assert sampler.raw_to_volts(0xFFFFFF) == pytest.approx(5.0)


def test_volts_to_amps_ranges():
    assert sampler.volts_to_amps(5.0, 100) == 100.0
    assert sampler.volts_to_amps(2.5, 200) == 100.0
    assert sampler.volts_to_amps(5.0, 150) == 150.0


def test_median_uses_configured_channels(monkeypatch):
    reader = make_reader(monkeypatch)
    leg1 = reader.read_leg_amps([0, 2, 4, 6], 100)
    leg2 = reader.read_leg_amps([1, 3, 5, 7], 100)
    assert leg1 == pytest.approx(50.0, abs=0.01)
    assert leg2 == pytest.approx(20.0, abs=0.01)


def test_median_ignores_single_outlier(monkeypatch):
    """One spiked channel must not move the leg value (the bug we saw live)."""
    spike = sampler.FULL_SCALE  # bit-flip / clamped spike
    reader = make_reader(monkeypatch, {2: [spike], 5: [0, 0]})
    leg1 = reader.read_leg_amps([0, 2, 4, 6], 100)
    assert leg1 == pytest.approx(50.0, abs=0.01)
    leg2 = reader.read_leg_amps([1, 3, 5, 7], 100)
    assert leg2 == pytest.approx(20.0, abs=0.01)


def test_retry_on_zero_then_recover(monkeypatch):
    good = int(2.5 * sampler.FULL_SCALE / sampler.VREF)
    reader = make_reader(monkeypatch, {0: [0, good]})
    assert reader.read_channel(0) == good
    assert reader.zeros_retried == 1
    assert reader.zeros_kept == 0
    assert reader.adc.calls.count(0) == 2


def test_persistent_zero_is_kept(monkeypatch):
    reader = make_reader(monkeypatch, {0: [0, 0]})
    assert reader.read_channel(0) == 0
    assert reader.zeros_kept == 1


def test_payload_schema_v1():
    p = sampler.build_payload(1727123456.5, 12.3456, 1.0, 150, "adcpi1")
    assert p == {"v": 1, "ts": 1727123456.5, "leg1_A": 12.346,
                 "leg2_A": 1.0, "range_setting": 150, "host": "adcpi1"}
    json.dumps(p)  # must be JSON-serializable


def test_config_rejects_bad_range(monkeypatch):
    monkeypatch.setenv("RANGE_AMPS", "300")
    with pytest.raises(ValueError):
        sampler.SamplerConfig()


def test_publisher_never_blocks_and_drops_oldest(monkeypatch):
    monkeypatch.setenv("RANGE_AMPS", "100")
    cfg = sampler.SamplerConfig()
    cfg.http_url = ""  # no network in tests
    pub = sampler.Publisher(cfg)
    pub.q = sampler.queue.Queue(maxsize=2)
    for i in range(5):
        pub.publish({"v": 1, "i": i})  # must not raise or block
    assert pub.q.qsize() == 2


def test_end_to_end_reading(monkeypatch):
    """Fake ADC -> payload math matches amps = volts/5 * range."""
    monkeypatch.setenv("RANGE_AMPS", "200")
    cfg = sampler.SamplerConfig()
    reader = make_reader(monkeypatch)
    p = sampler.build_payload(1.0,
                              reader.read_leg_amps(cfg.leg1_ch, cfg.range_amps),
                              reader.read_leg_amps(cfg.leg2_ch, cfg.range_amps),
                              cfg.range_amps, "test")
    assert p["leg1_A"] == pytest.approx(100.0, abs=0.05)  # 2.5V of 5V * 200A
    assert p["leg2_A"] == pytest.approx(40.0, abs=0.05)   # 1.0V of 5V * 200A
