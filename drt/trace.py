"""
Trace inspection helpers for DRT execution logs.

The helpers in this module are intentionally dependency-free so they can be
used by CLI commands, failure-bundle packagers, or tests without pulling in a
reporting stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Iterable, Sequence

from .events import (
    EventType,
    LogEntry,
    deserialize_cond_payload,
    deserialize_cond_wake_payload,
    deserialize_float_payload,
    deserialize_io_read_payload,
    deserialize_lock_acquire_payload,
    deserialize_log_complete_payload,
    deserialize_mutex_payload,
    deserialize_thread_create_payload,
    deserialize_thread_join_payload,
)
from .log import EventLog


_BUNDLE_LOG_CANDIDATES = (
    "execution.log",
    "drt.log",
    "trace.log",
    "recording.log",
)


@dataclass(frozen=True)
class TraceEvent:
    """A decoded event row suitable for text or HTML rendering."""

    index: int
    logical_time: int
    thread_id: int
    event_name: str
    details: str
    payload_size: int


@dataclass(frozen=True)
class TraceSummary:
    """Structured summary of a DRT log or failure bundle."""

    source_path: Path
    log_path: Path
    format_version: int
    event_count: int
    total_entries: int
    complete: bool
    integrity_status: str
    checksum: int | None
    threads: tuple[int, ...]
    events: tuple[TraceEvent, ...]


def load_trace(path: str | Path) -> TraceSummary:
    """
    Load a DRT log or failure-bundle directory into a structured summary.

    A directory is treated as a failure bundle. Common log names are preferred;
    otherwise the first ``*.log`` file in sorted order is used.
    """

    source_path = Path(path)
    log_path = _resolve_log_path(source_path)

    log = EventLog(log_path)
    log.open_for_replay()

    entries = tuple(log._entries)  # Same-package inspection surface.
    events = tuple(
        TraceEvent(
            index=index,
            logical_time=entry.logical_time,
            thread_id=entry.thread_id,
            event_name=entry.event_type.name,
            details=_describe_payload(entry),
            payload_size=len(entry.payload),
        )
        for index, entry in enumerate(entries)
    )

    threads = tuple(
        sorted(
            {
                entry.thread_id
                for entry in entries
                if entry.event_type != EventType.LOG_COMPLETE
            }
        )
    )

    return TraceSummary(
        source_path=source_path,
        log_path=log_path,
        format_version=log.format_version,
        event_count=len(log),
        total_entries=len(entries),
        complete=log.is_complete,
        integrity_status=_format_integrity(log),
        checksum=log.body_checksum if log.integrity_available else None,
        threads=threads,
        events=events,
    )


def format_timeline(path: str | Path) -> str:
    """Return a compact per-event timeline for a DRT log or bundle."""

    summary = load_trace(path)
    lines = [
        f"DRT trace timeline: {summary.log_path}",
        f"Format version: {summary.format_version}",
        f"Events: {summary.event_count}",
        f"Integrity: {summary.integrity_status}",
        f"Threads: {_format_threads(summary.threads)}",
        "",
        "idx  time  thread  event          details",
    ]

    for event in summary.events:
        detail = f"  {event.details}" if event.details else ""
        lines.append(
            f"{event.index:>3}  {event.logical_time:>4}  "
            f"{event.thread_id:>6}  {event.event_name:<14}{detail}"
        )

    return "\n".join(lines)


def format_log_event(entry: LogEntry) -> str:
    """Return a compact one-line description of a log event."""

    details = _describe_payload(entry)
    if details:
        return f"{entry.event_type.name} thread={entry.thread_id} ({details})"
    return f"{entry.event_type.name} thread={entry.thread_id}"


def format_explain(path: str | Path) -> str:
    """Return a higher-level explanation of what a DRT trace contains."""

    summary = load_trace(path)
    counts = _event_counts(summary.events)

    lines = [
        f"DRT trace explanation: {summary.log_path}",
        f"Source: {summary.source_path}",
        f"The log is {'complete' if summary.complete else 'incomplete'}.",
        f"Integrity: {summary.integrity_status}.",
        (
            f"It records {summary.event_count} replay events across "
            f"{len(summary.threads)} thread(s): {_format_threads(summary.threads)}."
        ),
        "",
        "Event mix:",
    ]

    for event_name, count in counts:
        lines.append(f"- {event_name}: {count}")

    schedule_events = [event for event in summary.events if event.event_name == "SCHEDULE"]
    nondeterministic_events = [
        event
        for event in summary.events
        if event.event_name in {"TIME_READ", "RANDOM_READ", "RANDOM_SEED", "IO_READ"}
    ]

    if schedule_events:
        lines.extend(
            [
                "",
                (
                    "Scheduler choices: "
                    + ", ".join(
                        f"t={event.logical_time}->thread {event.thread_id}"
                        for event in schedule_events[:8]
                    )
                    + (" ..." if len(schedule_events) > 8 else "")
                ),
            ]
        )

    if nondeterministic_events:
        lines.append("")
        lines.append("Recorded nondeterminism:")
        for event in nondeterministic_events[:8]:
            detail = f" ({event.details})" if event.details else ""
            lines.append(
                f"- t={event.logical_time} thread={event.thread_id} "
                f"{event.event_name}{detail}"
            )
        if len(nondeterministic_events) > 8:
            lines.append(f"- ... {len(nondeterministic_events) - 8} more")

    return "\n".join(lines)


def write_html_report(path: str | Path, output_path: str | Path) -> Path:
    """Write an escaped, standalone HTML report and return its path."""

    summary = load_trace(path)
    output = Path(output_path)
    rows = "\n".join(_html_event_row(event) for event in summary.events)
    counts = "\n".join(
        f"<li><code>{escape(name)}</code>: {count}</li>"
        for name, count in _event_counts(summary.events)
    )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>DRT Trace Report</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #17202a; }}
    code, table {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #d8dee4; padding: 0.4rem 0.5rem; text-align: left; }}
    th {{ background: #f6f8fa; }}
    .status {{ font-weight: 700; }}
  </style>
</head>
<body>
  <h1>DRT Trace Report</h1>
  <p><strong>Log:</strong> <code>{escape(str(summary.log_path))}</code></p>
  <p><strong>Source:</strong> <code>{escape(str(summary.source_path))}</code></p>
  <p><strong>Format:</strong> v{summary.format_version}</p>
  <p><strong>Events:</strong> {summary.event_count}</p>
  <p><strong>Threads:</strong> {escape(_format_threads(summary.threads))}</p>
  <p class="status"><strong>Integrity:</strong> {escape(summary.integrity_status)}</p>
  <h2>Event Mix</h2>
  <ul>
    {counts}
  </ul>
  <h2>Timeline</h2>
  <table>
    <thead>
      <tr><th>#</th><th>Logical Time</th><th>Thread</th><th>Event</th><th>Details</th></tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>
"""

    output.write_text(html, encoding="utf-8")
    return output


def _resolve_log_path(path: Path) -> Path:
    if path.is_file():
        return path

    if not path.exists():
        raise FileNotFoundError(f"Trace path not found: {path}")

    if not path.is_dir():
        raise ValueError(f"Trace path is neither a file nor a directory: {path}")

    for name in _BUNDLE_LOG_CANDIDATES:
        candidate = path / name
        if candidate.is_file():
            return candidate

    matches = sorted(path.glob("*.log"))
    if matches:
        return matches[0]

    raise FileNotFoundError(f"No DRT log file found in failure bundle: {path}")


def _format_integrity(log: EventLog) -> str:
    if log.integrity_available and log.integrity_valid:
        return f"verified (crc32=0x{log.body_checksum:08x})"
    if log.integrity_available:
        return "available but not verified"
    return "unavailable (legacy log format)"


def _format_threads(threads: Sequence[int]) -> str:
    if not threads:
        return "none"
    return ", ".join(f"thread {thread_id}" for thread_id in threads)


def _event_counts(events: Iterable[TraceEvent]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.event_name] = counts.get(event.event_name, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _html_event_row(event: TraceEvent) -> str:
    return (
        "<tr>"
        f"<td>{event.index}</td>"
        f"<td>{event.logical_time}</td>"
        f"<td>{event.thread_id}</td>"
        f"<td><code>{escape(event.event_name)}</code></td>"
        f"<td>{escape(event.details)}</td>"
        "</tr>"
    )


def _describe_payload(entry: LogEntry) -> str:
    if not entry.payload:
        return ""

    try:
        if entry.event_type in (EventType.TIME_READ, EventType.RANDOM_READ):
            return f"value={deserialize_float_payload(entry.payload)!r}"
        if entry.event_type == EventType.LOCK_ACQUIRE:
            mutex_id, blocking, immediate = deserialize_lock_acquire_payload(entry.payload)
            return (
                f"mutex={mutex_id} blocking={blocking} "
                f"acquired_immediately={immediate}"
            )
        if entry.event_type == EventType.LOCK_RELEASE:
            return f"mutex={deserialize_mutex_payload(entry.payload)}"
        if entry.event_type == EventType.COND_WAIT:
            return f"condition={deserialize_cond_payload(entry.payload)}"
        if entry.event_type == EventType.COND_WAKE:
            target_thread, condition_id = deserialize_cond_wake_payload(entry.payload)
            return f"target_thread={target_thread} condition={condition_id}"
        if entry.event_type == EventType.THREAD_CREATE:
            return f"new_thread={deserialize_thread_create_payload(entry.payload)}"
        if entry.event_type == EventType.THREAD_JOIN:
            target_thread, immediate = deserialize_thread_join_payload(entry.payload)
            return f"target_thread={target_thread} completed_immediately={immediate}"
        if entry.event_type == EventType.IO_READ:
            path, size, data = deserialize_io_read_payload(entry.payload)
            if path:
                return f"path={path!r} requested_size={size} bytes={len(data)}"
            return f"bytes={len(data)}"
        if entry.event_type == EventType.LOG_COMPLETE:
            entry_count, checksum = deserialize_log_complete_payload(entry.payload)
            return f"entry_count={entry_count} crc32=0x{checksum:08x}"
    except Exception as exc:
        return f"payload_decode_error={exc} raw=0x{entry.payload.hex()}"

    return f"payload=0x{entry.payload.hex()}"
