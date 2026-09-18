# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""QGIS Processing handlers: run/list/describe algorithms, plus the async task registry (QgsTask-backed) that run_processing(async=true)."""



from __future__ import annotations

from .layer_lookup import _find_layer  # noqa: F401 - importable here as before the split
from .processing_decisions import (  # noqa: F401 - moved here, re-exported for the callers of processing_tools
    _goes_to_task,
    _threadable,
)
from .processing_destinations import (  # noqa: F401 - moved here, re-exported for the callers of processing_tools
    _destination_names,
    _gpkg_table_target,
    _output_names,
)
from .processing_help import (  # noqa: F401 - moved here, re-exported for the callers of processing_tools
    _get_algorithm_help,
    _list_algorithms,
)
from .processing_run import (  # noqa: F401 - moved here, re-exported for the callers of processing_tools
    _ASYNC_PIXELS,
    _POLL_INTERVAL_S,
    _PROCESSING_TASKS,
    _cancel_task,
    _get_task_status,
    _heavy_inputs,
    _list_tasks,
    _on_proc_terminated,
    _process_outputs,
    _run_processing,
    _start_async_processing,
    _sweep_consumed_tasks,
    _sync_with_qgis,
    canceled_output,
    raster_file_problem,
    register_task,
    shutdown,
)



__all__ = [
    "_ASYNC_PIXELS",
    "_POLL_INTERVAL_S",
    "_PROCESSING_TASKS",
    "_cancel_task",
    "_destination_names",
    "_find_layer",
    "_get_algorithm_help",
    "_get_task_status",
    "_goes_to_task",
    "_gpkg_table_target",
    "_heavy_inputs",
    "_list_algorithms",
    "_list_tasks",
    "_on_proc_terminated",
    "_output_names",
    "_process_outputs",
    "_run_processing",
    "_start_async_processing",
    "_sweep_consumed_tasks",
    "_sync_with_qgis",
    "_threadable",
    "canceled_output",
    "raster_file_problem",
    "register_task",
    "shutdown",
]
