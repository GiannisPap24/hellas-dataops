# Historical weather data contract

Contract version: **0.4**. Canonical objects and domain validation were implemented
in Milestone 2A. Milestone 2B implements pure Open-Meteo response normalization.
Milestone 2C adds offline request planning and request intent provenance.
Milestone 2D adds bounded HTTP retrieval verified entirely with offline transports.
Ingestion, response provenance persistence, and storage remain planned.
All weather test inputs are explicitly synthetic.

## Implemented HTTP retrieval

`fetch_open_meteo(request, *, transport=None, timeout_seconds=10.0,
max_response_bytes=2097152)` in `sources/open_meteo_client.py` accepts one
validated `OpenMeteoRequest`. It returns a caller-owned `dict[str, object]` from
one JSON response. It does not normalize observations, trim partial days, join
chunks, persist provenance, read configuration, log bodies, or load a clock.
All existing domain, planner, and normalizer semantics are unchanged.

The standard-library adapter makes one GET to
`https://archive-api.open-meteo.com/v1/archive` with the specification's exact
query parameters. TLS certificate and hostname verification stay enabled.
Redirects, proxies from the environment, automatic retries, credentials, cookies,
and decompression are not supported. The adapter requests `Accept-Encoding:
identity` and owns both response and connection cleanup, including failure paths.
There is no service call on import or planning. No live call was made for 2D.

`ArchiveTransport` is a callable returning a context manager of `ArchiveResponse`
(`status`, `getheaders()`, `read1(size)`). A custom transport must implement
bounded byte reads, EOF as `b''`, timeout enforcement, and cleanup; it must not
silently retry, redirect, decompress, or buffer an unbounded body. Injection is
a trusted programming boundary, not a sandbox for arbitrary transport code.

Limits and accepted envelope:

- Timeout must be a finite number greater than zero and at most 300 seconds;
  booleans are invalid. The default 10 seconds is a per-socket-operation timeout,
  not an end-to-end deadline. System DNS lookup can exceed that timeout.
- The body cap must be an integer from 1 byte to 16 MiB (default 2 MiB).
  Content-Length above the cap fails before body reads. Independently of headers,
  reads request at most 64 KiB and consume at most cap + 1 bytes. Oversize data
  fails before JSON decoding. Decoded object memory can exceed this byte cap.
- Only HTTP 200 and media type `application/json` (case-insensitive, optional
  parameters) are accepted. Body decoding is always strict UTF-8; a charset
  parameter does not select another decoder. BOM-prefixed JSON is rejected.
- Content-Encoding must be absent or `identity`. Content-Length, when present,
  must be ASCII decimal and match the bytes delivered for the response.
  Chunked and connection-close framing are supported. Duplicate framing/content
  headers, unsupported Transfer-Encoding, and Transfer-Encoding together with
  Content-Length are rejected. The stdlib handles HTTP framing/header parsing;
  the body cap does not bound all header/trailer/framing overhead.
- JSON must contain one top-level object, no trailing content, no duplicate keys
  at any depth, no NaN/infinities (including exponent overflow), and at most 64
  nested containers. Python's integer decoding limit also applies. An object
  with `error: true` is treated as a provider failure; its reason is not echoed.
- This validates a JSON envelope, not weather correctness or model delivery.
  A valid object with missing weather fields reaches the separate normalizer.
  Missing bytes with a valid shorter body cannot be detected without an explicit
  framing length; weather coverage remains the normalizer's responsibility.

All retrieval errors derive from `OpenMeteoClientError` and carry `code`:

| Exception | Codes / fields | Meaning |
| --- | --- | --- |
| `OpenMeteoTimeoutError` (also a transport error) | `timeout` | Socket operation timeout |
| `OpenMeteoTransportError` | `transport` | OS, TLS, connection, or HTTP framing failure |
| `OpenMeteoStatusError` | `http_status`, `status`, `retry_after_seconds`, `retry_after_at` | Any non-200 response; body is not read |
| `OpenMeteoResponseError` | `content_type`, `content_encoding`, `invalid_headers` | Unsupported or ambiguous envelope |
| `OpenMeteoResponseError` | `response_too_large`, `content_length_mismatch`, `invalid_stream` | Oversized/truncated response or broken injected stream contract |
| `OpenMeteoResponseError` | `invalid_json`, `invalid_json_shape`, `provider_error` | Invalid JSON or provider error envelope |

Invalid API argument types/limits raise `TypeError`/`ValueError` before transport
invocation. Network exception chains are suppressed in normal traceback rendering;
callers must still avoid introspecting/logging raw exception context or locals.
No raw response body, provider reason, URL, or arbitrary header is added to the
public error message. Non-200 responses are never followed, retried, or decoded.

`Retry-After` is advisory metadata only: one header of at most 128 characters,
with at most 10 ASCII digits of delay seconds or a parseable timezone-aware HTTP
date converted to UTC. Missing, duplicate, malformed, or oversized hints become
`None`. Dates are not compared with the current time. A future retry policy must
decide transient status eligibility, cap attempts/delays, and honour these hints;
2D performs no waiting or retries.

Evidence is synthetic and offline. Live endpoint compatibility, DNS/TLS behavior,
service terms eligibility, upstream availability, throughput, and total-request
deadlines are not established by these tests.

## Implemented domain API

Import these objects from `hellas_dataops.domain`. Constructors are keyword-only;
models are immutable, slotted standard-library dataclasses with value equality.

| Object/function | Implemented responsibility |
| --- | --- |
| `DatasetIdentity(provider_id, dataset_id)` | Explicit provider-qualified series identity |
| `HourlyInterval(start, end)` | Nonempty, hourly UTC interval and `expected_hours` count |
| `HourlyObservation(city_id, dataset, timestamp_utc, ...)` | Validated hourly measurements; all four measurement fields default to `None` |
| `validate_batch(observations, *, city_id, dataset, interval)` | Exact requested hourly coverage, identity consistency, and duplicate rejection |
| `DomainValidationError` | `ValueError` carrying `code`, `field`, and a readable corrective message |

The five city IDs below are an explicit allowlist: unknown, uppercase, or padded
city names are rejected. Dataset and provider IDs are strings of 1-64 characters
matching `[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*`: lowercase ASCII slugs, without trimming
or case conversion. Their presence in an external catalogue is **not** checked.
Callers must select a meaningful, versioned dataset identity. The implemented
planner accepts only `provider_id="open-meteo", dataset_id="era5_hourly-v1"`;
synthetic domain fixtures instead use `synthetic/hourly-v1`. A source
test also supplies the proposed identity to check that it is preserved; that
test still uses invented data and does not establish ERA5 provenance.

`validate_batch` accepts a finite sequence such as a list or tuple, not a lazy
iterator/generator. It returns an immutable tuple sorted chronologically, leaves
the caller's sequence unchanged, and never silently deduplicates. Revalidating
the same valid input, or the returned tuple, yields equal values. It produces no
I/O, clock-dependent values, or persistence effects. This is deterministic
validation, not yet idempotent database ingestion.

All records must match the explicitly requested city and full `DatasetIdentity`,
including provider. Duplicate hours fail even if their measurement values are
identical. Exact timestamp coverage is required; equal row counts alone do not
prove coverage. An absent observation fails coverage, whereas a present all-null
observation covers its hour but provides no usable measurements. No per-variable
completeness summary is calculated yet; nulls are retained in the returned rows.

The interval is `[start, end)`, both hour-aligned UTC datetimes, with `start < end`.
Domain intervals may begin at any UTC hour; the planner explicitly distinguishes
whole-day retrieval chunks from retained hourly subsets. Coverage checks do not materialize the expected
timeline, even for a very long empty interval.

### Timestamp and numeric validation

Only Python `datetime` values are accepted by the domain; the source adapter
converts integer epoch seconds before domain validation. A defined zero UTC
offset is required at the supplied instant;
equivalent zero-offset timezone objects are canonicalized to `datetime.UTC` with
`fold=0`. Naive datetimes and nonzero offsets are rejected rather than silently
converted. Minutes, seconds, and microseconds must all be zero.

Measurements accept built-in `int`, `float`, or `None`. Integers normalize to
floats; booleans, numeric strings, Decimal/Fraction values, complex numbers, and
other numeric object types are rejected. NaN, infinity, and integers too large
for a finite float are rejected. No unit conversion or missing-value imputation
occurs in the domain layer. Explicit units are fixed by canonical field names.
An adapter must establish that a source value is genuinely missing before
passing `None`; an absent required provider array is not a null measurement.

### Error behaviour

Constructors fail immediately on an invalid field. Batches check requested
identity/interval, then row types, identities, and duplicates in input order,
then coverage. They return no partial result. Error `field` paths locate the
problem, for example `observations[3].dataset`. Coverage errors include expected
count, missing/unexpected counts, and the earliest example of each problem;
they intentionally do not allocate or list every missing timestamp.

| Error code | Meaning |
| --- | --- |
| `invalid_city_id` | City is outside the approved allowlist or not a string |
| `invalid_identifier` | Provider/dataset ID has invalid syntax or length |
| `invalid_dataset` | Expected a `DatasetIdentity` object |
| `invalid_timestamp` | Expected a Python datetime |
| `naive_timestamp` | UTC offset is undefined |
| `non_utc_timestamp` | UTC offset is nonzero |
| `unaligned_timestamp` | Timestamp is not on an hourly boundary |
| `invalid_numeric_type` | Unsupported measurement type |
| `non_finite_measurement` | NaN, infinity, or unrepresentable integer |
| `out_of_range` | A physical measurement bound was violated |
| `invalid_interval` | Invalid interval object, empty interval, or reversed endpoints |
| `invalid_batch` / `invalid_observation` | Wrong container or row object type |
| `city_mismatch` / `dataset_mismatch` | Row differs from the requested identity |
| `duplicate_observation` | More than one row for the same requested hour |
| `coverage_mismatch` | Missing hours, unexpected hours, or both |

## Cities and periods

These are project-selected representative WGS84 latitude/longitude points,
not claims about official city centroids or weather-station locations.

| Stable city ID | Display name | Latitude | Longitude |
| --- | --- | ---: | ---: |
| `athens` | Athens | 37.9838 | 23.7275 |
| `thessaloniki` | Thessaloniki | 40.6401 | 22.9444 |
| `patras` | Patras | 38.2466 | 21.7346 |
| `heraklion` | Heraklion | 35.3387 | 25.1442 |
| `larissa` | Larissa | 39.6390 | 22.4191 |

Version coordinate changes: they change the sampled location and must not
silently overwrite a previous location's series. Preserve both requested
coordinates and returned grid-cell coordinates/elevation.
The domain model deliberately contains neither coordinate pair. The implemented
source metadata preserves returned grid coordinates and optional elevation.
The planner captures versioned requested coordinates separately; do not treat
a returned grid point as the city's requested point.

`sources.cities.CITY_REGISTRY` contains these exact five points, all under
coordinate version `gr-cities-v1`. Entries are frozen `CityCoordinates` with
`city_id`, `latitude`, `longitude`, and `coordinate_version`; the mapping is
read-only. Coordinates accept finite built-in integers/floats (normalized to
floats), with latitude in [-90, 90] and longitude in [-180, 180]. Booleans,
numeric strings, NaN/infinity, and unrepresentable integers are invalid. Version
slugs use the same 1-64 character lowercase ASCII syntax as dataset identifiers.

Construction validates values; the planner additionally requires an exact
registered city/coordinate/version combination. A different valid point or
unknown version is rejected. Coordinate changes require review, a new version,
and a decision about dataset identity or storage keys before persistence; the
canonical observation model has not been expanded with coordinate fields.

- Development: Athens, `[2025-01-01T00:00:00Z, 2025-01-08T00:00:00Z)`;
  168 expected timestamps.
- Full MVP: all five cities, `[2025-01-01T00:00:00Z, 2026-01-01T00:00:00Z)`;
  8,760 timestamps per city and 43,800 city-hour records overall.

Counts measure expected timestamp coverage, not a guarantee that every variable
is non-null. Record coverage and per-variable completeness separately.

## Provider and request semantics

- Provider: Open-Meteo Historical Weather API.
- Intended endpoint: `https://archive-api.open-meteo.com/v1/archive`.
- Explicit model parameter: `models=era5`; do not use implicit `best_match`.
- Request UTC using `timezone=GMT`, with `timeformat=unixtime` for unambiguous
  epoch seconds. Normalize to timezone-aware UTC datetimes.
- Request hourly data, Celsius, millimetres, and metres per second explicitly.
- Preserve grid-selection and elevation options in request provenance, including
  whether provider defaults were used.

The implemented offline planner produces this explicit request contract:

| Parameter | Planned value |
| --- | --- |
| `models` | `era5` |
| `timezone` | `GMT` |
| `timeformat` | `unixtime` |
| `temperature_unit` | `celsius` |
| `wind_speed_unit` | `ms` |
| `precipitation_unit` | `mm` |
| `cell_selection` | `land` |
| `elevation` | Literal string `nan`: native grid elevation without elevation downscaling |
| `format` | `json` |
| `hourly` | `temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m` |
| `latitude`, `longitude` | Versioned requested city point, not returned grid coordinates |
| `start_date`, `end_date` | Inclusive provider dates derived from each complete-day retrieval chunk |

Land-cell selection is made explicit for the five land locations. No requested
city elevation has been supplied, so native grid elevation is explicitly selected
instead of inferring an altitude or relying on automatic elevation lookup. The
string `nan` here is a provider option, not a numeric NaN measurement or coordinate.
These choices define the initial ERA5 series; changing model, units, variables,
grid/elevation policy, or normalization semantics requires version review.

The archive provides reanalysis estimates, not direct observations from a
specific city station. ERA5 has an approximately five-day availability delay in
the provider documentation. Latest-day ingestion is outside the initial scope;
future availability checks must handle delayed or incomplete upstream data.

The provider's date-based request uses inclusive start/end dates. Internally,
use half-open intervals: start inclusive, end exclusive. For the initial seven
days, plan `start_date=2025-01-01` and `end_date=2025-01-07`. Partial-day domain
intervals are supported explicitly as described below. Adjacent chunks neither
overlap nor leave gaps. Each retrieval chunk contains at most 14 UTC calendar days.

## Implemented request planning

Import `ERA5_DATASET`, `OpenMeteoRequest`, and `plan_open_meteo_requests` from
`hellas_dataops.sources.open_meteo_requests`, and `CITY_REGISTRY` and
`CityCoordinates` from `hellas_dataops.sources.cities`.

```python
def plan_open_meteo_requests(
    city: CityCoordinates,
    *,
    dataset: DatasetIdentity,
    interval: HourlyInterval,
) -> tuple[OpenMeteoRequest, ...]: ...
```

The dataset must equal `ERA5_DATASET` (`open-meteo/era5_hourly-v1`); matching just
the dataset name or provider is insufficient. ERA5-Land, synthetic datasets,
`best_match`, and unrecognized versions are rejected. The domain and normalizer
remain more general and retain their existing public interfaces.

Every frozen, keyword-only `OpenMeteoRequest` contains the registry city, dataset,
original `requested_interval`, chunk `retrieval_interval`, contract version
`open-meteo-request-v1`, and fixed archive endpoint. Direct construction also
validates context and chunk bounds. Derived properties expose:

- `retain_interval`: the intersection of the original request and retrieval chunk.
- `start_date` / `end_date`: inclusive provider dates for that chunk.
- `parameters`: immutable, fixed-order tuple of string key/value pairs.
- `query_string`: standard percent-encoded parameters; commas encode as `%2C`.
- `url`: endpoint plus query, without performing any HTTP operation.

`to_json()` returns a canonical compact JSON string with sorted object keys,
explicit UTC ISO timestamps (`+00:00`), city/version/coordinates, dataset/provider,
all three intervals, endpoint, contract version, and parameters. The parameters
include model, units, variables, provider date range, and sampling options.
Coordinates serialize using their finite float string representation without
rounding (e.g. Larissa's 39.6390 becomes the equivalent 39.639). No mutable
parameter dictionaries are stored; caller conversions cannot mutate a plan.
Identical inputs yield equal plans, URLs, and provenance JSON, without time,
randomness, configuration reads, or external lookups.

### Hourly intent, retrieval days, and retained hours

First floor the original start to UTC midnight. Preserve an end already at UTC
midnight; otherwise round it up to the next UTC midnight. Split this complete-day
envelope into successive chunks of at most 14 days. For each chunk, the inclusive
provider `end_date` is the date one day before its exclusive retrieval end.
Retained subsets partition the original hourly interval exactly. A 14 x 24-hour
partial-day request may touch 15 calendar days and therefore require two chunks.

All examples use UTC; provider dates in each row are inclusive:

| Original hourly interval `[start, end)` | Provider date ranges | Retained hours per chunk |
| --- | --- | --- |
| Jan 1 00:00 to Jan 15 00:00, 2025 | Jan 1-14 | 336 |
| Jan 1 00:00 to Jan 16 00:00, 2025 | Jan 1-14; Jan 15 | 336, 24 |
| Jan 1 05:00 to Jan 1 06:00, 2025 | Jan 1 | 1 (24 hours retrieved) |
| Jan 1 05:00 to Jan 15 05:00, 2025 | Jan 1-14; Jan 15 | 331, 5 |
| Feb 28 23:00 to Mar 1 01:00, 2024 | Feb 28-Mar 1 | 26 (72 hours retrieved) |
| Dec 25 00:00, 2024 to Jan 9 00:00, 2025 | Dec 25-Jan 7; Jan 8 | 336, 24 |

Each specification retains the unchanged original interval, including when it
spans several chunks. Both retrieval chunks and retained subsets are ordered and
nonoverlapping within a plan. Independently planned partial intervals can retrieve
the same day; cross-plan deduplication is a future ingestion concern. UTC days
remain 24 hours across Greek daylight-saving transitions; local Greek-day
aggregation is not implemented. Date rounding beyond datetime year 9999 fails
explicitly, while provider data availability dates remain unverified offline.

### Integration with strict normalization (future work)

For each complete provider response, future ingestion must call the existing
normalizer with `interval=request.retrieval_interval`. After full response
validation, retain only records inside `request.retain_interval` and validate
coverage of that subset. Validate the assembled retained rows against the
original interval as well. Passing an unfiltered full-day response with a partial
retained interval correctly raises a coverage error. No filtering or ingestion
integration is implemented here; a synthetic test verifies the normalizer's
existing strict behaviour against these two intervals.

### Planning errors and limits

Errors use the existing `DomainValidationError` interface (`code`, `field`,
message). City identifier and interval construction retain domain validation.
New codes are `invalid_coordinate`, `invalid_coordinate_version`,
`invalid_city_configuration`, `unsupported_city_configuration`,
`incompatible_dataset`, `invalid_retrieval_interval`, and
`unrepresentable_retrieval_interval`; invalid dataset/interval object types use
the existing `invalid_dataset` / `invalid_interval` codes. No partial plan is
returned on failure, no implicit dataset is selected, and no caller values are
modified.

Planning records intended ERA5 selection; neither that intent nor a response
alone proves upstream model delivery. These are reproducible request
specifications, not a guarantee of reproducible upstream weather values.
Availability, response checksums, retrieval timestamps, authorization/service
eligibility, and retries belong to later milestones. HTTP body limits are now
implemented in the separate client. No live request
or fresh official-documentation retrieval was performed in this milestone;
provider options and response compatibility require verification before live use.

## Canonical hourly values

| Provider variable | Canonical field | Unit | Meaning |
| --- | --- | --- | --- |
| `temperature_2m` | `temperature_c` | degrees Celsius | Air temperature at 2 m at the timestamp |
| `relative_humidity_2m` | `relative_humidity_pct` | percent | Relative humidity at 2 m at the timestamp |
| `precipitation` | `precipitation_mm` | millimetres | Accumulation over the preceding hour |
| `wind_speed_10m` | `wind_speed_ms` | metres/second | Wind speed at 10 m at the timestamp |

The adapter must verify returned units, rather than assume the request was
honoured. Values are finite numbers or null; reject strings, booleans, NaN, and
infinities. Humidity must be between 0 and 100 inclusive. Precipitation and wind
speed must be nonnegative. Temperature below absolute zero is invalid; unusual
but possible temperatures must not be silently discarded by arbitrary bounds.

The shared row timestamp preserves the provider's label. A precipitation value
labelled `01:00Z` describes the preceding hour, unlike an instantaneous
temperature value. Do not relabel or shift it silently. Daily aggregation needs
its own interval contract and tests and is deferred.

## Implemented Open-Meteo normalization

Import from `hellas_dataops.sources.open_meteo`:

```python
def normalize_open_meteo(
    payload: Mapping[str, object],
    *,
    city_id: str,
    dataset: DatasetIdentity,
    interval: HourlyInterval,
) -> NormalizedWeather: ...
```

The input is an already decoded **single-location** JSON object. Top-level
`hourly` and `hourly_units` must be mappings. All five arrays below must exist
as JSON lists, have equal lengths, and match the explicitly requested interval.
Absent arrays, null arrays, tuples, and strings are invalid; an array element
may be null only for a measurement. Empty arrays fail nonempty interval coverage.

| Hourly array | Exact `hourly_units` value | Result |
| --- | --- | --- |
| `time` | `unixtime` | `HourlyObservation.timestamp_utc` |
| `temperature_2m` | `°C` | `HourlyObservation.temperature_c` |
| `relative_humidity_2m` | `%` | `HourlyObservation.relative_humidity_pct` |
| `precipitation` | `mm` | `HourlyObservation.precipitation_mm` |
| `wind_speed_10m` | `m/s` | `HourlyObservation.wind_speed_ms` |

Units are case-sensitive and verified, never inferred or converted. Epoch
timestamps must be built-in integers representing seconds since
1970-01-01T00:00:00Z. Booleans, floating-point epochs (even integral floats),
strings, nulls, and values outside Python datetime years 1-9999 are rejected.
Milliseconds are not detected and rescaled. UTC epoch arithmetic avoids
platform-dependent pre-1970 conversion behaviour. The existing domain checks
hour alignment and exact `[start, end)` coverage; no local timezone adjustment
is applied to timestamps. The last Python datetime hour cannot be requested
with an exclusive endpoint beyond year 9999.

Measurements pass through canonical construction-time validation. Explicit JSON
nulls become `None`, including all-null rows; values are never filled with zero.
Unsorted rows are sorted together with their measurements. Duplicates fail even
when identical. Missing rows and rows containing null measurements remain
distinct. The precipitation label is preserved without shifting the preceding
hour's accumulation to another timestamp.

### Returned result and source metadata

Both `NormalizedWeather` and `OpenMeteoMetadata` are frozen, slotted dataclasses.
`NormalizedWeather.observations` is a sorted tuple of `HourlyObservation` objects;
`NormalizedWeather.metadata` contains the validated source fields below. These
result containers describe normalizer outputs; direct construction of a source
metadata dataclass does not itself run the parser's validation.

| Response field | Metadata field | Validation |
| --- | --- | --- |
| `latitude` | `grid_latitude` | Required finite built-in number in [-90, 90] |
| `longitude` | `grid_longitude` | Required finite built-in number in [-180, 180] |
| `elevation` | `grid_elevation_m` | Optional finite number; absent/null becomes `None`; negative elevations allowed |
| `timezone` | `timezone` | Required exact `GMT`, `UTC`, `Etc/GMT`, or `Etc/UTC` |
| `utc_offset_seconds` | `utc_offset_seconds` | Required integer 0; booleans and floats rejected |
| `timezone_abbreviation` | `timezone_abbreviation` | Optional `GMT` or `UTC`; absent/null becomes `None` |

The future request always asks for GMT. The other listed UTC names are deliberate
compatible aliases, not evidence that they have been observed in actual responses.
Regional names such as `Europe/London` are rejected even when their seasonal
offset might be zero. Coordinates and elevation normalize to finite floats;
numeric strings and booleans are invalid. No distance check against the city
point is performed. Grid-cell coordinates can legitimately differ from the
requested coordinates and must not overwrite them.

Caller-supplied `city_id` and `dataset` are attached to every observation.
Dataset identity is validated syntactically by the domain, not inferred from
response fields. **The response does not prove that ERA5 was requested or
delivered.** The planner now binds explicit model, variables, units, coordinates,
and options to an approved dataset identity. Normalization alone still does not
perform that binding. These normalization results do not
yet contain full request/retrieval provenance, checksums, or a retained response.
Verified units are represented by the fixed canonical fields; no mutable copy
of `hourly_units` is attached to the result.

Unknown fields, including extra hourly variables, `generationtime_ms`, and any
untrusted model label, are ignored for forward compatibility. Their values and
array lengths are not validated, retained, or treated as provenance. Only the
requested fields defined here participate in normalization.

### Source errors and compatibility limits

Normalization fails atomically: no partial result, skipped row, silent coercion,
or input mutation. The first error in deterministic validation order is raised;
the parser does not collect every error. Context and source structure/metadata
are checked before rows; hourly units and array types precede lengths, then
per-row validation precedes batch validation.

`OpenMeteoNormalizationError` subclasses `DomainValidationError`, retaining the
common `code`, `field`, and readable message interface:

| Source error code | Meaning |
| --- | --- |
| `invalid_structure` | Top-level or nested object is absent or not a mapping |
| `invalid_metadata` | Missing/invalid coordinate or UTC metadata |
| `invalid_unit` | Missing or incorrect declared unit, including time units |
| `invalid_array` | Required hourly array is absent or not a JSON list |
| `array_length_mismatch` | Required array length differs from `hourly.time` |
| `invalid_epoch` | Timestamp is not integer seconds or exceeds datetime limits |

Domain errors keep their existing codes. Row-level timestamp/value errors include
source paths such as `hourly.temperature_2m[5]` and retain the original exception
as their cause. Duplicate errors retain `observations[index].timestamp_utc`, where
the index is the original input position; coverage errors retain `observations`
and report missing/unexpected counts with the earliest examples. Catching
`DomainValidationError` handles normalization and domain failures; HTTP client
failures use the separate `OpenMeteoClientError` hierarchy.

The synthetic schema examples follow the previously recorded official Historical
Weather API reference. No live request or fresh documentation retrieval was
performed in this offline milestone. Tests verify this chosen contract, not
upstream availability, actual ERA5 selection, or compatibility with every possible
provider response. The HTTP client rejects multi-location lists and duplicate
JSON keys, detects error envelopes, and bounds body bytes. Row counts and weather
schema validation remain the normalizer's responsibility; automatic adaptation
to schema drift is not implemented. The normalizer accepts no implicit dataset,
timezone, unit, or missing-array defaults.

## Time and completeness

- Canonical timestamp: timezone-aware UTC, rendered as ISO 8601 with `Z` at
  public boundaries; use PostgreSQL `timestamptz` when storage is implemented.
- Preserve hourly precision and verify uniqueness. The implemented batch
  validator sorts unsorted input without mutating it.
- The domain rejects duplicate timestamps, non-hourly timestamps, missing
  expected hours, and out-of-range timestamps. The implemented normalizer also
  rejects mismatched array lengths and absent required arrays; it never silently
  truncates arrays with `zip`.
- An explicit null measurement is valid missing data; retain it and count it.
  An all-null record remains incomplete and is not evidence of usable weather.
- Do not fill gaps, interpolate, or substitute zeros in the MVP.
- Future persistence must ensure a structurally invalid chunk writes no
  normalized weather records. A valid
  chunk containing nulls can be stored, but must report incomplete measurements
  rather than claim full completeness.

UTC requests avoid Greek daylight-saving ambiguity. Do not interpret UTC dates
as local Greek calendar days. Local-day aggregation is deferred.

## Identity, idempotency, and source revisions

The intended natural identity is `(city_id, dataset_id, timestamp_utc)`.
Here dataset identity is the pair `(provider_id, dataset_id)` represented by
`DatasetIdentity`; provider is part of equality, not an implied global default.
The versioned dataset configuration identifies provider, model, variables,
units, and relevant sampling/normalization semantics. City coordinate versions
must also remain distinguishable. A run ID or fetch time is not part of weather
record identity.

Reprocessing the same normalized values must leave weather values and row counts
unchanged. Audit history may gain a new attempt. Revised upstream values may
replace prior values only through an explicit refresh/upsert policy with
provenance. Metadata such as retrieval time must not by itself turn unchanged
weather into a changed measurement. Overlapping runs require concurrency control.

Selecting ERA5 does not freeze upstream data. Deterministic replay requires the
retained response, request configuration, and normalization version, not merely
refetching the same URL or possessing a checksum.

## Provenance

The planner retains immutable request intent and canonical JSON; the normalizer
retains canonical identity and returned location/timezone metadata separately.
Joining these with response/retrieval provenance remains a future requirement. Preserve, without
credentials:

- Provider, endpoint, explicit model, and dataset/contract version.
- City identifier and versioned requested coordinates.
- Returned grid-cell coordinates, elevation, timezone, and units.
- Requested range, variables, units, and model/grid/elevation options.
- Retrieval timestamp in UTC, run/chunk/attempt identifiers, and outcome.
- Response checksum with a defined algorithm and representation, plus a retained
  raw-response reference where replay is supported.
- Normalization/code version when available; until a commit exists, identify the
  implementation honestly rather than inventing a revision.
- Expected, received, incomplete, inserted, updated, and unchanged counts as
  applicable.

Ignore volatile retrieval metadata when comparing normalized weather values.
Raw response retention limits and the exact persistence schema are later design
decisions. Runtime data stays outside Git; small synthetic fixtures may be
versioned under `tests/fixtures/`.

## Usage terms and data licence

**API access:** Open-Meteo's free service has non-commercial usage restrictions,
rate limits, and no free-service uptime guarantee. Its terms include promotional
activities among commercial examples. Eligibility for a publicly promoted live
portfolio demo is unresolved; clarify service eligibility or use an appropriate
commercial service arrangement before that use. Offline development with
synthetic fixtures does not depend on live service access.

**Data:** Open-Meteo describes its weather data as CC BY 4.0. Attribution,
licence references, and identification of modifications are required when
distributing derived data. The data licence and permission to access a service
are separate matters. Recheck current official terms before live use.

For future actual-data distributions, include credit such as:

> Weather data provided by Open-Meteo, using ERA5 reanalysis. Licensed under
> CC BY 4.0. Hellas DataOps normalized timestamps and field names and applied
> validation; document any additional modifications with the dataset.

Include working links to Open-Meteo, the source documentation, and CC BY 4.0.
This attribution example must not imply that synthetic fixtures came from the
provider. The repository's code licence remains a separate, unselected decision.

## Official references

These references informed the planning review on 2026-10-20. No external API
request is part of this milestone's implementation or default tests.

- [Historical Weather API and variable semantics](https://open-meteo.com/en/docs/historical-weather-api)
- [Open-Meteo service terms](https://open-meteo.com/en/terms)
- [Pricing, access limits, and data attribution](https://open-meteo.com/en/pricing)
- [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
