# 结构: vibe-scripts/micro; plaintext pythonw launcher -> encrypted CLI through whitelisted Python.
# 用途: Start the internal sweep without a console and propagate the child exit code.
# 用法: pythonw.exe run_hidden.pyw run --no-judge --json
# 原始需求: Stable plaintext .pyw entry; fixed absolute whitelisted Python, no hidden enablement.
import os
import subprocess
import sys
from pathlib import Path

PYTHON = Path("C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe")


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parent
    try:
        result = subprocess.run([str(PYTHON), "-B", str(root / "scripts" / "pstate.py"),
                                 "sweep", *(arguments or ["run"])],
                                cwd=root, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", creationflags=0x08000000,
                                env={**os.environ, "PSTATE_SWEEP_HIDDEN_LAUNCH": "1"})
        for stream, text in ((sys.stdout, result.stdout), (sys.stderr, result.stderr)):
            if stream is not None:
                stream.write(text)
        return result.returncode
    except OSError as exc:
        if sys.stderr is not None:
            sys.stderr.write(str(exc) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
