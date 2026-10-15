#!/usr/bin/env python3
"""Install an isolated, authenticated loopback Qwen service as the SSH user.

Run with Python 3.10+ on Linux after uploading services/qwen-openai/runtime-manifest.json alongside
this script. NVIDIA packages are extracted below the service directory, never
installed system-wide. The existing application and other models are untouched.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import socket
import subprocess
import tarfile
import time
import urllib.request


def run(args, **kwargs):
    print("Running: " + shlex.join(str(a) for a in args), flush=True)
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def download(url, path, expected):
    if not path.exists() or digest(path) != expected:
        if shutil.which("aria2c"):
            run(["aria2c", "--continue=true", "--allow-overwrite=true", "--auto-file-renaming=false",
                 "--max-connection-per-server=16", "--split=16", "--min-split-size=8M",
                 "--file-allocation=none", "--summary-interval=0", "--console-log-level=warn",
                 "--max-tries=5", "--retry-wait=2", "--check-integrity=true",
                 "--checksum=sha-256=" + expected, "--dir=" + str(path.parent),
                 "--out=" + path.name, url])
        else:
            run(["curl", "--silent", "--show-error", "--fail", "--location",
                 "--retry", "4", "--continue-at", "-", "--output", path, url])
    actual = digest(path)
    if actual != expected:
        raise RuntimeError("Checksum mismatch: " + path.name)
    print("Verified " + path.name, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-dir", type=Path,
                        default=Path.home() / "services/interview-qwen")
    parser.add_argument("--manifest", type=Path,
                        default=Path(__file__).with_name("runtime-manifest.json"))
    parser.add_argument("--port", type=int, default=18792)
    parser.add_argument("--context", type=int, default=24576)
    parser.add_argument("--build-jobs", type=int, default=4)
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--build-only", action="store_true",
                        help="Build the private CUDA runtime while the model downloads separately")
    parser.add_argument("--cublas-root", type=Path,
                        help="Copy this user's existing cuBLAS headers/libraries into the isolated runtime")
    parser.add_argument("--cublas-headers", type=Path,
                        help="Use verified CUDA 12 cuBLAS headers from an operator-created private archive")
    args = parser.parse_args()
    if os.name != "posix" or not 1024 <= args.port <= 65535:
        raise SystemExit("Requires Linux and an unprivileged TCP port")
    root = args.service_dir.expanduser().resolve()
    if root == Path.home() or not root.is_relative_to(Path.home() / "services"):
        raise SystemExit("Service directory must be inside ~/services")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                       "interview-qwen-openai.service"]).returncode == 0:
        raise SystemExit("Stop interview-qwen-openai before rebuilding its runtime")
    manifest = json.loads(args.manifest.read_text())
    runtime = root / "runtime"
    downloads = runtime / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    cuda_root = runtime / "cuda"
    cuda_root.mkdir(exist_ok=True)
    model_dir = root / "models"
    model_dir.mkdir(exist_ok=True)
    model = manifest["model"]
    model_path = model_dir / model["filename"]
    model_url = "https://huggingface.co/{}/resolve/{}/{}".format(
        model["repository"], model["revision"], model["filename"])

    def prepare_cuda(package):
        path = downloads / package["filename"]
        download(manifest["cuda_repository"] + package["filename"], path,
                 package["sha256"])
        # Extracting concurrently would race on shared package directories.
        return path

    pool = ThreadPoolExecutor(max_workers=3)
    model_future = None if args.build_only else pool.submit(download, model_url, model_path, model["sha256"])
    packages = manifest["cuda_packages"]
    if args.cublas_root:
        packages = [p for p in packages if not p["filename"].startswith("libcublas-")]
    package_futures = [pool.submit(prepare_cuda, p) for p in packages]
    for future in package_futures:
        run(["dpkg-deb", "--extract", future.result(), cuda_root])

    source = runtime / "llama.cpp"
    llama = manifest["llama"]
    if not (source / ".git").exists():
        run(["git", "clone", "--depth", "1", "--branch", llama["tag"],
             llama["repository"], source])
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if commit != llama["commit"]:
        raise RuntimeError("Runtime source is not the pinned commit")
    cuda = cuda_root / "usr/local/cuda-12.8"
    cublas_receipt = None
    if args.cublas_root:
        cublas_source = args.cublas_root.expanduser().resolve()
        if not cublas_source.is_relative_to(Path.home()):
            raise SystemExit("Existing cuBLAS must belong to this SSH user's home directory")
        include_dir = cuda / "include"
        library_dir = cuda / "lib64"
        cublas_files = list((cublas_source / "include").glob("cublas*.h"))
        library_files = list((cublas_source / "lib").glob("libcublas*.so.*"))
        if not cublas_files or not library_files:
            raise RuntimeError("Existing cuBLAS headers or libraries are missing")
        copied = []
        for source_file in cublas_files + library_files:
            destination = (include_dir if source_file.suffix == ".h" else library_dir) / source_file.name
            source_sha = digest(source_file)
            if not destination.exists() or digest(destination) != source_sha:
                shutil.copy2(source_file, destination)
            copied.append({"filename": source_file.name, "sha256": source_sha})
        for name in ("libcublas", "libcublasLt"):
            versions = sorted(p.name for p in library_files if p.name.startswith(name + ".so."))
            if not versions:
                raise RuntimeError("Missing cuBLAS runtime library: " + name)
            link = library_dir / (name + ".so")
            if link.is_symlink():
                link.unlink()
            if not link.exists():
                link.symlink_to(versions[0])
        cublas_receipt = {"copied_from": str(cublas_source), "files": copied}
        if args.cublas_headers:
            expected_headers = manifest["cublas12_headers"]["files"]
            with tarfile.open(args.cublas_headers, "r:gz") as archive:
                members = archive.getmembers()
                if {m.name for m in members} != set(expected_headers) or any(not m.isfile() for m in members):
                    raise RuntimeError("Unexpected CUDA 12 cuBLAS header archive members")
                for member in members:
                    content = archive.extractfile(member).read()
                    if hashlib.sha256(content).hexdigest() != expected_headers[member.name]:
                        raise RuntimeError("CUDA 12 cuBLAS header checksum mismatch: " + member.name)
                    (include_dir / member.name).write_bytes(content)
            cublas_receipt["compile_headers"] = manifest["cublas12_headers"]
            cublas_receipt["compile_header_archive_sha256"] = digest(args.cublas_headers)
    env = dict(os.environ, CUDACXX=str(cuda / "bin/nvcc"),
               LD_LIBRARY_PATH=str(cuda / "lib64"),
               PATH=str(cuda / "bin") + ":" + os.environ["PATH"])
    build = runtime / "build"
    run([args.cmake, "-S", source, "-B", build, "-DCMAKE_BUILD_TYPE=Release",
         "-DGGML_CUDA=ON", "-DCMAKE_CUDA_ARCHITECTURES=89", "-DGGML_NATIVE=OFF",
         "-DCUDAToolkit_ROOT=" + str(cuda), "-DLLAMA_OPENSSL=OFF",
         "-DLLAMA_CURL=OFF", "-DLLAMA_BUILD_TESTS=OFF",
         "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_TOOLS=ON"], env=env)
    run([args.cmake, "--build", build, "--parallel", str(args.build_jobs),
         "--target", "llama-server"], env=env)
    run([build / "bin/llama-server", "--version"], env=env)
    if model_future is not None:
        model_future.result()
    pool.shutdown()
    if args.build_only:
        print("Private CUDA runtime prepared; model service has not been started", flush=True)
        return
    key_path = root / "api-keys.txt"
    if not key_path.exists():
        key_path.write_text("qwen_" + secrets.token_urlsafe(32) + "\n")
    key_path.chmod(0o600)
    api_key = key_path.read_text().strip()
    if not api_key or any(c.isspace() for c in api_key):
        raise RuntimeError("Expected exactly one API key in the private key file")
    client_config = root / "provider.private.json"
    client_config.write_text(json.dumps({
        "base_url": "http://127.0.0.1:{}/v1".format(args.port),
        "model": "qwen3-8b-interview", "api_key": api_key,
        "context_tokens": args.context,
    }, indent=2) + "\n")
    client_config.chmod(0o600)
    launcher = root / "start.sh"
    command = [build / "bin/llama-server", "--model", model_path,
               "--alias", "qwen3-8b-interview", "--host", "127.0.0.1",
               "--port", str(args.port), "--ctx-size", str(args.context),
               "--parallel", "1", "--n-gpu-layers", "99", "--flash-attn", "on",
               "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--jinja",
               "--chat-template-kwargs", '{"enable_thinking":false}',
               "--temp", "0.7", "--top-p", "0.8", "--top-k", "20", "--min-p", "0",
               "--threads", "4", "--threads-batch", "4", "--batch-size", "512",
               "--ubatch-size", "256", "--api-key-file", key_path,
               "--no-webui", "--no-slots"]
    launcher.write_text("#!/bin/sh\nset -eu\nexport LD_LIBRARY_PATH={}\nexec {}\n".format(
        shlex.quote(str(cuda / "lib64") + ":" + str(build / "bin")),
        shlex.join(str(a) for a in command)))
    launcher.chmod(0o700)
    unit_path = Path.home() / ".config/systemd/user/interview-qwen-openai.service"
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text("""[Unit]
Description=Private OpenAI-compatible Qwen3 8B interview model
After=network.target

[Service]
Type=simple
WorkingDirectory={root}
ExecStart={launcher}
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=yes
MemoryMax=12G
CPUQuota=400%
TimeoutStopSec=90
KillSignal=SIGINT

[Install]
WantedBy=default.target
""".format(root=root, launcher=launcher))
    receipt = {"model": model, "llama": llama,
               "base_url": "http://127.0.0.1:{}/v1".format(args.port),
               "alias": "qwen3-8b-interview", "context_tokens": args.context,
               "parallel_slots": 1, "kv_cache_type": "q8_0",
               "unit": unit_path.name, "client_config": str(client_config)}
    if cublas_receipt:
        receipt["cublas_runtime"] = cublas_receipt
    (root / "deployment-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    run(["systemctl", "--user", "daemon-reload"])
    if not args.prepare_only:
        active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit_path.name]).returncode == 0
        if not active:
            with socket.socket() as check:
                check.bind(("127.0.0.1", args.port))
        run(["systemctl", "--user", "enable", "--now", unit_path.name])
        for _ in range(120):
            try:
                with urllib.request.urlopen(receipt["base_url"].removesuffix("/v1") + "/health", timeout=5) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(2)
        else:
            raise RuntimeError("Qwen did not become healthy; inspect its user journal")
    print("Private client configuration: " + str(client_config), flush=True)


if __name__ == "__main__":
    main()
