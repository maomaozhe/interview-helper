#!/usr/bin/env python3
"""Download verified deployment artifacts locally when remote egress is slow."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def download(url, target, expected, workers, size=None, limit_rate=None):
    if target.exists() and sha256(target) == expected:
        print("Already verified " + target.name, flush=True)
        return
    if size is None:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as response:
            size = int(response.headers["Content-Length"])
    parts = target.with_name(target.name + ".parts")
    parts.mkdir(exist_ok=True)
    chunk = 32 * 1024 * 1024
    count = (size + chunk - 1) // chunk
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is required")
    started = time.monotonic()

    def transfer(index):
        begin, end = index * chunk, min(size, (index + 1) * chunk) - 1
        path = parts / ("part-{:05d}".format(index))
        if not path.exists() or path.stat().st_size != end - begin + 1:
            command = [curl, "--silent", "--show-error", "--fail", "--location", "--retry", "4",
                       "--connect-timeout", "15", "--max-time", "180", "--range", "{}-{}".format(begin, end),
                       "--output", str(path), url]
            if limit_rate:
                command[1:1] = ["--limit-rate", limit_rate]
            for attempt in range(5):
                result = subprocess.run(command)
                if result.returncode == 0:
                    break
                time.sleep(min(2 ** attempt, 16))
            else:
                raise subprocess.CalledProcessError(result.returncode, command)
        if path.stat().st_size != end - begin + 1:
            raise RuntimeError("Range response has wrong size: " + path.name)
        return path

    complete = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(transfer, i) for i in range(count)]
        for future in as_completed(futures):
            future.result()
            complete += 1
            if complete % 8 == 0 or complete == count:
                print("{}: {}/{} chunks ({:.1f}s)".format(target.name, complete, count,
                                                        time.monotonic() - started), flush=True)
    temporary = target.with_name(target.name + ".assembling")
    with temporary.open("wb") as output:
        for index in range(count):
            with (parts / ("part-{:05d}".format(index))).open("rb") as source:
                shutil.copyfileobj(source, output, 8 * 1024 * 1024)
    if temporary.stat().st_size != size or sha256(temporary) != expected:
        raise RuntimeError("Final official SHA-256 mismatch: " + target.name)
    temporary.replace(target)
    print("Verified " + target.name, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("runtime-manifest.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--model-only", action="store_true")
    parser.add_argument("--cuda-only", action="store_true")
    parser.add_argument("--model-url", help="Official mirror URL; the pinned full SHA is still enforced")
    parser.add_argument("--limit-rate", help="Per-worker curl rate cap, e.g. 256K; preserves bandwidth for the web service")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text())
    model = manifest["model"]
    if not args.cuda_only:
        download(args.model_url or "https://huggingface.co/{}/resolve/{}/{}".format(
            model["repository"], model["revision"], model["filename"]),
            args.output_dir / model["filename"], model["sha256"], args.workers, model["size"], args.limit_rate)
    if not args.model_only:
        for package in manifest["cuda_packages"]:
            download(manifest["cuda_repository"] + package["filename"],
                     args.output_dir / package["filename"], package["sha256"], args.workers, limit_rate=args.limit_rate)


if __name__ == "__main__":
    main()
