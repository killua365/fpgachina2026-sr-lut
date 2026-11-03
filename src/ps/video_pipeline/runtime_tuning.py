"""Reversible board runtime tuning for the real-time video path."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


def find_interface_irqs(interrupts: str, interface: str) -> list[int]:
    """Return IRQ numbers whose /proc/interrupts label contains interface."""

    if not interface or any(character.isspace() for character in interface):
        raise ValueError("network interface must be a non-empty token")
    result: list[int] = []
    for line in interrupts.splitlines():
        if ":" not in line:
            continue
        prefix, details = line.split(":", 1)
        if interface not in details.split():
            continue
        try:
            result.append(int(prefix.strip()))
        except ValueError:
            continue
    return result


class NetworkIrqAffinity:
    """Temporarily route one network interface's IRQs to a selected CPU."""

    def __init__(
        self,
        *,
        interface: str,
        target_cpu: int | None,
        interrupts_path: Path = Path("/proc/interrupts"),
        irq_root: Path = Path("/proc/irq"),
    ) -> None:
        self.interface = interface
        self.target_cpu = target_cpu
        self.interrupts_path = Path(interrupts_path)
        self.irq_root = Path(irq_root)
        self._original: list[tuple[Path, str]] = []
        self._report: dict[str, Any] = {
            "enabled": target_cpu is not None,
            "interface": interface,
            "target_cpu": target_cpu,
            "irq_numbers": [],
            "apply_status": "not-run",
            "restore_status": "not-run",
        }

    def apply(self) -> dict[str, Any]:
        if self.target_cpu is None:
            self._report["apply_status"] = "disabled"
            self._report["restore_status"] = "not-needed"
            return self.report()
        if self.target_cpu < 0:
            raise ValueError("target CPU must be non-negative")
        target = str(int(self.target_cpu))
        try:
            interrupts = self.interrupts_path.read_text(encoding="ascii")
            irq_numbers = find_interface_irqs(interrupts, self.interface)
            self._report["irq_numbers"] = irq_numbers
            if not irq_numbers:
                self._report["apply_status"] = "interface-irq-not-found"
                self._report["restore_status"] = "not-needed"
                return self.report()
            for irq_number in irq_numbers:
                affinity_path = self.irq_root / str(irq_number) / "smp_affinity_list"
                original = affinity_path.read_text(encoding="ascii").strip()
                if not original:
                    raise RuntimeError(f"IRQ {irq_number} has an empty affinity list")
                affinity_path.write_text(target + "\n", encoding="ascii")
                effective = affinity_path.read_text(encoding="ascii").strip()
                if effective != target:
                    raise RuntimeError(
                        f"IRQ {irq_number} affinity is {effective!r}, expected {target!r}"
                    )
                self._original.append((affinity_path, original))
            self._report["original_affinity_lists"] = [
                original for _, original in self._original
            ]
            self._report["effective_affinity_list"] = target
            self._report["apply_status"] = "applied"
        except (OSError, RuntimeError, ValueError) as exc:
            self._report["apply_status"] = "failed"
            self._report["error"] = f"{type(exc).__name__}: {exc}"
            self.restore()
        return self.report()

    def restore(self) -> dict[str, Any]:
        if not self._original:
            if self._report["restore_status"] == "not-run":
                self._report["restore_status"] = "not-needed"
            return self.report()
        errors: list[str] = []
        retry: list[tuple[Path, str]] = []
        for affinity_path, original in reversed(self._original):
            try:
                affinity_path.write_text(original + "\n", encoding="ascii")
            except OSError as exc:
                errors.append(f"{affinity_path}: {type(exc).__name__}: {exc}")
                retry.append((affinity_path, original))
        self._original = list(reversed(retry))
        if errors:
            self._report["restore_status"] = "failed"
            self._report["restore_errors"] = errors
        else:
            self._report["restore_status"] = "restored"
        return self.report()

    def report(self) -> dict[str, Any]:
        return deepcopy(self._report)


__all__ = ["NetworkIrqAffinity", "find_interface_irqs"]
