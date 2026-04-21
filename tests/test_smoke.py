import subprocess
import sys

import clipwarden


def test_version_is_set():
    assert isinstance(clipwarden.__version__, str)
    assert clipwarden.__version__.count(".") >= 2


def test_module_runs():
    # `python -m clipwarden --version` is the non-blocking smoke path;
    # the bare invocation starts the watcher and blocks on Ctrl-C.
    result = subprocess.run(
        [sys.executable, "-m", "clipwarden", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert clipwarden.__version__ in result.stdout
    assert "ClipWarden" in result.stdout
