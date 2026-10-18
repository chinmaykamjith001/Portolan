"""Parent side of the sandbox: vet the program, run it in a locked-down child, read the table."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

from portolan.sandbox.check import check_source, imported_modules
from portolan.sandbox.types import SandboxResult

log = logging.getLogger(__name__)

STDOUT_CAP = 64 * 1024
STDERR_CAP = 8 * 1024
CHILD = Path(__file__).with_name("_child.py")
MARK = "PORTOLAN_SANDBOX"
POSIX = os.name == "posix"

# Single-threaded numeric libraries: no worker threads to outlive the process group, and a small
# address-space footprint under RLIMIT_AS.
ENV = {
    "PATH": "/usr/bin:/bin",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}

_net_prefix: list[str] | None = None
_warned = False


async def _network_prefix() -> list[str]:
    """`unshare -n` (own, empty network namespace) if it works for this user; else nothing."""
    global _net_prefix, _warned
    if _net_prefix is not None:
        return _net_prefix
    _net_prefix = []
    exe = shutil.which("unshare") if sys.platform.startswith("linux") else None
    if exe:
        for flags in (["-n"], ["-r", "-n"]):
            try:
                probe = await asyncio.create_subprocess_exec(
                    exe, *flags, "true",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )  # fmt: skip
                if await probe.wait() == 0:
                    _net_prefix = [exe, *flags, "--"]
                    break
            except OSError:
                continue
    if not _net_prefix and not _warned:
        _warned = True
        log.warning("sandbox: no network namespace available; relying on the import allowlist")
    return _net_prefix


async def _read_capped(
    stream: asyncio.StreamReader, cap: int, on_overflow: asyncio.Event | None
) -> bytes:
    data = bytearray()
    while chunk := await stream.read(8192):
        if len(data) < cap:
            data.extend(chunk[: cap - len(data)])
        if len(data) >= cap and on_overflow is not None:
            on_overflow.set()
    return bytes(data)


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        if POSIX:
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


def _last_json(text: str) -> object | None:
    for line in reversed(text.splitlines()):
        if line.strip():
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


def _marker(stderr: str) -> tuple[str, str] | None:
    for line in reversed(stderr.splitlines()):
        if line.startswith(MARK + "\t"):
            parts = line.split("\t", 2)
            if len(parts) == 3:
                return parts[1], parts[2]
    return None


async def run_program(source: str, *, timeout: float = 5.0, memory_mb: int = 256) -> SandboxResult:
    """Statically check, then run `source` in a locked-down subprocess (see §10.7.3)."""
    started = time.monotonic()

    def fail(message: str, stdout: str = "") -> SandboxResult:
        return SandboxResult(False, None, stdout, message, time.monotonic() - started)

    tree, problem = check_source(source)
    if tree is None:
        return fail(problem or "The example couldn't be checked.")
    return await _execute(source, sorted(imported_modules(tree)), timeout, memory_mb, started)


async def _execute(
    source: str, modules: list[str], timeout: float, memory_mb: int, started: float
) -> SandboxResult:
    """Run already-vetted source in the child. Tests call this directly to exercise the child's own
    defenses (resource limits, reduced builtins, audit hook) without the static check in front."""

    def fail(message: str, stdout: str = "") -> SandboxResult:
        return SandboxResult(False, None, stdout, message, time.monotonic() - started)

    if not POSIX:
        log.warning("sandbox: no resource limits on this platform; only the timeout applies")

    work = tempfile.mkdtemp(prefix="portolan-sandbox-")
    try:
        script = Path(work) / "example.py"
        script.write_text(source, encoding="utf-8")
        prefix = await _network_prefix() if POSIX else []
        mods = ",".join(modules)
        cpu_s = int(timeout) + 2
        proc = await asyncio.create_subprocess_exec(
            *prefix, sys.executable, "-I", str(CHILD),
            str(script), str(memory_mb), str(cpu_s), mods,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work,
            env=ENV,
            start_new_session=POSIX,
        )  # fmt: skip
        assert proc.stdout is not None and proc.stderr is not None
        overflow = asyncio.Event()
        out_task = asyncio.ensure_future(_read_capped(proc.stdout, STDOUT_CAP + 1, overflow))
        err_task = asyncio.ensure_future(_read_capped(proc.stderr, STDERR_CAP, None))
        watch = asyncio.ensure_future(overflow.wait())
        wait = asyncio.ensure_future(proc.wait())
        timed_out = False
        try:
            await asyncio.wait({watch, wait}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if not wait.done():
                timed_out = not overflow.is_set()
                _kill(proc)
            await wait
            out_raw, err_raw = await asyncio.gather(out_task, err_task)
        finally:
            _kill(proc)
            watch.cancel()
            for task in (out_task, err_task, wait):
                if not task.done():
                    task.cancel()

        text = out_raw[:STDOUT_CAP].decode("utf-8", errors="replace")
        too_long = len(out_raw) > STDOUT_CAP
        err = err_raw.decode("utf-8", errors="replace")
        elapsed = time.monotonic() - started
        code = proc.returncode
        cpu_killed = POSIX and code == -signal.SIGXCPU
        if timed_out or cpu_killed:
            unit = "second" if timeout == 1 else "seconds"
            return fail(f"The example took longer than {timeout:g} {unit}.", text)
        if too_long:
            return fail("The example printed too much (the limit is 64 KB).", text)
        found = _marker(err)
        if found is not None:
            kind, message = found
            if kind == "memory":
                return fail(f"The example needed more than {memory_mb} MB of memory.", text)
            if kind == "blocked":
                if message.startswith("The example"):
                    return fail(message, text)
                return fail(
                    "The example tried to use files or other programs, which isn't allowed.", text
                )
            return fail(f"The example failed: {message}", text)
        if code != 0:
            return fail("The example crashed before finishing.", text)
        return SandboxResult(True, _last_json(text), text, None, elapsed)
    finally:
        shutil.rmtree(work, ignore_errors=True)
