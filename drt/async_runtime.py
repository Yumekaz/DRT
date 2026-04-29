"""Opt-in deterministic scheduler for async/await DRT tasks.

This module supports DRT-managed async coroutines that yield through
``drt_async_yield()`` or ``drt_async_sleep()``. It is intentionally not a
transparent replacement for the standard ``asyncio`` event loop.
"""

from __future__ import annotations

import inspect
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from .events import (
    EventType,
    LogEntry,
    deserialize_thread_create_payload,
    serialize_thread_create_payload,
)
from .exceptions import DivergenceError, RuntimeStateError
from .log import EventLog


class _YieldPoint:
    """Awaitable token recognized by ``DRTAsyncRuntime``."""

    def __await__(self):
        yield self
        return None


async def drt_async_yield() -> None:
    """Yield control to the DRT async scheduler."""
    await _YieldPoint()


async def drt_async_sleep(seconds: float = 0.0) -> None:
    """Logical async sleep; records no wall-clock delay and yields once."""
    await _YieldPoint()


@dataclass
class _AsyncTask:
    task_id: int
    coroutine: Any
    done: bool = False
    result: Any = None
    exception: Optional[BaseException] = None


class DRTAsyncRuntime:
    """Deterministic record/replay runtime for DRT-managed async tasks."""

    def __init__(
        self,
        mode: str = "record",
        log_path: str = "async-execution.log",
        schedule_strategy: str = "round_robin",
        schedule_seed: Optional[int] = None,
        schedule_choices: Optional[Sequence[int]] = None,
        schedule_priorities: Optional[Mapping[int, int]] = None,
    ):
        if mode not in ("record", "replay"):
            raise ValueError("mode must be 'record' or 'replay'")
        if schedule_strategy not in ("round_robin", "random", "scripted", "priority"):
            raise ValueError(
                "schedule_strategy must be 'round_robin', 'random', "
                "'scripted', or 'priority'"
            )

        self.mode = mode
        self.log_path = Path(log_path)
        self.log = EventLog(self.log_path)
        self.schedule_strategy = schedule_strategy
        self.schedule_seed = schedule_seed
        self._rng = random.Random(schedule_seed)
        self._schedule_choices = list(schedule_choices or [])
        self._schedule_choice_index = 0
        self._schedule_priorities = {
            int(task_id): int(priority)
            for task_id, priority in (schedule_priorities or {}).items()
        }
        self._recorded_schedule_choices: list[int] = []
        self._tasks: Dict[int, _AsyncTask] = {}
        self._next_task_id = 1
        self._current_task_id = 0
        self._logical_time = 0
        self._replay_index = 0
        self._initialized = False

    def run(self, target: Callable[["DRTAsyncRuntime"], Any], *args, **kwargs) -> Any:
        """Run a target that creates DRT async tasks or returns a coroutine."""
        try:
            self._initialize()
            result = target(self, *args, **kwargs)
            main_task_id = None

            if inspect.iscoroutine(result):
                main_task_id = self.create_task(result)
                result = None

            self._run_tasks()
            self._verify_replay_complete()

            if self.mode == "record":
                self.log.finalize()

            if main_task_id is not None:
                return self._tasks[main_task_id].result
            return result
        finally:
            self._close_pending_tasks()
            self.log.close()
            self._initialized = False

    def create_task(self, coroutine: Any) -> int:
        """Register a coroutine with the deterministic async scheduler."""
        if not inspect.iscoroutine(coroutine):
            raise TypeError("create_task() requires a coroutine object")

        if self.mode == "replay":
            try:
                entry = self._consume_replay_event(EventType.THREAD_CREATE, 0)
            except BaseException:
                coroutine.close()
                raise
            task_id = deserialize_thread_create_payload(entry.payload)
        else:
            task_id = self._next_task_id
            self._next_task_id += 1
            self._log_event(
                EventType.THREAD_CREATE,
                serialize_thread_create_payload(task_id),
                thread_id=0,
            )

        self._tasks[task_id] = _AsyncTask(task_id=task_id, coroutine=coroutine)
        return task_id

    @property
    def recorded_schedule_choices(self) -> list[int]:
        """Runnable-list indexes chosen during record mode."""
        return list(self._recorded_schedule_choices)

    def _initialize(self) -> None:
        if self._initialized:
            raise RuntimeStateError("DRTAsyncRuntime already initialized")
        if self.mode == "record":
            self.log.open_for_record()
        else:
            self.log.open_for_replay()
        self._initialized = True

    def _run_tasks(self) -> None:
        while True:
            runnable = self._runnable_task_ids()
            if not runnable:
                return

            task_id = self._schedule_next(runnable)
            self._current_task_id = task_id
            task = self._tasks[task_id]

            try:
                yielded = task.coroutine.send(None)
            except StopIteration as stop:
                task.done = True
                task.result = stop.value
                if self.mode == "record":
                    self._log_event(EventType.THREAD_EXIT, b"", thread_id=task_id)
                else:
                    self._consume_replay_event(EventType.THREAD_EXIT, task_id)
                continue
            except BaseException as exc:
                task.done = True
                task.exception = exc
                if self.mode == "record":
                    self._log_event(EventType.THREAD_EXIT, b"", thread_id=task_id)
                raise

            if not isinstance(yielded, _YieldPoint):
                raise RuntimeError(
                    "DRTAsyncRuntime can only drive coroutines that yield via "
                    "drt_async_yield() or drt_async_sleep()"
                )

    def _runnable_task_ids(self) -> list[int]:
        return sorted(task_id for task_id, task in self._tasks.items() if not task.done)

    def _schedule_next(self, runnable: Sequence[int]) -> int:
        if self.mode == "replay":
            entry = self._consume_replay_event(EventType.SCHEDULE)
            if entry.thread_id not in runnable:
                raise DivergenceError(
                    "Async replay scheduled a task that is not runnable",
                    self._logical_time,
                    f"task in {list(runnable)}",
                    f"task {entry.thread_id}",
                )
            chosen = entry.thread_id
        else:
            choice_index = self._choose_record_index(list(runnable))
            self._recorded_schedule_choices.append(choice_index)
            chosen = list(runnable)[choice_index]
            self._log_event(EventType.SCHEDULE, b"", thread_id=chosen)

        self._logical_time += 1
        return chosen

    def _choose_record_index(self, runnable: list[int]) -> int:
        if self.schedule_strategy == "random":
            return self._rng.randrange(len(runnable))
        if (
            self.schedule_strategy == "scripted"
            and self._schedule_choice_index < len(self._schedule_choices)
        ):
            raw_choice = self._schedule_choices[self._schedule_choice_index]
            self._schedule_choice_index += 1
            return raw_choice % len(runnable)
        if self.schedule_strategy == "priority":
            task_id = min(
                runnable,
                key=lambda candidate: (
                    self._schedule_priorities.get(candidate, 0),
                    candidate,
                ),
            )
            return runnable.index(task_id)
        if self._current_task_id in runnable and len(runnable) > 1:
            current_index = runnable.index(self._current_task_id)
            return (current_index + 1) % len(runnable)
        return 0

    def _log_event(
        self,
        event_type: EventType,
        payload: bytes,
        thread_id: Optional[int] = None,
    ) -> None:
        entry = LogEntry(
            logical_time=self._logical_time,
            thread_id=thread_id if thread_id is not None else self._current_task_id,
            event_type=event_type,
            payload=payload,
        )
        self.log.append(entry)

    def _peek_replay_event(self) -> Optional[LogEntry]:
        if self.mode != "replay":
            return None
        if self._replay_index >= len(self.log._entries):
            return None
        entry = self.log._entries[self._replay_index]
        if entry.event_type == EventType.LOG_COMPLETE:
            return None
        return entry

    def _consume_replay_event(
        self,
        expected_type: EventType,
        expected_thread_id: Optional[int] = None,
    ) -> LogEntry:
        entry = self._peek_replay_event()
        if entry is None:
            raise DivergenceError(
                f"Expected {expected_type.name} but async replay log ended",
                self._logical_time,
                expected_type.name,
                "end of log",
            )
        if entry.logical_time != self._logical_time:
            raise DivergenceError(
                "Async replay event appeared at an unexpected logical time",
                self._logical_time,
                f"{expected_type.name} at logical time {self._logical_time}",
                f"{entry.event_type.name} at logical time {entry.logical_time}",
            )
        if entry.event_type != expected_type:
            raise DivergenceError(
                f"Expected {expected_type.name}",
                self._logical_time,
                expected_type.name,
                entry.event_type.name,
            )
        if expected_thread_id is not None and entry.thread_id != expected_thread_id:
            raise DivergenceError(
                f"Expected event by task {expected_thread_id}",
                self._logical_time,
                f"task {expected_thread_id}",
                f"task {entry.thread_id}",
            )
        self._replay_index += 1
        return entry

    def _verify_replay_complete(self) -> None:
        if self.mode != "replay":
            return
        if self._replay_index >= len(self.log._entries):
            raise DivergenceError(
                "Async replay ended without LOG_COMPLETE",
                self._logical_time,
                "LOG_COMPLETE",
                "end of log",
            )
        entry = self.log._entries[self._replay_index]
        if entry.event_type != EventType.LOG_COMPLETE:
            raise DivergenceError(
                "Async replay finished before log was exhausted",
                self._logical_time,
                "LOG_COMPLETE",
                entry.event_type.name,
            )
        self._replay_index += 1
        if self._replay_index != len(self.log._entries):
            extra = self.log._entries[self._replay_index]
            raise DivergenceError(
                "Async replay log has trailing events",
                self._logical_time,
                "end of log",
                extra.event_type.name,
            )

    def _close_pending_tasks(self) -> None:
        for task in self._tasks.values():
            if task.done:
                continue
            close = getattr(task.coroutine, "close", None)
            if callable(close):
                close()
            task.done = True
