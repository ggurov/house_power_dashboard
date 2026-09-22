# Vendored Waveshare ADS1256 driver, copied byte-identical from the working
# sampler on `adcpi1` (`/root/ADS1256_graphite/`, Sep 2023) so SPI behavior is
# proven unchanged. DO NOT "improve" these files; fix quirks in `sampler.py`.
#
# - `ADS1256.py` / `config.py`: Waveshare High-Precision AD/DA board sample code.
#   `config.py` carries the Waveshare MIT-style license header; the same terms
#   apply to `ADS1256.py` (same sample package).
# - `orig_main_reference.py`: the previous graphite-pushing loop, kept as a
#   behavioral reference only. Not used by the new sampler.
#
# Quirk notes for `sampler.py`:
# - Single-ended 8-channel mode (`ScanMode = 0`); CTs are 0-5 V DC self-powered,
#   so only the positive half of the ADC range is meaningful.
# - `ADS1256_Read_ADC_Data()` mangles negative codes; we clamp at zero and only
#   trust `0..0x7FFFFF`, where `volts = raw * 5.0 / 0x7FFFFF`.
