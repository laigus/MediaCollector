from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path


async def choose_folder(initial: str) -> str | None:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.core.folder_picker", initial,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(stderr.decode("utf-8", errors="replace"))
    return json.loads(stdout)


def _show_dialog(initial: str) -> str | None:
    from tkinter import Tk, filedialog

    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        options = {"initialdir": initial} if initial and Path(initial).is_dir() else {}
        return filedialog.askdirectory(
            parent=root, title="选择下载文件夹", mustexist=True, **options,
        ) or None
    finally:
        root.destroy()


if __name__ == "__main__":
    print(json.dumps(_show_dialog(sys.argv[1])))
