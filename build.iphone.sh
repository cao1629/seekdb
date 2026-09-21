#!/usr/bin/env bash
# Copyright (c) 2026 OceanBase.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK=iphoneos
RUST_TARGET=aarch64-apple-ios
BUILD_DIR="$ROOT/build_ios_arm64"
DEPLOYMENT=18.0
JOBS=4
TARGET=oceanbase_static
INITIALIZE=false
CONFIGURE_ONLY=false
DEPS_ONLY=false
DEPS_PREFIX=""
HEADERS_PREFIX=""
CMAKE_ARGS=()

# Print supported options without requiring Xcode or any installed dependencies.
usage() {
  cat <<'EOF'
Usage: ./build.iphone.sh [options] [-- -DCMAKE_OPTION=value ...]

Experimental seekdb iOS ARM64 build. This does not package or sign an iPhone app.

  --simulator             Build for an Apple Silicon iOS simulator
  --init                  Prepare host tools and install the pinned Rust iOS target
  --configure-only        Generate build rules without compiling
  --deps-only             Build pinned iOS dependencies, including ICU and VSAG
  --jobs N                Parallel C/C++ jobs (default: 4)
  --target NAME           CMake target (default: oceanbase_static)
  --deps-prefix PATH      iOS-built dependencies (default: deps/ios/<sdk>/devel)
  --headers-prefix PATH   Optional header-only prefix; libraries still come from --deps-prefix
  --deployment-target V   Minimum iOS version (default: 18.0)
  -h, --help              Show this help

Examples:
  ./build.iphone.sh --init --configure-only
  ./build.iphone.sh --jobs 8
  ./build.iphone.sh --simulator --init --jobs 8

Set DEVELOPER_DIR to a full Xcode Developer directory if needed.
CARGO and RUSTUP may point to existing rustup executables.
Rust caches, build output, and logs default to directories within this checkout.
Host dependency initialization supplies parser tools; its macOS libraries cannot
be linked into iOS. The selected dependency prefix must contain iOS libraries.
EOF
}

# Stop before configuration when a required argument or prerequisite is absent.
fail() {
  printf '[build.iphone.sh] ERROR: %s\n' "$*" >&2
  exit 2
}

# Parse the bounded command-line surface and retain explicit CMake overrides.
parse_args() {
  while (($#)); do
    case "$1" in
      --simulator)
        SDK=iphonesimulator
        RUST_TARGET=aarch64-apple-ios-sim
        BUILD_DIR="$ROOT/build_ios_sim_arm64"
        shift ;;
      --init) INITIALIZE=true; shift ;;
      --configure-only) CONFIGURE_ONLY=true; shift ;;
      --deps-only) DEPS_ONLY=true; shift ;;
      --jobs|--target|--deps-prefix|--headers-prefix|--deployment-target)
        (($# >= 2)) || fail "$1 requires a value"
        case "$1" in
          --jobs) JOBS="$2" ;;
          --target) TARGET="$2" ;;
          --deps-prefix) DEPS_PREFIX="$2" ;;
          --headers-prefix) HEADERS_PREFIX="$2" ;;
          --deployment-target) DEPLOYMENT="$2" ;;
        esac
        shift 2 ;;
      -h|--help) usage; exit 0 ;;
      --) shift; CMAKE_ARGS=("$@"); break ;;
      *) fail "unknown option: $1" ;;
    esac
  done
  [[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || fail '--jobs must be a positive integer'
  [[ "$DEPLOYMENT" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]] || fail 'invalid iOS version'
  [[ "$TARGET" =~ ^[a-zA-Z0-9_.-]+$ ]] || fail 'invalid CMake target'
}

# Install version-pinned host parser generators, retaining downloads inside the checkout.
prepare_host_tools() {
  # Only parser generators run on the host; avoid downloading macOS target libraries.
  [[ "$(uname -m)" == arm64 ]] || fail 'host-tool bootstrap currently requires Apple Silicon'
  local profile="$ROOT/deps/init/oceanbase.macos15.arm64.deps"
  local repository package archive
  repository="$(awk -F= '$1 == "repo" { print $2; exit }' "$profile")"
  mkdir -p "$ROOT/deps/3rd/pkg"
  while IFS= read -r package; do
    archive="$ROOT/deps/3rd/pkg/$package"
    if [[ ! -f "$archive" ]]; then
      curl --fail --location --retry 2 "$repository/$package" --output "$archive.partial"
      mv "$archive.partial" "$archive"
    fi
    tar -xzf "$archive" -C "$ROOT/deps/3rd"
  done < <(grep -E '^obdevtools-(bison|flex)-[^/]+\.tar\.gz$' "$profile")
}

# Locate Apple and Rust tools without changing the system-wide Xcode selection.
prepare_tools() {
  [[ "$(uname -s)" == Darwin ]] || fail 'iOS compilation requires macOS and Xcode'
  if [[ -z "${DEVELOPER_DIR:-}" && -d /Applications/Xcode.app/Contents/Developer ]]; then
    export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
  fi
  xcrun --sdk "$SDK" --show-sdk-path >/dev/null || fail "Xcode SDK missing: $SDK"
  command -v cmake >/dev/null || fail 'cmake is not installed'
  if [[ -d "$ROOT/deps/ios/cargo/bin" ]]; then
    export PATH="$ROOT/deps/ios/cargo/bin:$PATH"
  fi
  CARGO="${CARGO:-$(command -v cargo || true)}"
  RUSTUP="${RUSTUP:-$(command -v rustup || true)}"
  [[ -x "$CARGO" && -x "$RUSTUP" ]] || fail 'set CARGO and RUSTUP to installed Rust executables'
  export CARGO_HOME="${CARGO_HOME:-$ROOT/deps/ios/cargo}"
  export RUSTUP_HOME="${RUSTUP_HOME:-$ROOT/deps/ios/rustup}"
  export PATH="$(dirname "$CARGO"):$(dirname "$RUSTUP"):$PATH"
  export CARGO
  PINNED_RUST="$(sed -n 's/^channel = "\([^"]*\)"/\1/p' "$ROOT/rust/rust-toolchain.toml")"
  [[ -n "$PINNED_RUST" ]] || fail 'cannot read the pinned Rust toolchain'
  if [[ "$INITIALIZE" == true ]]; then
    prepare_host_tools
    "$RUSTUP" toolchain install "$PINNED_RUST" --profile minimal --no-self-update
    "$RUSTUP" target add --toolchain "$PINNED_RUST" "$RUST_TARGET"
  fi
  "$RUSTUP" run "$PINNED_RUST" rustc --version >/dev/null || fail 'pinned rustc is unavailable or could not start; inspect the preceding error'
  "$RUSTUP" target list --toolchain "$PINNED_RUST" --installed |
    grep -qx "$RUST_TARGET" || fail "missing $RUST_TARGET; run with --init"
}

# Configure the engine and optionally compile, preserving failure logs in the build tree.
build_engine() {
  DEPS_PREFIX="${DEPS_PREFIX:-$ROOT/deps/ios/$SDK/devel}"
  mkdir -p "$BUILD_DIR/logs"
  printf '[build.iphone.sh] SDK=%s target=%s dependencies=%s\n' "$SDK" "$RUST_TARGET" "$DEPS_PREFIX"
  cmake -S "$ROOT" -B "$BUILD_DIR" \
    -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_SYSROOT="$SDK" \
    -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET="$DEPLOYMENT" \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo -DOB_USE_LLD=OFF -DOB_DISABLE_PIE=OFF \
    -DCARGO="$CARGO" -DDEP_DIR="$DEPS_PREFIX" \
    -DSEEKDB_IOS_HEADER_PREFIX="${HEADERS_PREFIX:-$DEPS_PREFIX}" \
    ${CMAKE_ARGS[@]+"${CMAKE_ARGS[@]}"} 2>&1 | tee "$BUILD_DIR/logs/configure.log"
  if [[ "$CONFIGURE_ONLY" == false ]]; then
    local free_kib minimum_gib
    minimum_gib="${SEEKDB_IOS_MIN_FREE_GIB:-10}"
    [[ "$minimum_gib" =~ ^[0-9]+$ ]] || fail 'SEEKDB_IOS_MIN_FREE_GIB must be an integer'
    free_kib="$(df -Pk "$ROOT" | awk 'NR == 2 { print $4 }')"
    ((free_kib >= minimum_gib * 1024 * 1024)) ||
      fail "insufficient disk space: keep at least $minimum_gib GiB free before compiling (not a total-build size estimate)"
    cmake --build "$BUILD_DIR" --target "$TARGET" --parallel "$JOBS" \
      2>&1 | tee "$BUILD_DIR/logs/build.log"
  fi
}

parse_args "$@"
if [[ "$DEPS_ONLY" == true ]]; then
  DEPS_ARGS=(--jobs "$JOBS" --deployment-target "$DEPLOYMENT")
  if [[ "$SDK" == iphonesimulator ]]; then
    DEPS_ARGS+=(--simulator)
  fi
  exec python3 "$ROOT/deps/ios-build/build.py" "${DEPS_ARGS[@]}"
fi
prepare_tools
build_engine
