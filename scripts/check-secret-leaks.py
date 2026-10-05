"""Compare configured secrets with Git tracked and non-ignored workspace files."""
import subprocess
from pathlib import Path
from dotenv import dotenv_values

secrets={k:v for k,v in dotenv_values(".env").items() if v and len(v)>=16 and
    any(part in k.upper() for part in ("KEY","TOKEN","PASSWORD","SECRET"))}
paths=subprocess.check_output(["git","ls-files","-z","--cached","--others","--exclude-standard"]).decode("utf-8").split("\0")
found=[]
for path in set(paths):
    p=Path(path)
    if not p.is_file():continue
    content=p.read_bytes()
    for key,value in secrets.items():
        if value.encode() in content:found.append({"file":path,"setting":key})
if found:
    print(found)
    raise SystemExit("Configured secret found in a non-ignored file")
print(f"Secret leak scan passed: {len(secrets)} configured values, {len(set(paths))} paths")
