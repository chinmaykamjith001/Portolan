"""Sandbox: the static check, the child's own defenses, and the resource limits (spec §10.7.3)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from portolan.sandbox import SandboxResult, run_program, runner
from portolan.sandbox.check import MAX_SOURCE_BYTES, check_source

TABLE = """\
import json
import numpy as np
z = np.array([2.0, 1.0, 0.1])
p = np.exp(z - z.max())
p /= p.sum()
rows = [[f"{a:.1f}", f"{b:.3f}"] for a, b in zip(z, p)]
print(json.dumps({"columns": ["score", "weight"], "rows": rows}))
"""


def sandbox_dirs() -> set[str]:
    return {p.name for p in Path(tempfile.gettempdir()).glob("portolan-sandbox-*")}


async def unchecked(source: str, modules: list[str] | None = None, **kw: float) -> SandboxResult:
    """Skip the static check, so the child's own defenses are what's being tested."""
    return await runner._execute(  # pyright: ignore[reportPrivateUsage]
        source,
        modules or [],
        kw.get("timeout", 5.0),
        int(kw.get("memory_mb", 256)),
        time.monotonic(),
    )


# -- the happy path ---------------------------------------------------------------------------


async def test_numpy_table_is_run_and_parsed() -> None:
    result = await run_program(TABLE)
    assert result.ok, result.error
    assert result.error is None
    assert result.output == {
        "columns": ["score", "weight"],
        "rows": [["2.0", "0.659"], ["1.0", "0.242"], ["0.1", "0.099"]],
    }
    assert json.loads(result.stdout.strip()) == result.output


async def test_math_and_statistics_and_fractions_are_allowed() -> None:
    src = (
        "import json, math, statistics\nfrom fractions import Fraction\n"
        "print(json.dumps([math.sqrt(16), statistics.mean([1, 2, 3]), str(Fraction(1, 3))]))"
    )
    result = await run_program(src)
    assert result.ok, result.error
    assert result.output == [4.0, 2, "1/3"]


async def test_non_json_output_is_kept_as_text() -> None:
    result = await run_program("print('hello')")
    assert result.ok
    assert result.output is None
    assert result.stdout == "hello\n"


async def test_only_the_last_line_is_parsed() -> None:
    result = await run_program("print('noise')\nprint('{\"a\": 1}')")
    assert result.output == {"a": 1}


# -- static check -----------------------------------------------------------------------------


@pytest.mark.parametrize("module", ["os", "sys", "subprocess", "socket", "importlib", "ctypes"])
async def test_forbidden_imports_are_rejected(module: str) -> None:
    result = await run_program(f"import {module}")
    assert not result.ok
    assert result.error == f"The example tried to use `{module}`, which isn't allowed."


@pytest.mark.parametrize(
    "src",
    ["from os import path", "from os.path import join", "import os.path", "import numpy, os"],
)
async def test_from_and_dotted_imports_are_checked(src: str) -> None:
    result = await run_program(src)
    assert not result.ok
    assert "`os`" in (result.error or "")


async def test_relative_import_is_rejected() -> None:
    result = await run_program("from . import x")
    assert not result.ok
    assert "relative import" in (result.error or "")


@pytest.mark.parametrize(
    "name",
    [
        "open", "exec", "eval", "compile", "getattr", "setattr", "delattr", "vars", "globals",
        "locals", "breakpoint", "input", "__import__",
    ],
)  # fmt: skip
async def test_forbidden_builtins_are_rejected(name: str) -> None:
    result = await run_program(f"x = {name}")
    assert not result.ok
    assert f"`{name}`" in (result.error or "")


@pytest.mark.parametrize(
    "src",
    [
        "x = ().__class__",
        "x = (1).__class__.__mro__",
        "import math\nmath.__dict__",
        "def f(): pass\nf.__globals__",
        "x = __builtins__",
        "x = '{0.__class__}'.format(1)",
        "import statistics\nstatistics.random._os.system('id')",
        "import numpy as np\nnp.os.system('id')",
        "import numpy as np\nnp.load('x.npy')",
        "import numpy as np\nnp.fromfile('/etc/passwd')",
        "import numpy as np\nnp.loadtxt('/etc/passwd')",
        "import json\njson.codecs.open('/etc/passwd')",
        "from numpy import load",
    ],
)
async def test_escape_routes_are_rejected(src: str) -> None:
    result = await run_program(src)
    assert not result.ok, src
    assert "isn't allowed" in (result.error or ""), result.error


async def test_syntax_error_has_a_plain_message() -> None:
    result = await run_program("x = (")
    assert not result.ok
    assert result.error == "The example has a syntax error on line 1."


async def test_oversized_program_is_rejected() -> None:
    result = await run_program("x = 1\n" * (MAX_SOURCE_BYTES // 4))
    assert not result.ok
    assert "too long" in (result.error or "")


def test_check_returns_tree_for_good_source() -> None:
    tree, problem = check_source("x = 1")
    assert tree is not None and problem is None


# -- the child's own layer (static check bypassed) --------------------------------------------


async def test_child_has_no_open_builtin() -> None:
    result = await unchecked("open('/etc/passwd')")
    assert not result.ok
    assert result.error is not None and "NameError" in result.error


async def test_child_rejects_unlisted_imports_at_runtime() -> None:
    result = await unchecked("import os")
    assert not result.ok
    assert result.error == "The example tried to use `os`, which isn't allowed."


async def test_child_blocks_file_reads_even_through_numpy() -> None:
    result = await unchecked("import numpy as np\nnp.loadtxt('/etc/passwd')", ["numpy"])
    assert not result.ok
    assert "files or other programs" in (result.error or "")


async def test_child_blocks_writes_even_through_numpy(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    src = f"import numpy as np\nnp.savetxt({str(target)!r}, np.zeros(3))"
    result = await unchecked(src, ["numpy"])
    assert not result.ok
    assert not target.exists()


async def test_child_blocks_processes_and_files_reached_through_stdlib_internals() -> None:
    probes = [
        "statistics.random._os.system('echo hi > /tmp/portolan-sandbox-probe')",
        "statistics.random._os.listdir('/')",
        "statistics.random._os.remove('/tmp/portolan-sandbox-probe')",
        "statistics.random._os.fork()",
    ]
    for probe in probes:
        result = await unchecked(f"import statistics\n{probe}", ["statistics"])
        assert not result.ok, probe
        assert "files or other programs" in (result.error or ""), probe
    assert not Path("/tmp/portolan-sandbox-probe").exists()


async def test_parent_environment_is_not_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTOLAN_FAKE_SECRET", "hunter2")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    src = "import json, statistics\nprint(json.dumps(dict(statistics.random._os.environ)))"
    result = await unchecked(src, ["json", "statistics"])
    assert result.ok, result.error
    assert result.output == {}
    assert "hunter2" not in result.stdout and "not-a-real-key" not in result.stdout


async def test_child_is_started_with_an_explicit_minimal_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PORTOLAN_FAKE_SECRET", "hunter2")
    seen: dict[str, object] = {}
    real = asyncio.create_subprocess_exec

    async def spy(*args: object, **kwargs: object):
        if "-I" in args:
            seen.update(kwargs)
        return await real(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    result = await run_program("print(1)")
    assert result.ok
    assert seen["env"] == runner.ENV
    assert "PORTOLAN_FAKE_SECRET" not in seen["env"]  # type: ignore[operator]
    assert seen["stdin"] == asyncio.subprocess.DEVNULL


async def test_working_directory_is_a_fresh_temp_dir() -> None:
    # Reading a relative path can only reach the sandbox's own empty directory.
    result = await unchecked(
        "import numpy as np\nnp.loadtxt('example.py')", ["numpy"]
    )  # example.py is the program itself, the only file there
    assert not result.ok
    assert "NameError" not in (result.error or "")


# -- limits -----------------------------------------------------------------------------------


async def test_infinite_loop_times_out() -> None:
    start = time.monotonic()
    result = await run_program("while True:\n    pass", timeout=1.0)
    assert not result.ok
    assert result.error == "The example took longer than 1 second."
    assert time.monotonic() - start < 4


async def test_sleep_style_busy_wait_in_numpy_times_out() -> None:
    src = "import numpy as np\nwhile True:\n    np.linalg.inv(np.random.rand(60, 60))"
    result = await run_program(src, timeout=1.5)
    assert not result.ok
    assert "longer than 1.5 seconds" in (result.error or "")


async def test_memory_bomb_fails_cleanly() -> None:
    result = await run_program("x = [0] * 10**9", memory_mb=128)
    assert not result.ok
    assert result.error == "The example needed more than 128 MB of memory."


async def test_numpy_memory_bomb_fails_cleanly() -> None:
    result = await run_program("import numpy as np\nx = np.ones((20000, 20000))", memory_mb=128)
    assert not result.ok
    assert "memory" in (result.error or "")


async def test_output_is_capped() -> None:
    result = await run_program("while True:\n    print('x' * 1000)", timeout=5.0)
    assert not result.ok
    assert "too much" in (result.error or "")
    assert len(result.stdout) <= runner.STDOUT_CAP


async def test_runtime_error_is_reported_without_a_traceback() -> None:
    result = await run_program("x = 1 / 0")
    assert not result.ok
    assert result.error == "The example failed: ZeroDivisionError: division by zero"
    assert "Traceback" not in (result.error or "")
    assert "portolan-sandbox" not in (result.error or "")


async def test_oversized_file_write_is_impossible() -> None:
    result = await unchecked("import json\njson.dump('x' * 10**7, json.codecs)", ["json"])
    assert not result.ok


# -- hygiene ----------------------------------------------------------------------------------


async def test_no_temp_dirs_are_left_behind() -> None:
    before = sandbox_dirs()
    await run_program("print(1)")
    await run_program("while True: pass", timeout=0.5)
    await run_program("x = (")
    await run_program("x = 1 / 0")
    assert sandbox_dirs() == before


async def test_parallel_runs_do_not_interfere() -> None:
    sources = [f"import json\nprint(json.dumps({{'n': {i}}}))" for i in range(8)]
    results = await asyncio.gather(*(run_program(s) for s in sources))
    assert [r.output for r in results] == [{"n": i} for i in range(8)]


async def test_a_timed_out_run_leaves_no_process_behind() -> None:
    result = await run_program("while True: pass", timeout=0.5)
    assert not result.ok
    await asyncio.sleep(0.2)
    me = os.getpid()
    leftovers = [
        p
        for p in Path("/proc").glob("[0-9]*")
        if (p / "stat").exists() and _ppid(p) == me and "_child.py" in _cmdline(p)
    ]
    assert leftovers == []


def _ppid(proc: Path) -> int:
    try:
        return int((proc / "stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return -1


def _cmdline(proc: Path) -> str:
    try:
        return (proc / "cmdline").read_text()
    except OSError:
        return ""


async def test_realistic_numpy_example_fits_the_default_limits() -> None:
    src = """\
import json
import numpy as np
rng = np.random.default_rng(0)
q = rng.normal(size=(4, 8))
k = rng.normal(size=(4, 8))
scores = q @ k.T / np.sqrt(8)
w = np.exp(scores - scores.max(axis=-1, keepdims=True))
w /= w.sum(axis=-1, keepdims=True)
inv = np.linalg.inv(np.eye(3) * 2)
spec = np.abs(np.fft.fft(np.arange(8.0)))
rows = [[str(i), f"{s:.3f}"] for i, s in enumerate(w.sum(axis=-1))]
check = [float(inv[0, 0]), float(spec[0])]
print(json.dumps({"columns": ["row", "sum"], "rows": rows, "check": check}))
"""
    result = await run_program(src)
    assert result.ok, result.error
    assert isinstance(result.output, dict)
    assert result.output["rows"] == [["0", "1.000"], ["1", "1.000"], ["2", "1.000"], ["3", "1.000"]]
    assert result.output["check"] == [0.5, 28.0]


async def test_network_namespace_is_used_when_available() -> None:
    prefix = await runner._network_prefix()  # pyright: ignore[reportPrivateUsage]
    # Where `unshare -n` works the child gets an empty network namespace; elsewhere the import
    # allowlist and the audit hook are what stop sockets, and a warning is logged once.
    assert prefix == [] or prefix[-1] == "--"
