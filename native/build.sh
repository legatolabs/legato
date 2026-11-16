#!/usr/bin/env bash
# Build the native helper for both sandbox CPUs into legato/bin, where release wheels pick it up.
#   rustup target add x86_64-unknown-linux-musl aarch64-unknown-linux-musl   # once
set -euo pipefail
cd "$(dirname "$0")/legato-step"
out=../../legato/bin
mkdir -p "$out"
for arch in x86_64 aarch64; do
  cargo build --release --locked --target "$arch-unknown-linux-musl"
  cp "target/$arch-unknown-linux-musl/release/legato-step" "$out/legato-step-$arch"
done
ls -l "$out"
