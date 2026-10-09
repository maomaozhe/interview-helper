"""Prepare an initial user-systemd deployment from an exported live release.

Refuses to overwrite an existing database. Run this with Python 3.12 after
deploy-ssh-runtime.sh; database and search restore happen before app startup.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tarfile
import textwrap


def write_private(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o600)


def environment_file(path, values):
    lines = []
    for key, value in values.items():
        if value is None:
            continue
        value = str(value).lower() if isinstance(value, bool) else str(value)
        if "\n" in value or "\r" in value:
            raise ValueError(f"Multiline environment value: {key}")
        value = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key.upper()}="{value}"')
    write_private(path, "\n".join(lines) + "\n")


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        source.extractall(destination, filter="data")


def main(root, listen, hostname=None, admin_url=None):
    root = root.resolve()
    bind_host, bind_port = listen.rsplit(":", 1)
    if not bind_port.isdigit() or not bind_host:
        raise ValueError("Expected --listen ADDRESS:PORT")
    if " " in str(root):
        raise ValueError("Service directory must not contain spaces")
    if (root / "state/postgres/PG_VERSION").exists():
        raise ValueError("Existing database: refusing initial setup")
    release = root / "release"
    for name in ("app", "pi", "state/postgres", "state/elasticsearch", "state/run", "data", "logs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    extract(release / "app.tar.gz", root / "app")
    extract(release / "pi.tar.gz", root / "pi")
    extract(release / "corpus-and-snapshots.tar.gz", root)
    subprocess.run([str(Path.home() / ".local/bin/uv"), "venv", "--python", "3.12.10", str(root / ".venv")], check=True)
    python = root / ".venv/bin/python"
    site_packages = root / ".venv/lib/python3.12/site-packages"
    if (release / "python-deps.tar.gz").exists():
        extract(release / "python-deps.tar.gz", site_packages)
    else:
        subprocess.run([str(Path.home() / ".local/bin/uv"), "pip", "install", "--python", str(python), "-r", str(release / "requirements.active.txt")], check=True)
    subprocess.run([str(python), "-c", "import fastapi, numpy, psycopg, pydantic, sqlalchemy, uvicorn; print('Python dependency imports ready')"], check=True)

    config = json.loads((release / "runtime.private.json").read_text())
    pg_password = secrets.token_urlsafe(32)
    internal_token = secrets.token_urlsafe(40)
    config.update(database_url=f"postgresql+psycopg://interview:{pg_password}@127.0.0.1:15432/interview_intelligence",
                  elasticsearch_url="http://127.0.0.1:19200", corpus_root=str(root / "md"),
                  snapshot_root=str(root / "data/snapshots"), model_lock_path=str(root / "state/run/model-call.lock"),
                  pi_agent_url="http://127.0.0.1:18787", internal_agent_token=internal_token,
                  app_signing_key=config.get("app_signing_key") or secrets.token_urlsafe(40), api_root_path="", jev_decision_enabled=False,
                  jev_api_key=None, jev_calibration_path=None, jev_base_url="http://127.0.0.1:18788/v1")
    environment_file(root / "app/.env", config)
    environment_file(root / "pi.env", {"internal_agent_token": internal_token,
                     "agent_host_url": "http://127.0.0.1:18082", "host": "127.0.0.1", "port": 18787})
    write_private(root / "pg-password", pg_password + "\n")
    write_private(root / "pgpass", f"127.0.0.1:15432:*:interview:{pg_password}\n")
    pg_bin = root / "runtime/postgresql/bin"
    subprocess.run([str(pg_bin / "initdb"), "-D", str(root / "state/postgres"), "--username=interview",
                    f"--pwfile={root / 'pg-password'}", "--auth-host=scram-sha-256", "--auth-local=scram-sha-256",
                    "--encoding=UTF8", "--locale=C.UTF-8"], check=True)
    (root / "pg-password").unlink()
    with (root / "state/postgres/postgresql.conf").open("a") as target:
        target.write(f"\nlisten_addresses = '127.0.0.1'\nport = 15432\nunix_socket_directories = '{root / 'state/run'}'\n")

    es = root / "runtime/elasticsearch-8.19.0"
    es_config = root / "elasticsearch-config"
    shutil.copytree(es / "config", es_config, dirs_exist_ok=True)
    write_private(es_config / "elasticsearch.yml", textwrap.dedent(f"""\
        cluster.name: interview-intelligence
        node.name: interview-intelligence
        discovery.type: single-node
        network.host: 127.0.0.1
        http.port: 19200
        transport.port: 19300
        xpack.security.enabled: false
        xpack.security.enrollment.enabled: false
        node.store.allow_mmap: false
        path.data: {root / 'state/elasticsearch'}
        path.logs: {root / 'logs/elasticsearch'}
        """))
    write_private(es_config / "jvm.options.d/heap.options", "-Xms512m\n-Xmx512m\n")

    login_user = "interview"
    login_password = secrets.token_urlsafe(24)
    password_hash = subprocess.run(["/usr/local/bin/caddy", "hash-password"], input=login_password + "\n",
                                   text=True, check=True, capture_output=True).stdout.strip()
    write_private(root / "Caddyfile", textwrap.dedent(f"""\
        {{
            admin off
            auto_https off
        }}
        http://:{bind_port} {{
            bind {bind_host}
            @internal path /internal /internal/*
            respond @internal 404
            basic_auth {{
                {login_user} {password_hash}
            }}
            header X-Robots-Tag "noindex, nofollow"
            header Referrer-Policy "no-referrer"
            reverse_proxy 127.0.0.1:18082 {{
                flush_interval -1
            }}
        }}
        """))
    subprocess.run(["/usr/local/bin/caddy", "validate", "--config", str(root / "Caddyfile"), "--adapter", "caddyfile"], check=True)
    write_private(root / "access.private.json", json.dumps({"username": login_user, "password": login_password,
                  "listen": listen, "url": f"https://{hostname}/" if hostname else f"http://{listen}/"}, indent=2) + "\n")

    units = Path.home() / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)

    def unit(name, description, command, *, after="", env="", extra="", pre="", work=None):
        filename = units / f"interview-intelligence-{name}.service"
        if filename.exists():
            raise FileExistsError(filename)
        filename.write_text(textwrap.dedent(f"""\
            [Unit]
            Description=Interview Intelligence {description}
            After=network.target {after}
            StartLimitIntervalSec=0

            [Service]
            Type=simple
            WorkingDirectory={work or root / 'app'}
            Environment=PYTHONPATH={root / 'app'}
            Environment=TZ=Asia/Shanghai
            Environment=PYTHONUNBUFFERED=1
            UMask=0077
            {env}
            {pre}
            ExecStart={command}
            Restart=on-failure
            RestartSec=5
            TimeoutStartSec=180
            TimeoutStopSec=90
            {extra}

            [Install]
            WantedBy=default.target
            """), encoding="utf-8")

    unit("postgres", "PostgreSQL 16.9", f"{pg_bin / 'postgres'} -D {root / 'state/postgres'}", extra="KillSignal=SIGINT")
    unit("elasticsearch", "Elasticsearch 8.19.0", f"{es / 'bin/elasticsearch'}", env=f"Environment=ES_PATH_CONF={es_config}", extra="MemoryMax=2G\nLimitNOFILE=65535")
    dependencies = "interview-intelligence-postgres.service interview-intelligence-elasticsearch.service"
    unit("api", "API", f"{python} -m uvicorn interview_intelligence.api:app --host 127.0.0.1 --port 18082",
         after=dependencies, env=f"EnvironmentFile={root / 'app/.env'}", pre=f"ExecStartPre={python} -m alembic upgrade head")
    unit("pi", "Pi Agent", f"{root / 'runtime/node-v22.23.0-linux-x64/bin/node'} services/pi-agent/server.mjs",
         after="interview-intelligence-api.service", env=f"EnvironmentFile={root / 'pi.env'}", work=root / "pi")
    unit("worker", "ingestion worker", f"{python} -m interview_intelligence.worker", after=dependencies + " interview-intelligence-api.service", env=f"EnvironmentFile={root / 'app/.env'}")
    unit("web", "password protected web proxy", f"/usr/local/bin/caddy run --config {root / 'Caddyfile'} --adapter caddyfile", after="interview-intelligence-api.service interview-intelligence-pi.service",
         env=f"Environment=XDG_DATA_HOME={root / 'state/caddy-data'}\nEnvironment=XDG_CONFIG_HOME={root / 'state/caddy-config'}")
    if hostname:
        if not admin_url:
            raise ValueError("HTTPS hostname requires the existing Caddy admin URL")
        unit("proxy", "HTTPS hostname route", f"{python} {root / 'deploy-ssh-proxy.py'} --hostname {hostname} --admin-url {admin_url}", after="interview-intelligence-web.service")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    print("Services prepared. Restore the database and search index before starting API/Pi/worker/web.")


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-dir", type=Path, default=Path.home() / "services/interview-intelligence")
    parser.add_argument("--listen", default="127.0.0.1:8092")
    parser.add_argument("--public-hostname")
    parser.add_argument("--caddy-admin-url")
    args = parser.parse_args()
    main(args.service_dir, args.listen, args.public_hostname, args.caddy_admin_url)
