#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Build the optional exact-EDT core for macOS and publish it next to the
# Windows DLL inside the extension directory, so native.py can load it on
# any platform.  A universal (arm64 + x86_64) dylib is produced by default;
# pass a single architecture to speed up local iteration, e.g.
#   scripts/build_native_macos.sh arm64
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_file="$root/native/quicksdf_core.cpp"
target="$root/quick_sdf_blender/bin/libquicksdf_core.dylib"

if [[ ! -f "$source_file" ]]; then
    echo "native source was not found: $source_file" >&2
    exit 1
fi

if (($# > 0)); then
    arch_args=()
    for arch in "$@"; do
        arch_args+=(-arch "$arch")
    done
else
    arch_args=(-arch arm64 -arch x86_64)
fi

mkdir -p "$(dirname "$target")"
clang++ -std=c++17 -O2 -fPIC -dynamiclib -fvisibility=hidden \
    -install_name @rpath/libquicksdf_core.dylib \
    -mmacosx-version-min=11.2 \
    "${arch_args[@]}" \
    "$source_file" -o "$target"

echo "Built $target"
lipo -archs "$target" 2>/dev/null || true
