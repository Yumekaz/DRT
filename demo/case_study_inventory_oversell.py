#!/usr/bin/env python3
"""Case-study target for ``drt check`` failure bundles.

The bug is a realistic last-item checkout race: two workers both read the same
remaining inventory count before either writes the reservation.  The target is
small on purpose so the generated failure bundle is easy to replay, minimize,
and inspect.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, Tuple


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt import DRTRuntime, DRTMutex, DRTThread, runtime_yield


SKU = "GPU-42"
INITIAL_STOCK = 1
ORDER_IDS = ("order-1001", "order-1002")
BUG_TARGET = "demo.case_study_inventory_oversell:check_last_item_oversell"
FIXED_TARGET = "demo.case_study_inventory_oversell:check_last_item_fixed"


CheckoutResult = Dict[str, object]


def run_buggy_checkout() -> CheckoutResult:
    """Run the intentionally buggy checkout workflow once."""

    inventory = {SKU: INITIAL_STOCK}
    reservations = []
    audit = []

    def reserve(order_id: str) -> None:
        audit.append(f"{order_id}:read-start")
        available = inventory[SKU]
        audit.append(f"{order_id}:read={available}")

        # The bad window: another checkout can read the same stock count.
        runtime_yield()

        if available <= 0:
            audit.append(f"{order_id}:sold-out")
            return

        # A second yield makes the stale write easy for DRT to schedule.
        runtime_yield()

        inventory[SKU] = available - 1
        reservations.append(order_id)
        audit.append(f"{order_id}:reserved")

    _run_workers(reserve)

    return {
        "sku": SKU,
        "initial_stock": INITIAL_STOCK,
        "final_stock": inventory[SKU],
        "reservations": tuple(reservations),
        "audit": tuple(audit),
    }


def check_last_item_oversell() -> None:
    """DRT check target: fail when the last item is reserved twice."""

    result = run_buggy_checkout()
    reservations = result["reservations"]
    final_stock = result["final_stock"]
    accounted_units = len(reservations) + int(final_stock)

    assert accounted_units == INITIAL_STOCK, (
        f"oversold {SKU}: initial={INITIAL_STOCK} "
        f"final_stock={final_stock} reservations={reservations}"
    )


def run_fixed_checkout() -> CheckoutResult:
    """Run the same workflow with a DRT-managed mutex around the stock ledger."""

    inventory = {SKU: INITIAL_STOCK}
    reservations = []
    audit = []
    stock_lock = DRTMutex("inventory-ledger")

    def reserve(order_id: str) -> None:
        with stock_lock:
            audit.append(f"{order_id}:locked")
            available = inventory[SKU]

            # Keep the same deliberate yield inside the critical section.  The
            # mutex should make the race disappear, not the absence of yields.
            runtime_yield()

            if available <= 0:
                audit.append(f"{order_id}:sold-out")
                return

            inventory[SKU] = available - 1
            reservations.append(order_id)
            audit.append(f"{order_id}:reserved")

    _run_workers(reserve)

    return {
        "sku": SKU,
        "initial_stock": INITIAL_STOCK,
        "final_stock": inventory[SKU],
        "reservations": tuple(reservations),
        "audit": tuple(audit),
    }


def check_last_item_fixed() -> None:
    """Control target: the fixed version should pass schedule exploration."""

    result = run_fixed_checkout()
    reservations = result["reservations"]
    final_stock = result["final_stock"]
    accounted_units = len(reservations) + int(final_stock)

    assert accounted_units == INITIAL_STOCK, (
        f"fixed checkout violated inventory accounting: {result}"
    )


def _run_workers(target) -> None:
    threads = [
        DRTThread(target=target, args=(order_id,), name=order_id)
        for order_id in ORDER_IDS
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def _record_once(target) -> Tuple[bool, str]:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    temp_file.close()
    log_path = temp_file.name

    runtime = DRTRuntime(
        mode="record",
        log_path=log_path,
        schedule_strategy="scripted",
        schedule_choices=[0, 1, 0, 1, 0, 0],
    )
    try:
        runtime.run(target)
    except AssertionError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            Path(log_path).unlink()
        except OSError:
            pass

    return True, "completed without assertion"


def main() -> int:
    print("DRT inventory oversell case study")
    print(f"Bug target:   {BUG_TARGET}")
    print(f"Fixed target: {FIXED_TARGET}")

    passed, detail = _record_once(check_last_item_oversell)
    if passed:
        print("Buggy target did not fail under the built-in scripted schedule.")
        print(f"Log: {detail}")
        return 1

    print(f"Captured expected buggy failure: {detail}")

    fixed_passed, fixed_detail = _record_once(check_last_item_fixed)
    if not fixed_passed:
        print(f"Fixed target failed unexpectedly: {fixed_detail}")
        return 1

    print(f"Fixed target passed under the same scripted schedule: {fixed_detail}")
    print("Use the docs command sequence to create a replayable failure bundle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
