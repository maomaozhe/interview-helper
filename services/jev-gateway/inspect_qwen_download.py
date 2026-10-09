"""Inspect or stop only this service's own download helper, without arguments."""
import argparse
import os
import signal
from pathlib import Path

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--stop",action="store_true"); args=parser.parse_args()
    for folder in Path("/proc").iterdir():
        if not folder.name.isdecimal() or int(folder.name)==os.getpid(): continue
        try:
            words=(folder/"cmdline").read_bytes().split(b"\0")
            if len(words)<2 or not Path(os.fsdecode(words[0])).name.startswith("python"): continue
            entries=[Path(os.fsdecode(word)) for word in words[1:] if word and Path(os.fsdecode(word)).name=="download_qwen.py"]
            if not entries: continue
            script=entries[0]
            absolute=(script if script.is_absolute() else (folder/"cwd").resolve()/script).resolve()
            if absolute!=Path(__file__).resolve().parent/"download_qwen.py": continue
            pid=int(folder.name)
            print({"download_pid":pid,"state":(folder/"status").read_text().split("State:",1)[1].splitlines()[0].strip()})
            if args.stop:
                os.kill(pid,signal.SIGTERM); print({"stopped_own_download_helper":pid})
        except (OSError,IndexError): pass
