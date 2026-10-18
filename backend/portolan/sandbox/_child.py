"""Runs inside the sandbox subprocess: `python -I _child.py <source> <memory_mb> <cpu_s> <mods>`.

Stdlib only. The parent already vetted the source with `ast`; this is the second layer: resource
limits, a reduced `builtins`, a guarded `__import__`, and an audit hook that blocks file access
outside the interpreter's own install, sockets, processes and other side effects. Failures are
reported on stderr as one line `PORTOLAN_SANDBOX<TAB>kind<TAB>message` and a non-zero exit.
"""

import builtins
import os
import sys
from collections.abc import Mapping, Sequence
from types import ModuleType

MARK = "PORTOLAN_SANDBOX"
ALLOWED = {"math", "statistics", "fractions", "json", "numpy"}
REMOVED = {
    "open", "exec", "eval", "compile", "getattr", "setattr", "delattr", "vars", "globals",
    "locals", "breakpoint", "input", "help", "exit", "quit", "memoryview", "copyright",
    "credits", "license",
}  # fmt: skip
BLOCKED_EVENT_PREFIXES = (
    "socket.", "subprocess.", "ctypes.", "shutil.", "os.system", "os.exec", "os.spawn",
    "os.posix_spawn", "os.fork", "os.forkpty", "os.kill", "os.killpg", "os.remove",
    "os.rename", "os.mkdir", "os.rmdir", "os.chmod", "os.chown", "os.truncate", "os.symlink",
    "os.link", "os.putenv", "os.unsetenv", "os.chdir", "os.chroot",
    "os.mkfifo", "os.mknod", "os.utime", "pty.", "urllib.", "webbrowser.", "winreg.",
)  # fmt: skip
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
# Read access that the interpreter and numpy need at runtime.
READ_EXTRA = ("/proc/self/", "/proc/cpuinfo", "/proc/meminfo", "/sys/devices/system/cpu/",
              "/dev/urandom", "/dev/null")  # fmt: skip


class Blocked(ImportError):
    """The program asked for something the sandbox doesn't allow."""


def fail(kind: str, message: str, code: int = 2) -> None:
    sys.stdout.flush()
    sys.stderr.write(f"{MARK}\t{kind}\t{message.replace(chr(10), ' ')[:300]}\n")
    sys.stderr.flush()
    os._exit(code)


def vm_size_bytes() -> int:
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


def apply_limits(memory_mb: int, cpu_s: int) -> None:
    try:
        import resource
    except ImportError:  # non-POSIX: only the parent's wall-clock timeout applies
        return
    mb = 1024 * 1024
    for name, value in (
        ("RLIMIT_CORE", 0),
        ("RLIMIT_CPU", cpu_s),
        ("RLIMIT_NOFILE", 64),
        ("RLIMIT_FSIZE", 1 * mb),
    ):
        try:
            limit = getattr(resource, name)
            resource.setrlimit(limit, (value, value))
        except (AttributeError, ValueError, OSError):
            pass
    try:
        # Headroom on top of what the imports already mapped, so numpy's own mappings don't count.
        cap = vm_size_bytes() + memory_mb * mb
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except (AttributeError, ValueError, OSError):
        pass


def install_audit_hook(tmp_dir: str) -> None:
    roots: list[str] = []
    import sysconfig

    for path in (
        sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix,
        *(sysconfig.get_paths().values()),
    ):  # fmt: skip
        if path:
            roots.append(os.path.realpath(path) + os.sep)
    real = os.path.realpath
    tmp_root = real(tmp_dir) + os.sep

    def readable(path: str) -> bool:
        resolved = real(path)
        if resolved.startswith(tuple(roots)) or resolved.startswith(READ_EXTRA):
            return True
        return resolved.startswith(tmp_root)

    def hook(event: str, args: tuple[object, ...]) -> None:
        if event == "open":
            path, _mode, flags = args[0], args[1], args[2]
            if isinstance(path, int):
                return
            text = os.fsdecode(path) if isinstance(path, (str, bytes)) else ""
            writing = isinstance(flags, int) and bool(flags & WRITE_FLAGS)
            if text and not writing and readable(text):
                return
            raise Blocked("file access")
        if event in ("os.listdir", "os.scandir"):
            # The import machinery lists package directories; nothing else may list anything.
            path = args[0]
            if isinstance(path, (str, bytes)) and readable(os.fsdecode(path)):
                return
            raise Blocked("directory listing")
        if event.startswith(BLOCKED_EVENT_PREFIXES):
            raise Blocked(event.split(".")[0])

    sys.addaudithook(hook)


def main() -> None:
    source_path, memory_mb, cpu_s, mods = (
        sys.argv[1],
        int(sys.argv[2]),
        int(sys.argv[3]),
        sys.argv[4],
    )
    # -I ignores PYTHONIOENCODING; Windows stdout is cp1252 and chokes on Greek letters, symbols.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    tmp_dir = os.path.dirname(source_path)
    with open(source_path, encoding="utf-8") as f:
        source = f.read()

    # Import the allowed modules while open() and friends still work, then lock down.
    sys.dont_write_bytecode = True
    for name in filter(None, mods.split(",")):
        __import__(name)
        if name == "numpy":  # submodules numpy loads lazily, which would hit the import machinery
            for sub in ("random", "linalg", "fft", "polynomial"):
                __import__(f"numpy.{sub}")
    code = compile(source, "<example>", "exec")
    real_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> ModuleType:
        root = name.split(".")[0]
        if level != 0 or root not in ALLOWED:
            raise Blocked(f"The example tried to use `{root}`, which isn't allowed.")
        return real_import(name, globals, locals, fromlist, level)

    safe = {k: v for k, v in vars(builtins).items() if k not in REMOVED}
    safe["__import__"] = guarded_import
    namespace = {"__builtins__": safe, "__name__": "__main__"}

    os.environ.clear()
    apply_limits(memory_mb, cpu_s)
    install_audit_hook(tmp_dir)
    try:
        exec(code, namespace)
    except MemoryError:
        fail("memory", "out of memory")
    except Blocked as exc:
        text = str(exc)
        fail("blocked", text if text.startswith("The example") else "file or process access")
    except SystemExit:
        pass
    except BaseException as exc:
        msg = str(exc).replace(tmp_dir, "<tmp>")[:200]
        fail("error", f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__)
    sys.stdout.flush()


main()
