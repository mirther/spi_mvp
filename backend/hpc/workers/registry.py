"""Explicit registry of executable SPI modules.

Module contract for this initial worker:
- SEGYIN: execute(parameters, context=...) -> WorkingDataset
- SEGYOUT: execute(dataset, parameters, context=...) -> result dictionary
"""

from dataclasses import dataclass
from typing import Any, Callable

from workers.modules import segyin, segyout


@dataclass(frozen=True, slots=True)
class ModuleRegistration:
    module_id: str
    execute: Callable[..., Any]
    input_kind: str
    output_kind: str


MODULE_REGISTRY: dict[str, ModuleRegistration] = {
    "segyin": ModuleRegistration(
        module_id="segyin",
        execute=segyin.execute,
        input_kind="none",
        output_kind="working_dataset",
    ),
    "segyout": ModuleRegistration(
        module_id="segyout",
        execute=segyout.execute,
        input_kind="working_dataset",
        output_kind="file",
    ),
}


def get_module(module_id: str) -> ModuleRegistration:
    try:
        return MODULE_REGISTRY[module_id]
    except KeyError as exc:
        raise KeyError(f"No worker implementation is registered for module '{module_id}'.") from exc
