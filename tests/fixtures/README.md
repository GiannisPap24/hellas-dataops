# Synthetic fixtures

This directory contains small, explicitly synthetic provider response examples.
Milestone 2A uses deterministic Python fixtures in `tests/unit/conftest.py` for
canonical domain records. They generate exactly 168 hourly observations for
Athens during 2025-01-01 through 2025-01-07 UTC, with deliberately invented
measurements and dataset identity `synthetic/hourly-v1`. No external data has
been fetched.

Domain tests derive nulls, invalid values, gaps, duplicates, identity mismatches,
and reversed ordering from these fixtures. Leap-day and large-interval cases
use explicit datetimes. No random generator, wall clock, timezone database,
credential, or network connection is needed. Existing socket/DNS test guards
remain enabled.

Milestone 2B adds three hand-specified, deterministically generated JSON examples
under `open_meteo/`. Every file contains a `_fixture` label identifying it as
synthetic, not captured API data. The extra label also exercises tolerance of
unknown response fields.

| File | UTC coverage | Purpose |
| --- | --- | --- |
| `seven_days.json` | 2025-01-01 through 2025-01-07 inclusive; 168 hours | Valid archive-shaped response with explicit units and differing grid coordinates |
| `null_measurements.json` | 2025-01-01 00:00 through 02:00; 3 hours | All-null, partially-null, and zero measurements |
| `leap_day.json` | 2024-02-28 23:00 through 2024-03-01 00:00; 26 hours | All 24 leap-day hours and adjacent day boundaries |

Timestamps are integer Unix seconds: the seven-day fixture starts at 1735689600,
then increases by 3600 per row. Grid coordinates (37.95, 23.75) and elevation
(70 m) are invented and differ from the configured Athens point. Measurements
follow simple repeating patterns and make no assertion about actual weather.

`tests/unit/test_open_meteo.py` loads a fresh fixture per test and derives malformed
structures, units, lengths, values, metadata, duplicate/gapped/reversed timelines,
and optional/extra fields in memory. No invalid JSON syntax is needed: the parser
accepts already decoded objects. JSON decoding and duplicate JSON object-key
detection remain future transport concerns. Synthetic fixtures must not be
presented as Open-Meteo observations or evidence of live compatibility.
