#!/usr/bin/env bash
# Keep the native Linux release build independent of GitHub Release downloads.
set -euo pipefail

target="${1:-x86_64-unknown-linux-gnu}"
output="${2:-dist}"

export PATH="$HOME/.cargo/bin:$PATH"
# Cross images contain target-architecture Pythons under /opt/python.
# Build tooling must run with the host interpreter instead.
if [[ "$target" == "x86_64-unknown-linux-gnu" ]]; then
    export PATH="/opt/python/cp312-cp312/bin:$PATH"
fi
if ! command -v rustup >/dev/null; then
    curl --fail --show-error --silent --location --retry 5 https://sh.rustup.rs -o /tmp/smelt-rustup.sh
    sh /tmp/smelt-rustup.sh -y --profile minimal --default-toolchain stable
fi
rustup update --no-self-update stable
rustup default stable
rustup target add "$target"
python3 -m pip install --disable-pip-version-check "maturin[patchelf]==1.16.0"
maturin build --release --locked --target "$target" \
    --out "$output" --compatibility manylinux_2_28
