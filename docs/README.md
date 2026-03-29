# DRT Documentation

## Deterministic Record-and-Replay Runtime for Python

Welcome to the DRT documentation. This directory contains comprehensive documentation for understanding, using, and extending the DRT runtime.

---

## Documentation Index

### For Users

| Document | Description |
|----------|-------------|
| [User Guide](USER_GUIDE.md) | Step-by-step guide to using DRT |
| [API Reference](API_REFERENCE.md) | Complete API documentation |
| [FAQ](USER_GUIDE.md#11-faq) | Frequently asked questions |

### For Developers

| Document | Description |
|----------|-------------|
| [Architecture](ARCHITECTURE.md) | System design and components |
| [Specification](SPECIFICATION.md) | Formal technical specification |
| [Failure Analysis](../FAILURE_ANALYSIS.md) | Known limitations and failure modes |

### For Systems Engineers

| Document | Description |
|----------|-------------|
| [Advanced Features](ADVANCED_FEATURES.md) | Tracing, chaos, operations |

### Quick Links

- **Getting Started:** [User Guide → Getting Started](USER_GUIDE.md#3-getting-started)
- **Converting Code:** [User Guide → Converting Your Code](USER_GUIDE.md#5-converting-your-code)
- **API Examples:** [API Reference → Examples](API_REFERENCE.md#examples)
- **Debugging:** [User Guide → Debugging with DRT](USER_GUIDE.md#8-debugging-with-drt)
- **Tracing:** [Advanced Features → Distributed Tracing](ADVANCED_FEATURES.md#1-distributed-tracing)
- **Chaos Testing:** [Advanced Features → Chaos Engineering](ADVANCED_FEATURES.md#2-chaos-engineering)
- **Operations:** [Advanced Features → Operational Scripts](ADVANCED_FEATURES.md#3-operational-scripts)

---

## Documentation Overview

### User Guide

The **User Guide** is the best starting point for new users. It covers:

- Installation and setup
- Basic concepts (modes, nondeterminism, yield points)
- Converting existing code to use DRT
- Recording and replaying executions
- Debugging techniques
- Best practices and troubleshooting

### API Reference

The **API Reference** provides complete documentation for all public APIs:

- `DRTRuntime` — Main runtime controller
- `DRTThread` — Managed thread class
- `DRTMutex`, `DRTCondition` — Synchronization primitives
- `drt_time()`, `drt_random()` — Nondeterminism interceptors
- Exception classes
- Low-level log access

### Architecture Document

The **Architecture Document** explains how DRT works internally:

- System components and their responsibilities
- Threading model and permission-based blocking
- Scheduling algorithm
- Event log format
- Data flow in record and replay modes
- Design decisions and rationales

### Technical Specification

The **Specification** provides a formal definition of DRT behavior:

- State machine definitions
- Invariants and theorems
- Binary log format specification
- Conformance requirements

### Failure Analysis

The **Failure Analysis** documents known limitations:

- What DRT can and cannot do
- Sources of nondeterminism not captured
- Performance characteristics
- Failure modes and their causes

---

## Reading Order

### New Users

1. [User Guide](USER_GUIDE.md) — Learn the basics
2. [API Reference](API_REFERENCE.md) — Look up specific functions
3. [Failure Analysis](../FAILURE_ANALYSIS.md) — Understand limitations

### Contributors / Advanced Users

1. [Architecture](ARCHITECTURE.md) — Understand the design
2. [Specification](SPECIFICATION.md) — Formal definitions
3. Source code in `drt/` — Implementation details

---

## Document Conventions

### Code Examples

All code examples are complete and runnable:

```python
from drt import DRTRuntime, DRTThread

def my_program():
    print("Hello!")

runtime = DRTRuntime(mode='record', log_path='test.log')
runtime.run(my_program)
```

### Admonitions

> **Note:** Important information

> **Warning:** Potential pitfalls

> **Tip:** Helpful suggestions

### Terminology

Technical terms are defined in the [Glossary](ARCHITECTURE.md#appendix-a-glossary).

---

## Getting Help

1. Check the [FAQ](USER_GUIDE.md#11-faq)
2. Review [Troubleshooting](USER_GUIDE.md#10-troubleshooting)
3. Read error messages carefully — DRT provides detailed divergence information
4. Examine the log with `dump_log()` to understand execution

---

## Version

This documentation covers DRT version **1.0.0**.
