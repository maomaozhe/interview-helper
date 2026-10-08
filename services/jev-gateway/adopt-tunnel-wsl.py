"""Replace only the task's initial forward with the managed control socket."""
from pathlib import Path
import os
import signal
import subprocess

ssh_target = os.environ.get("SSH_TARGET")
if not ssh_target:
    raise SystemExit("Set SSH_TARGET to your SSH alias or user@host")
ssh_target_bytes = os.fsencode(ssh_target)
target = b"172.17.0.1:18789:127.0.0.1:18788"
for process in Path("/proc").iterdir():
    if not process.name.isdigit():
        continue
    try:
        args = (process / "cmdline").read_bytes().split(b"\0")
    except (FileNotFoundError, PermissionError):
        continue
    if args and args[0] == b"ssh" and target in args and ssh_target_bytes in args and b"-M" not in args:
        os.kill(int(process.name), signal.SIGTERM)
# Only remove empty temporary directories created by this task.
for directory in Path("/tmp").glob("interview-system-one.*"):
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()
subprocess.run(["sh", "services/jev-gateway/start-tunnel-wsl.sh"], check=True)
print("Managed private SSH tunnel ready; temporary key removed")
