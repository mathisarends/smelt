#!/usr/bin/env bash
# Keep the native Linux release build independent of GitHub Release downloads.
set -euo pipefail

export PATH="/opt/python/cp312-cp312/bin:$HOME/.cargo/bin:$PATH"
if ! command -v rustup >/dev/null; then
    curl --fail --show-error --silent --location --retry 5 https://sh.rustup.rs -o /tmp/smelt-rustup.sh
    sh /tmp/smelt-rustup.sh -y --profile minimal --default-toolchain stable
fi
rustup update --no-self-update stable
rustup default stable
python -m pip install --disable-pip-version-check "maturin[patchelf]==1.16.0"
maturin build --release --locked --target x86_64-unknown-linux-gnu \
    --out dist --compatibility manylinux_2_28
