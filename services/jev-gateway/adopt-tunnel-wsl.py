"""Replace only the task's initial forward with the managed control socket."""
from pathlib import Path
import os
import signal
import subprocess

target = b"172.17.0.1:18789:127.0.0.1:18788"
for process in Path("/proc").iterdir():
    if not process.name.isdigit():
        continue
    try:
        args = (process / "cmdline").read_bytes().split(b"\0")
    except (FileNotFoundError, PermissionError):
        continue
    if args and args[0] == b"ssh" and target in args and b"dylan@101.47.18.72" in args and b"-M" not in args:
        os.kill(int(process.name), signal.SIGTERM)
# Only remove empty temporary directories created by this task.
for directory in Path("/tmp").glob("interview-system-one.*"):
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()
subprocess.run(["sh", "services/jev-gateway/start-tunnel-wsl.sh"], check=True)
print("Managed private SSH tunnel ready; temporary key removed")
