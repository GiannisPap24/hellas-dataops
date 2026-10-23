# Code licensing and third-party attribution

Owner-approved code licence, 2026-10-24: **MIT**.

The root [LICENSE](../LICENSE) contains the official MIT text and approved notice:
**Copyright (c) 2026 GiannisPap24**. The package declares SPDX expression `MIT`
and includes `LICENSE` in both source and wheel distributions. Retain the licence
and copyright notice when copying or redistributing the software.

This decision covers this project's own code and documentation. Third-party
software and provider data retain their own terms. It does not authorize live
API use, change a dependency licence, or imply public deployment/publication.
The repository contains synthetic fixtures, not redistributed real weather data.
The audit considered Apache-2.0 as an alternative; the owner selected MIT.
Authoritative text: [Open Source Initiative MIT](https://opensource.org/license/mit).

## Python dependency notices

Inventory below comes from the actual locked Windows environment's distribution
metadata and licence-file locations, not from assumptions about package names.
The detailed local record is `artifacts/final-audit/dependency-license-metadata.json`.
Dependencies are installed separately; the project wheel does not vendor them.
If redistributing an environment, image or executable, retain the dependencies'
actual copyright/licence/NOTICE files. This table does not replace those texts.

| Direct runtime dependency | Locked version | Declared licence |
| --- | --- | --- |
| SQLAlchemy | 2.1.6 | MIT |
| Alembic | 1.20.0 | MIT |
| pg8000 | 1.31.5 | BSD-3-Clause |
| FastAPI | 0.159.1 | MIT |
| Pydantic | 2.14.1 | MIT |
| pydantic-settings | 2.16.1 | MIT |
| Uvicorn | 0.54.0 | BSD-3-Clause |

Other installed locked distributions (runtime and development combined):

| Declared licence family | Packages |
| --- | --- |
| MIT | annotated-doc, annotated-types, anyio, asn1crypto, h11, iniconfig, Mako, pluggy, pydantic-core, pytest, pytest-cov, Ruff, setuptools, six, truststore, typing-inspection |
| BSD-3-Clause / BSD declaration | click, colorama, httpcore2, httpx2, idna, MarkupSafe, python-dotenv, Starlette |
| BSD-2-Clause | Pygments |
| Apache-2.0 | coverage, opentelemetry-api, tzdata (also review its included timezone-data notices) |
| Apache-2.0 OR BSD-2-Clause | packaging |
| Dual licence | python-dateutil: review the shipped BSD/Apache notices, including individual-file terms |
| MIT No Attribution | scramp |
| PSF-2.0 | typing-extensions |

The conditional `httpx2-jsfetch` lock entry is not installed on Windows; its
platform-specific redistribution has not been verified. httpx2 is intentionally
development-only for the installed Starlette testing stack, not a live ingestion
client. No dependency was added, upgraded or replaced during this audit.
Python, uv, VS Code extensions, Docker Desktop, PostgreSQL and the official image
also carry separate terms. They are not bundled in the project wheel. Do not
represent this metadata inventory as an audit of Docker image operating-system
packages or third-party editor extensions.

## Open-Meteo: three separate matters

1. **API access terms:** the free hosted service is limited to non-commercial
   use and has rate limits. Its examples classify promotional activities as
   commercial. A portfolio must not assume it qualifies for free live access;
   review the intended use with the provider or select an appropriate paid plan
   before enabling it. Limits and call accounting may change.
2. **Weather data licence:** Open-Meteo offers API data under CC BY 4.0, requiring
   attribution, a licence link and identification of modifications. Its licence
   page asks for an Open-Meteo link next to displayed data. Data reuse permission
   does not override service access conditions. ERA5 reanalysis also involves
   Copernicus/C3S source attribution; review the linked source-data terms before
   distributing real data.
3. **Provider server code:** Open-Meteo's server software is AGPL-licensed. This
   repository implements an independent API adapter and does not vendor that
   server. Do not confuse the provider's server licence with this project's code
   licence or with the API data licence.

Current fixtures and demonstrations are invented synthetic measurements, not
Open-Meteo downloads. Label them **synthetic** rather than claiming provider
attribution or model verification. A future real-data presentation should link
to Open-Meteo and CC BY 4.0, identify ERA5/C3S provenance and explain normalization,
UTC retention and other transformations without implying endorsement.

Sources reviewed: [service terms](https://open-meteo.com/en/terms),
[data and source licences](https://open-meteo.com/en/licence),
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/),
[historical API](https://open-meteo.com/en/docs/historical-weather-api).
This audit accessed documentation and advisory metadata only, not weather APIs.
