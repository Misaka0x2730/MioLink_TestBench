#!/usr/bin/env bash
#
# provision_ghrunner.sh — prepare a fresh DietPi (Debian-based) Raspberry Pi
# to act as the MioLink HIL-bench self-hosted GitHub Actions runner.
#
# The bench runner only FLASHES and TESTS hardware (it never compiles the
# probe/target firmware — GitHub-hosted runners do that). So this script
# installs exactly what scripts/bench/run_bench.py needs at job time:
#
#   * arm-none-eabi-gdb   — flashes the test targets over the BMP GDB/MI server
#                           (from the ARM GNU Toolchain you point --toolchain-url at)
#   * picotool (>= 2.0)   — acknowledged flashing of RP2 probes in BOOTSEL,
#                           incl. RP2350 / Pico 2W (built from source)
#   * dfu-util            — detaches a running probe into BOOTSEL
#   * python3 + libusb    — pyusb / pygdbmi / pyserial / PyYAML
#   * udev rules + groups — raw-USB + /dev/ttyACM* access for an unprivileged
#                           service user (no root needed at job time)
#
# It then registers the GitHub Actions runner under a dedicated 'ghrunner'
# user and installs it as an auto-starting systemd service.
#
# Run on the Pi as root (the script re-execs itself with sudo if needed):
#
#   sudo ./provision_ghrunner.sh \
#       --toolchain-url <ARM_GNU_TOOLCHAIN_TARBALL_URL> \
#       --token         <GITHUB_RUNNER_REGISTRATION_TOKEN> \
#       --labels        <RUNNER_LABEL[,LABEL...]> \
#       --url           <https://github.com/OWNER/REPO>
#
# All four arguments above are mandatory. See --help for the optional ones.

set -euo pipefail

# ── Re-exec as root ──────────────────────────────────────────────────
# apt, useradd, udev, and systemd all need root. Re-run under sudo so the
# caller can invoke the script as a normal user.
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "provision_ghrunner: elevating with sudo..." >&2
    exec sudo -E -- bash "$0" "$@"
fi

# ── Logging helpers ──────────────────────────────────────────────────
_c_blue=$'\033[1;34m'; _c_yellow=$'\033[1;33m'; _c_red=$'\033[1;31m'; _c_reset=$'\033[0m'
log()  { printf '%s==>%s %s\n' "$_c_blue"   "$_c_reset" "$*"; }
warn() { printf '%sWARN:%s %s\n' "$_c_yellow" "$_c_reset" "$*" >&2; }
die()  { printf '%sERROR:%s %s\n' "$_c_red" "$_c_reset" "$*" >&2; exit 1; }

# ── Defaults ─────────────────────────────────────────────────────────
TOOLCHAIN_URL=""
RUNNER_TOKEN=""
RUNNER_LABELS=""
RUNNER_URL=""

RUNNER_USER="ghrunner"
RUNNER_NAME=""                       # default: <hostname>-bench
RUNNER_WORK="_work"
RUNNER_VERSION=""                    # default: latest release from GitHub API
RUNNER_GROUP=""                      # only valid for org/enterprise runners
RUNNER_ARCH=""                       # default: auto-detected

TOOLCHAIN_PREFIX="/opt/arm-gnu-toolchain"
PICO_SDK_DIR="/opt/pico-sdk"
PICOTOOL_SRC="/opt/picotool"
PICOTOOL_REF=""                      # empty -> default branch (tracks latest, RP2350-capable)
PICO_SDK_REF=""                      # empty -> default branch

usage() {
    sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'
    cat <<'EOF'

Mandatory arguments:
  --toolchain-url URL   ARM GNU Toolchain tarball URL (.tar.xz/.tar.gz) for the
                        Pi's host architecture; provides arm-none-eabi-gdb.
  --token TOKEN         GitHub Actions runner registration token.
  --labels LABELS       Runner label(s), comma-separated (e.g. miolink-bench).
  --url URL             GitHub repo/org URL to register against
                        (e.g. https://github.com/Misaka0x2730/MioLink_TestSetup).

Optional arguments:
  --user NAME           Service user to create/use (default: ghrunner).
  --name NAME           Runner name shown in GitHub (default: <hostname>-bench).
  --work DIR            Runner work directory (default: _work).
  --runner-version VER  actions/runner version, e.g. 2.319.1 (default: latest).
  --runner-group NAME   Runner group (org/enterprise runners only; omitted otherwise).
  --runner-arch ARCH    Override runner arch: arm64 | arm (default: auto-detect).
  --toolchain-prefix D  Install prefix for the ARM toolchain (default: /opt/arm-gnu-toolchain).
  --picotool-ref REF    picotool git ref/tag to build (default: repo default branch).
  --pico-sdk-ref REF    pico-sdk git ref/tag for the picotool build (default: default branch).
  -h, --help            Show this help and exit.

Notes:
  * Use a 64-bit (arm64) DietPi image. The official actions/runner is effectively
    unsupported on 32-bit armhf (its bundled .NET will not start there).
  * The arm-none-eabi-gdb inside --toolchain-url must be runnable on THIS Pi
    (matching host arch and satisfiable shared libs, e.g. libpython/libncurses);
    run_bench.py uses it to flash the test targets. The script verifies this and
    attempts to install the missing libraries, but a wrong-arch toolchain cannot
    be fixed here.
EOF
}

# ── Argument parsing ─────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --toolchain-url)    TOOLCHAIN_URL="${2:?missing value for $1}"; shift 2 ;;
        --token)            RUNNER_TOKEN="${2:?missing value for $1}"; shift 2 ;;
        --labels)           RUNNER_LABELS="${2:?missing value for $1}"; shift 2 ;;
        --url)              RUNNER_URL="${2:?missing value for $1}"; shift 2 ;;
        --user)             RUNNER_USER="${2:?missing value for $1}"; shift 2 ;;
        --name)             RUNNER_NAME="${2:?missing value for $1}"; shift 2 ;;
        --work)             RUNNER_WORK="${2:?missing value for $1}"; shift 2 ;;
        --runner-version)   RUNNER_VERSION="${2:?missing value for $1}"; shift 2 ;;
        --runner-group)     RUNNER_GROUP="${2:?missing value for $1}"; shift 2 ;;
        --runner-arch)      RUNNER_ARCH="${2:?missing value for $1}"; shift 2 ;;
        --toolchain-prefix) TOOLCHAIN_PREFIX="${2:?missing value for $1}"; shift 2 ;;
        --picotool-ref)     PICOTOOL_REF="${2:?missing value for $1}"; shift 2 ;;
        --pico-sdk-ref)     PICO_SDK_REF="${2:?missing value for $1}"; shift 2 ;;
        -h|--help)          usage; exit 0 ;;
        *) die "unknown argument: $1 (run '$0 --help')" ;;
    esac
done

[[ -n "$TOOLCHAIN_URL" ]] || die "--toolchain-url is required"
[[ -n "$RUNNER_TOKEN"  ]] || die "--token is required"
[[ -n "$RUNNER_LABELS" ]] || die "--labels is required"
[[ -n "$RUNNER_URL"    ]] || die "--url is required"

[[ -n "$RUNNER_NAME" ]] || RUNNER_NAME="$(hostname --short 2>/dev/null || hostname)-bench"

# ── Host architecture → GitHub runner arch ───────────────────────────
if [[ -z "$RUNNER_ARCH" ]]; then
    deb_arch="$(dpkg --print-architecture 2>/dev/null || true)"
    case "$deb_arch" in
        arm64) RUNNER_ARCH="arm64" ;;
        armhf) RUNNER_ARCH="arm" ;;
        *)
            case "$(uname -m)" in
                aarch64|arm64)      RUNNER_ARCH="arm64" ;;
                armv7l|armv6l|arm)  RUNNER_ARCH="arm" ;;
                *) die "cannot auto-detect runner arch (dpkg='$deb_arch', uname='$(uname -m)'); pass --runner-arch" ;;
            esac
            ;;
    esac
fi
log "Host runner architecture: $RUNNER_ARCH"
if [[ "$RUNNER_ARCH" == "arm" ]]; then
    warn "32-bit ARM (armhf) detected: the official actions/runner has no working .NET"
    warn "on armhf and will most likely fail to start. Use a 64-bit (arm64) DietPi image."
fi

export DEBIAN_FRONTEND=noninteractive

# ── 1. Base packages ─────────────────────────────────────────────────
install_packages() {
    log "Installing base packages (apt)"
    apt-get update -y
    apt-get install -y --no-install-recommends \
        ca-certificates curl wget git tar xz-utils unzip sudo usbutils \
        build-essential cmake pkg-config \
        libusb-1.0-0 libusb-1.0-0-dev dfu-util \
        python3 python3-pip python3-venv \
        libncursesw6
}

# ── 2. Service user + hardware-access groups ─────────────────────────
create_user() {
    getent group plugdev >/dev/null 2>&1 || groupadd plugdev
    getent group dialout >/dev/null 2>&1 || groupadd dialout
    if ! id -u "$RUNNER_USER" >/dev/null 2>&1; then
        log "Creating service user '$RUNNER_USER'"
        useradd --create-home --shell /bin/bash "$RUNNER_USER"
    else
        log "Service user '$RUNNER_USER' already exists"
    fi
    # dialout -> /dev/ttyACM* (BMP GDB/UART CDC); plugdev -> raw USB via udev.
    usermod -aG dialout,plugdev "$RUNNER_USER"
}

# ── 3. ARM GNU Toolchain (arm-none-eabi-gdb) ─────────────────────────
install_toolchain() {
    log "Installing ARM GNU Toolchain from: $TOOLCHAIN_URL"
    local tmp
    tmp="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '$tmp'" RETURN

    curl -fL --retry 3 -o "$tmp/toolchain.tar" "$TOOLCHAIN_URL" \
        || die "failed to download toolchain from $TOOLCHAIN_URL"
    # GNU tar auto-detects xz/gz/bz2 compression.
    tar -xf "$tmp/toolchain.tar" -C "$tmp" || die "failed to extract toolchain tarball"

    local gcc_bin toolchain_root
    # -print -quit stops at the first match itself (no 'find | head' SIGPIPE
    # that would trip pipefail/set -e).
    gcc_bin="$(find "$tmp" -maxdepth 3 -type f -name 'arm-none-eabi-gcc' -print -quit)"
    [[ -n "$gcc_bin" ]] || die "arm-none-eabi-gcc not found inside the downloaded tarball"
    toolchain_root="$(dirname "$(dirname "$gcc_bin")")"

    rm -rf "$TOOLCHAIN_PREFIX"
    mkdir -p "$(dirname "$TOOLCHAIN_PREFIX")"
    mv "$toolchain_root" "$TOOLCHAIN_PREFIX"

    # Symlink every cross tool into /usr/local/bin so it is on PATH for both
    # interactive shells and the systemd runner service.
    local f
    for f in "$TOOLCHAIN_PREFIX"/bin/*; do
        [[ -f "$f" ]] && ln -sf "$f" "/usr/local/bin/$(basename "$f")"
    done
    cat > /etc/profile.d/arm-gnu-toolchain.sh <<EOF
export PATH="$TOOLCHAIN_PREFIX/bin:\$PATH"
EOF

    ensure_gdb_runtime
}

# arm-none-eabi-gdb from the ARM tarball is dynamically linked; on a minimal
# Debian it can want legacy libncurses/libtinfo .so.5 and/or a specific
# libpythonX.Y (13.x gdb is usually Python-enabled). Verify it runs and try to
# satisfy those runtime libraries before giving up.
ensure_gdb_runtime() {
    local gdb="$TOOLCHAIN_PREFIX/bin/arm-none-eabi-gdb"
    if "$gdb" --version >/dev/null 2>&1; then
        log "arm-none-eabi-gdb runs OK"
        return 0
    fi
    warn "arm-none-eabi-gdb did not start; attempting to satisfy its runtime libraries"

    # (a) legacy libncurses/libtinfo .so.5 -> .so.6 compatibility shims.
    local base so6
    for base in libncursesw libtinfo libncurses; do
        # '|| true': awk exits at the first hit, so ldconfig may take SIGPIPE,
        # which pipefail/set -e would otherwise treat as a fatal failure.
        so6="$(ldconfig -p | awk -v b="${base}.so.6" '$1==b {print $NF; exit}' || true)"
        if [[ -n "$so6" && -e "$so6" ]]; then
            ln -sf "$so6" "$(dirname "$so6")/${base}.so.5"
        fi
    done
    ldconfig

    # (b) a specific libpythonX.Y the gdb was linked against, if reported
    #     missing. Best-effort apt of the matching minor version.
    local pylib pkg
    while read -r pylib; do
        [[ -n "$pylib" ]] || continue
        pkg="${pylib%.so*}"        # libpython3.11.so.1.0 -> libpython3.11
        log "arm-none-eabi-gdb needs $pylib; trying apt-get install $pkg"
        apt-get install -y "$pkg" || warn "could not install $pkg from the configured apt repos"
    done < <(ldd "$gdb" 2>/dev/null | awk '/libpython.*not found/ {print $1}' | sort -u)
    ldconfig

    if "$gdb" --version >/dev/null 2>&1; then
        log "arm-none-eabi-gdb runs OK after installing runtime dependencies"
        return 0
    fi
    warn "arm-none-eabi-gdb still fails to start. Dependency report:"
    ldd "$gdb" 2>&1 | sed 's/^/    /' >&2 || true
    warn "The --toolchain-url toolchain must be built for this Pi's architecture and its"
    warn "gdb runnable here; install any remaining libs (e.g. a matching libpython/libncurses)."
}

# ── 4. picotool (>= 2.0, built from source for RP2350 support) ───────
build_picotool() {
    log "Building picotool from source (RP2350/Pico 2W support)"
    rm -rf "$PICO_SDK_DIR" "$PICOTOOL_SRC"

    local sdk_args=(--depth 1) ptool_args=(--depth 1)
    [[ -n "$PICO_SDK_REF" ]] && sdk_args+=(--branch "$PICO_SDK_REF")
    [[ -n "$PICOTOOL_REF" ]] && ptool_args+=(--branch "$PICOTOOL_REF")

    # picotool needs the SDK tree (pico_usb_reset_interface headers, version
    # info) but none of its submodules.
    git clone "${sdk_args[@]}" https://github.com/raspberrypi/pico-sdk "$PICO_SDK_DIR"
    git clone "${ptool_args[@]}" https://github.com/raspberrypi/picotool "$PICOTOOL_SRC"

    cmake -S "$PICOTOOL_SRC" -B "$PICOTOOL_SRC/build" \
        -DCMAKE_BUILD_TYPE=Release \
        -DPICO_SDK_PATH="$PICO_SDK_DIR"
    cmake --build "$PICOTOOL_SRC/build" -j"$(nproc)"
    cmake --install "$PICOTOOL_SRC/build"      # -> /usr/local/bin/picotool
    ldconfig

    # Ship picotool's udev rules if the checkout provides them.
    if [[ -f "$PICOTOOL_SRC/udev/99-picotool.rules" ]]; then
        install -m 0644 "$PICOTOOL_SRC/udev/99-picotool.rules" /etc/udev/rules.d/99-picotool.rules
    fi

    command -v picotool >/dev/null 2>&1 || warn "picotool not on PATH after install"
}

# ── 5. udev rules for unprivileged hardware access ───────────────────
install_udev_rules() {
    log "Installing udev rules for probe USB access (group plugdev / dialout)"
    cat > /etc/udev/rules.d/60-miolink-bench.rules <<'EOF'
# MioLink HIL bench — unprivileged USB access for the ghrunner service user.
#
# Black Magic Probe / MioLink runtime (raw USB for dfu-util + pyusb enumeration).
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="6018", MODE="0660", GROUP="plugdev", TAG+="uaccess"
# RP2 ROM bootloader (BOOTSEL) for picotool: 0003 = RP2040, 000f = RP2350.
SUBSYSTEM=="usb", ATTRS{idVendor}=="2e8a", ATTRS{idProduct}=="0003", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTRS{idVendor}=="2e8a", ATTRS{idProduct}=="000f", MODE="0660", GROUP="plugdev", TAG+="uaccess"
# CDC-ACM serial ports exposed by the probe (BMP GDB server + target UART bridge).
SUBSYSTEM=="tty", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="6018", MODE="0660", GROUP="dialout"
EOF
    udevadm control --reload-rules || warn "udevadm reload failed (no effect until next plug/boot)"
    udevadm trigger || true
}

# ── 6. Python runtime deps (pre-warm; the workflow reinstalls per job) ─
install_python_deps() {
    log "Pre-installing Python runtime dependencies (as $RUNNER_USER)"
    local home_dir
    home_dir="$(getent passwd "$RUNNER_USER" | cut -d: -f6)"
    # Mirrors scripts/requirements.txt (libusb-package is win32-only, skipped).
    # Installed into the user site as $RUNNER_USER so it matches what the
    # workflow's unprivileged per-job 'pip install' resolves at run time.
    runuser -u "$RUNNER_USER" -- env HOME="$home_dir" \
        pip3 install --user --break-system-packages --upgrade \
        "pyusb>=1.2.1" "pygdbmi>=0.11.0.0" "pyserial>=3.5" "PyYAML>=6.0" \
        || warn "pip pre-install failed; the workflow installs these at job time anyway"
}

# ── 7. GitHub Actions runner: download, register, install as service ─
install_runner() {
    local home_dir runner_dir
    home_dir="$(getent passwd "$RUNNER_USER" | cut -d: -f6)"
    [[ -n "$home_dir" ]] || die "cannot resolve home directory for '$RUNNER_USER'"
    runner_dir="$home_dir/actions-runner"

    if [[ -z "$RUNNER_VERSION" ]]; then
        log "Resolving latest actions/runner release"
        # '|| true': head closes the pipe early, so grep may take SIGPIPE,
        # which pipefail/set -e would otherwise treat as a fatal failure.
        RUNNER_VERSION="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest \
            | grep -oP '"tag_name":\s*"v\K[^"]+' | head -1 || true)"
        [[ -n "$RUNNER_VERSION" ]] || die "could not determine latest runner version; pass --runner-version"
    fi
    log "Using actions/runner v$RUNNER_VERSION ($RUNNER_ARCH)"

    # Tear down a previously-installed service so re-runs are idempotent.
    if [[ -f "$runner_dir/.service" ]]; then
        log "Removing existing runner service"
        ( cd "$runner_dir" && ./svc.sh stop || true; ./svc.sh uninstall || true )
    fi

    mkdir -p "$runner_dir"
    local tgz="actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
    log "Downloading $tgz"
    curl -fL --retry 3 -o "$runner_dir/$tgz" \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${tgz}" \
        || die "failed to download the runner tarball"
    tar -xzf "$runner_dir/$tgz" -C "$runner_dir"
    rm -f "$runner_dir/$tgz"
    chown -R "$RUNNER_USER":"$RUNNER_USER" "$runner_dir"

    # .NET / libicu and friends (apt-based; must run as root).
    if [[ -x "$runner_dir/bin/installdependencies.sh" ]]; then
        log "Installing runner .NET dependencies"
        ( cd "$runner_dir" && ./bin/installdependencies.sh )
    fi

    # Register the runner as the unprivileged user (config.sh refuses root).
    # runuser -u does NOT reset the environment, and we were re-exec'd via
    # 'sudo -E', so HOME still points at the caller's/root's home. Force HOME
    # to the service user's home or config.sh's .NET runtime writes to the
    # wrong $HOME/.dotnet and fails with an access error.
    log "Registering runner '$RUNNER_NAME' at $RUNNER_URL"
    runuser -u "$RUNNER_USER" -- env \
        HOME="$home_dir" USER="$RUNNER_USER" LOGNAME="$RUNNER_USER" \
        RUNNER_CFG_DIR="$runner_dir" \
        RUNNER_CFG_URL="$RUNNER_URL" \
        RUNNER_CFG_TOKEN="$RUNNER_TOKEN" \
        RUNNER_CFG_NAME="$RUNNER_NAME" \
        RUNNER_CFG_LABELS="$RUNNER_LABELS" \
        RUNNER_CFG_WORK="$RUNNER_WORK" \
        RUNNER_CFG_GROUP="$RUNNER_GROUP" \
        bash -c '
            set -e
            cd "$RUNNER_CFG_DIR"
            # Drop any stale local registration so --replace can re-claim the name.
            rm -f .runner .credentials .credentials_rsaparams
            args=(--unattended --replace
                  --url "$RUNNER_CFG_URL"
                  --token "$RUNNER_CFG_TOKEN"
                  --name "$RUNNER_CFG_NAME"
                  --labels "$RUNNER_CFG_LABELS"
                  --work "$RUNNER_CFG_WORK")
            [ -n "$RUNNER_CFG_GROUP" ] && args+=(--runnergroup "$RUNNER_CFG_GROUP")
            ./config.sh "${args[@]}"
        ' || die "runner registration failed"

    # Install + start the systemd service (auto-starts on boot).
    log "Installing and starting the runner systemd service"
    ( cd "$runner_dir" && ./svc.sh install "$RUNNER_USER" && ./svc.sh start )

    RUNNER_DIR="$runner_dir"   # for the summary
}

# ── 8. Summary / verification ────────────────────────────────────────
summary() {
    log "Verification"
    printf '    arm-none-eabi-gdb : %s\n' "$("$TOOLCHAIN_PREFIX/bin/arm-none-eabi-gdb" --version 2>/dev/null | head -1 || echo 'FAILED')"
    printf '    picotool          : %s\n' "$(picotool version 2>/dev/null | head -1 || echo 'FAILED')"
    printf '    dfu-util          : %s\n' "$(dfu-util --version 2>/dev/null | head -1 || echo 'FAILED')"
    printf '    python3           : %s\n' "$(python3 --version 2>&1 || echo 'FAILED')"
    echo
    log "Runner service status"
    ( cd "$RUNNER_DIR" && ./svc.sh status ) || true
    echo
    log "Done. Runner '$RUNNER_NAME' registered at $RUNNER_URL with labels '$RUNNER_LABELS'."
    warn "Group membership (dialout/plugdev) for '$RUNNER_USER' takes full effect after a reboot;"
    warn "reboot the Pi now so the runner service picks up USB access cleanly."
}

main() {
    install_packages
    create_user
    install_toolchain
    build_picotool
    install_udev_rules
    install_python_deps
    install_runner
    summary
}

main "$@"
