# DRT Documentation

This directory contains the core written documentation for DRT.

> Status
>
> These docs describe the current DRT prototype and its intended model. They are not a claim that every aspirational guarantee in the design docs is already production-hardened.

## Index

| Document | Purpose |
| --- | --- |
| [USER_GUIDE.md](USER_GUIDE.md) | Practical usage guide for recording, replaying, and debugging with DRT |
| [API_REFERENCE.md](API_REFERENCE.md) | Public API reference and examples |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, major components, and design tradeoffs |
| [SPECIFICATION.md](SPECIFICATION.md) | Formal model and target behavior for the runtime |
| [CASE_STUDY_LOST_UPDATE.md](CASE_STUDY_LOST_UPDATE.md) | A concrete lost-update debugging story from record through replay and fix |

## Suggested Reading Order

### New readers

1. [USER_GUIDE.md](USER_GUIDE.md)
2. [API_REFERENCE.md](API_REFERENCE.md)
3. [CASE_STUDY_LOST_UPDATE.md](CASE_STUDY_LOST_UPDATE.md)
4. [ARCHITECTURE.md](ARCHITECTURE.md)

### Contributors

1. [ARCHITECTURE.md](ARCHITECTURE.md)
2. [SPECIFICATION.md](SPECIFICATION.md)
3. Source code in `drt/`

## Notes

- The root [README.md](../README.md) is the best entry point for the project story and quick start.
- The docs here should stay aligned with the actual code. If behavior changes, update the docs with it.
