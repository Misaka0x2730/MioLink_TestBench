# MioLink TestBench

A hardware-in-the-loop bench for automated testing of the
[MioLink](https://github.com/Misaka0x2730/MioLink) debug probe.

The bench builds probe firmware and target test firmware, flashes both onto
real hardware, and runs a suite of functional tests over the physically wired
interfaces (SWD/JTAG, UART, RTT, SWO).

## How it works

A full run consists of three phases orchestrated by [`bench_run_all.sh`](scripts/bench_run_all.sh):

1. **Build probe firmware** ([`miolink_build_all.sh`](scripts/miolink_build_all.sh)) — builds
   every MioLink variant listed in the bench config and stages the `.uf2` images.
2. **Build target firmware** ([`targets_build_all.sh`](scripts/targets_build_all.sh)) —
   builds the `test_board` fixtures for each target platform and CLI backend.
3. **Run tests** ([`bench_run_tests_only.sh`](scripts/bench_run_tests_only.sh)) — flashes the
   probes and targets, verifies probe identity, and runs the tests.

Each script can also be run on its own if you only need a single phase.

## Bench configuration

The physical layout is described by a YAML file under [`config/`](config/): a
flat list of slots where each probe ↔ target pair carries everything needed —
probe serial number, target model, platform, wired interfaces, Vtref mode, SWO
presence, the set of CLI backends, and so on. Every field is documented inline
in the file.

A bench is fully described by its config, so you can keep one per physical setup
and select it with `--config` (the scripts default to
[`config/bench_pizero2w.yaml`](config/bench_pizero2w.yaml)).

## Repository layout

| Directory | Purpose |
|---|---|
| [`firmware/`](firmware/) | Test target firmware (`test_board`); platforms: `stm32f030`, `stm32f103`, `stm32f401`, `hc32f460` |
| [`scripts/cmake/`](scripts/cmake/) | Project-agnostic CMake build driver |
| [`scripts/miolink/`](scripts/miolink/) | Build, flash, and discovery of the MioLink probe (DFU detach / picotool) |
| [`scripts/usbutil/`](scripts/usbutil/) | Generic USB device lookup by VID/PID/serial |
| [`scripts/targets/`](scripts/targets/) | Target firmware builds and target control over GDB/BMP (`gdb`) |
| [`scripts/bench/`](scripts/bench/) | Bench runner and config parser |
| [`scripts/tests/`](scripts/tests/) | Functional tests: `uart`, `rtt`, `swo`, `buffer` |
| [`scripts/runner/`](scripts/runner/) | Self-hosted CI runner provisioning |
| `scripts/*.sh` | Entry-point wrappers from `scripts/`: `bench_run_all`, `bench_run_tests_only`, `miolink_build_all`, `targets_build_all` |
| [`hardware/`](hardware/) | KiCad adapter boards for WeAct Core Boards (STM32F1/F4, MSPM0G3507, STM32G0/C0) |
| [`config/`](config/) | Bench description YAMLs |

## Target platforms

The `test_board` firmware is built for several MCUs that cover different
Cortex-M cores: STM32F030 (CM0), STM32F103 (CM3), STM32F401 (CM4),
HC32F460 (CM4). Variants differ by CLI backend (UART / RTT / disabled),
SWO presence, and build type (Debug / Release).

## Getting started

### Prerequisites

- **ARM GNU Toolchain, CMake, Ninja** — for building the firmware (not needed on
  a bench host that only flashes and tests).
- **Python 3** with the packages from
  [`scripts/requirements.txt`](scripts/requirements.txt) (`pyusb`, `pygdbmi`,
  `pyserial`, `PyYAML`).
- **Git submodules** from [`.gitmodules`](.gitmodules) (CMSIS, STM32 HAL drivers,
  SEGGER RTT, embedded-cli) — pulled in by the clone step below.

### Setup

Clone the repository with its submodules:

```bash
git clone --recurse-submodules https://github.com/Misaka0x2730/MioLink_TestBench.git
cd MioLink_TestBench
```

If you already cloned without `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

Install the Python dependencies:

```bash
pip install -r scripts/requirements.txt
```

## Usage

Run scripts from the repository root.

**Full run** — build probe firmware, build target firmware, flash, and test:

```bash
./scripts/bench_run_all.sh --miolink-firmware-root /path/to/MioLink/firmware
```

**Build only** — skip the flash-and-test phase:

```bash
./scripts/bench_run_all.sh --miolink-firmware-root /path/to/MioLink/firmware --build-only
```

**Run only** — reuse already-built images, just flash and test:

```bash
./scripts/bench_run_all.sh --run-only
```

**Individual phases:**

```bash
# 1. Build the MioLink probe .uf2 images
./scripts/miolink_build_all.sh --miolink-firmware-root /path/to/MioLink/firmware

# 2. Build the test_board target firmware
./scripts/targets_build_all.sh

# 3. Flash everything and run the tests
./scripts/bench_run_tests_only.sh
```

**Useful options** (anything after `--` is forwarded to the bench runner):

```bash
# Run a single probe by serial number
./scripts/bench_run_tests_only.sh -- --probe E66394664F387030

# Include slots marked enabled: false in the config
./scripts/bench_run_tests_only.sh -- --include-disabled

# Skip re-flashing the target firmware
./scripts/bench_run_tests_only.sh -- --no-target-flash

# Use a custom bench config
./scripts/bench_run_all.sh --config config/bench_pizero2w.yaml --miolink-firmware-root /path/to/MioLink/firmware
```

Pass `-h` / `--help` to any script for the full list of flags.

## CI/CD

There are two GitHub Actions workflows: one that purely builds the target
firmware, and one that runs the full hardware-in-the-loop bench.

### Build Test Firmware CI

[`build-test-firmware-ci.yml`](.github/workflows/build-test-firmware-ci.yml)
runs on every push and pull request to `main` (and on manual
`workflow_dispatch`). It compiles the `test_board` firmware for the **full
variant matrix** and packages the results, acting as a compile-only smoke test
that nothing has broken across platforms/configs.

- **`version` job** — derives one version string shared by every artifact name
  (git tag name for tag refs, otherwise the short SHA) so the build matrix and
  the packaging job stay consistent.
- **`build` job (matrix)** — a matrix over the variant axes: target platform,
  `CONFIG_CLI` (`UART` / `RTT` / `NONE`), `CONFIG_TEST_SWO` (`OFF` / `ON`), and
  build type (`Debug` / `Release`). Combinations a platform can't support are
  excluded (e.g. SWO on a Cortex-M0 with no ITM/TPIU). `fail-fast` is off, so one
  broken variant doesn't hide the rest. Each cell checks out submodules, sets up
  the ARM GCC toolchain, extracts any committed vendor SDK the platform needs,
  runs `cmake` configure + build, and uploads a version-stamped `.elf` artifact.
- **`package` job** — runs only if **all** matrix builds succeeded (so the
  archive is never silently incomplete), merges every per-variant ELF into a
  single `MioLink_TestBench_firmware_<version>` archive, and best-effort prunes
  the intermediate `elf-*` staging artifacts.

### Run Bench CI

[`run-bench-ci.yml`](.github/workflows/run-bench-ci.yml) runs the actual bench
on physical hardware. It is triggered by **`repository_dispatch`
(`miolink-build`)** from the MioLink build pipeline once that pipeline has built
the probe firmware — or manually via `workflow_dispatch` with a `run_id` (and an
optional `sha` to report on).

The flow is split so the physical bench host stays cheap (it never compiles):

1. **`build-fixtures` job** (GitHub-hosted) — builds the `test_board` fixtures
   in the exact layout `run_bench.py` expects
   (`firmware/build/cli-X_swo-Y/test_boards/...`) and uploads only the `.elf`
   files as a `target-fixtures` artifact. The self-hosted runner therefore never
   needs a toolchain.
2. **`bench` job** (self-hosted, labels `[self-hosted, miolink-bench]`) — the
   one with the real hardware (e.g. a Pi Zero 2W):
   - Resolves the MioLink `run_id` from the dispatch payload or manual input.
   - **Cross-repo downloads** the exact probe `.uf2` set that this MioLink run
     produced (`actions/download-artifact` with `repository: Misaka0x2730/MioLink`
     + `run-id`, authenticated with the `MIOLINK_BENCH_TOKEN` secret). This
     guarantees the bench tests the precise artifacts that triggered it.
   - Downloads and unpacks the `target-fixtures` from step 1, installs the
     runtime-only Python deps, and runs `scripts/bench_run_tests_only.sh` to
     flash and test against the bench config.
   - A `concurrency` group (`cancel-in-progress: false`) serializes runs so
     concurrent MioLink changes **queue** instead of fighting over the single
     physical bench.
3. **`report` job** — `if: always()`, so it runs even when the bench fails or is
   skipped. It posts a commit status (`context: miolink-bench`) back onto the
   MioLink commit SHA, which renders in MioLink's PR checks list and resolves the
   pending status MioLink had set. It no-ops when there is no SHA to report on
   (e.g. a manual run without the `sha` input).

Both the cross-repo artifact download and the status report authenticate with
the **`MIOLINK_BENCH_TOKEN`** repository secret — a token with read access to
the MioLink repo's Actions artifacts and write access to its commit statuses.
The workflow cannot fetch the probe firmware or report results without it.

In other words, the two repos form a loop: **MioLink builds probe firmware →
dispatches to this repo → the bench flashes and tests it on real targets →
the result is reported back as a check on the MioLink commit.**

### Self-hosted bench runner

The `bench` job lands on a self-hosted runner labelled
`[self-hosted, miolink-bench]` — the host physically wired to the probes and
targets. Because compilation happens on GitHub-hosted runners, this host only
flashes and tests, so a low-powered SBC (e.g. a Raspberry Pi / Pi Zero 2W
running DietPi) is enough.

[`scripts/runner/provision_ghrunner.sh`](scripts/runner/provision_ghrunner.sh)
provisions such a host from scratch. It installs exactly what
`scripts/bench/run_bench.py` needs at job time — `arm-none-eabi-gdb` (flashes
targets over the BMP GDB/MI server), `picotool` (BOOTSEL flashing of RP2 probes,
incl. RP2350 / Pico 2W), `dfu-util` (detaches a running probe into BOOTSEL), and
`python3` + libusb for the runtime deps — plus udev rules and group membership
so an unprivileged service user gets raw-USB and `/dev/ttyACM*` access. It then
registers the GitHub Actions runner under a dedicated `ghrunner` user and
installs it as an auto-starting systemd service.

Run it on the Pi (it re-execs itself with `sudo`):

```bash
sudo ./scripts/runner/provision_ghrunner.sh \
    --toolchain-url <ARM_GNU_TOOLCHAIN_TARBALL_URL> \
    --token         <GITHUB_RUNNER_REGISTRATION_TOKEN> \
    --labels        miolink-bench \
    --url           https://github.com/Misaka0x2730/MioLink_TestBench
```

The `--labels` value must include `miolink-bench` so the `bench` job targets
this host.
