# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






def _drop_stale_modules() -> None:









    import sys

    prefix = __name__ + "."
    for name in [key for key in sys.modules if key.startswith(prefix)]:
        sys.modules.pop(name, None)


_drop_stale_modules()


def _read_ahead() -> None:










    import importlib.util
    import os
    import threading

    if os.name != "nt":
        return
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    try:
        if os.path.exists(importlib.util.cache_from_source(os.path.join(src, "plugin.py"))):
            return
    except (NotImplementedError, ValueError):
        return

    def read_all() -> None:
        from concurrent.futures import ThreadPoolExecutor

        files = [os.path.join(folder, name) for folder, dirs, names in os.walk(src)
                 for name in names if name.endswith(".py")]

        def read(path: str) -> None:
            try:
                with open(path, "rb") as handle:
                    handle.read()
            except OSError:
                pass

        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="ai-agent-read-ahead") as pool:
            list(pool.map(read, files))

    threading.Thread(target=read_all, name="ai-agent-read-ahead", daemon=True).start()


_read_ahead()


def classFactory(iface):
    from .src.plugin import AIAgentPlugin

    return AIAgentPlugin(iface)
