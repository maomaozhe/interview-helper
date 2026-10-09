#!/usr/bin/env bash
# Install the pinned Compose runtimes in a user-owned directory (no sudo).
set -euo pipefail
umask 077

SERVICE_DIR="${SERVICE_DIR:-$HOME/services/interview-intelligence}"
mkdir -p "$SERVICE_DIR/runtime" "$SERVICE_DIR/downloads" "$SERVICE_DIR/logs"
SERVICE_DIR="$(cd "$SERVICE_DIR" && pwd -P)"
export SERVICE_DIR

download() {
    curl --fail --location --silent --show-error --retry 3 \
        --connect-timeout 20 --max-time 1200 "$1" -o "$2"
}

install_postgres() {
    cd "$SERVICE_DIR/downloads"
    if [[ ! -x "$SERVICE_DIR/runtime/postgresql/bin/postgres" ]]; then
        download https://ftp.postgresql.org/pub/source/v16.9/postgresql-16.9.tar.bz2 postgresql-16.9.tar.bz2
        download https://ftp.postgresql.org/pub/source/v16.9/postgresql-16.9.tar.bz2.sha256 postgresql-16.9.tar.bz2.sha256
        sha256sum --check postgresql-16.9.tar.bz2.sha256
        tar -xjf postgresql-16.9.tar.bz2
        cd postgresql-16.9
        ./configure --prefix="$SERVICE_DIR/runtime/postgresql" --without-readline --without-icu
        make -j8
        make install
    fi
    "$SERVICE_DIR/runtime/postgresql/bin/postgres" --version
}

install_elasticsearch() {
    cd "$SERVICE_DIR/downloads"
    if [[ ! -x "$SERVICE_DIR/runtime/elasticsearch-8.19.0/bin/elasticsearch" ]]; then
        download https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-8.19.0-linux-x86_64.tar.gz elasticsearch-8.19.0-linux-x86_64.tar.gz
        download https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-8.19.0-linux-x86_64.tar.gz.sha512 elasticsearch-8.19.0-linux-x86_64.tar.gz.sha512
        sha512sum --check elasticsearch-8.19.0-linux-x86_64.tar.gz.sha512
        tar -xzf elasticsearch-8.19.0-linux-x86_64.tar.gz -C "$SERVICE_DIR/runtime"
    fi
    "$SERVICE_DIR/runtime/elasticsearch-8.19.0/bin/elasticsearch" --version
}

install_python_node() {
    "$HOME/.local/bin/uv" python install 3.12.10
    cd "$SERVICE_DIR/downloads"
    if [[ ! -x "$SERVICE_DIR/runtime/node-v22.23.0-linux-x64/bin/node" ]]; then
        download https://nodejs.org/dist/v22.23.0/node-v22.23.0-linux-x64.tar.xz node-v22.23.0-linux-x64.tar.xz
        download https://nodejs.org/dist/v22.23.0/SHASUMS256.txt node-SHASUMS256.txt
        grep ' node-v22.23.0-linux-x64.tar.xz$' node-SHASUMS256.txt | sha256sum --check -
        tar -xJf node-v22.23.0-linux-x64.tar.xz -C "$SERVICE_DIR/runtime"
    fi
    "$SERVICE_DIR/runtime/node-v22.23.0-linux-x64/bin/node" --version
}

install_postgres >"$SERVICE_DIR/logs/bootstrap-postgres.log" 2>&1 &
postgres_pid=$!
install_elasticsearch >"$SERVICE_DIR/logs/bootstrap-elasticsearch.log" 2>&1 &
elasticsearch_pid=$!
install_python_node >"$SERVICE_DIR/logs/bootstrap-python-node.log" 2>&1 &
python_node_pid=$!
failed=0
for job_pid in "$postgres_pid" "$elasticsearch_pid" "$python_node_pid"; do
    wait "$job_pid" || failed=1
done
if [[ "$failed" != 0 ]]; then
    echo "Runtime setup failed; inspect $SERVICE_DIR/logs/bootstrap-*.log" >&2
    exit 1
fi
printf 'PostgreSQL 16.9, Elasticsearch 8.19.0, Python 3.12.10, Node 22.23.0 ready.\n'
touch "$SERVICE_DIR/runtime/.ready"
