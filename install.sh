#!/bin/sh
# nr-workbench installer for macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/neutrons-ai/aure-workbench/main/install.sh | sh
#
# What it does: installs `uv` if it is not already there, then installs
# nr-workbench as an isolated uv tool, which puts `nrw` on your PATH with its
# own Python. Nothing lands outside $HOME and nothing needs sudo.
#
# Why uv rather than pip: nr-workbench needs Python >= 3.11 and stock macOS
# ships 3.9. uv is a single binary that downloads a private CPython, so this
# works on a machine with no usable Python at all, and the user never has to
# create or activate a virtualenv.
#
# Configuration is by environment variable, because a script run through
# `curl | sh` cannot be given arguments. docs/install.md documents them:
#
#   NRW_VERSION            git ref to install               (default: main)
#   NRW_REPO               git URL to install from
#   NRW_PYTHON             Python for the tool environment  (default: 3.13)
#   NRW_EXTRAS             extras to include, e.g. "nexus"
#   NRW_UV_VERSION         pin uv to this version instead of the latest
#   NRW_SYSTEM_CERTS=1     use the system trust store from the start
#   NRW_INSTALL_DRY_RUN=1  print what would run, touch nothing, exit 0
#
# Everything below is a definition; `main` runs at the very last line. That is
# deliberate: `sh` executes a piped script as it arrives, so a script whose
# body sits at the top level runs half of itself if the download is truncated
# mid-transfer. With one trailing call, a truncated download does nothing.

set -eu

NRW_REPO="${NRW_REPO:-https://github.com/neutrons-ai/aure-workbench.git}"
NRW_VERSION="${NRW_VERSION:-main}"
NRW_PYTHON="${NRW_PYTHON:-3.13}"
NRW_EXTRAS="${NRW_EXTRAS:-}"
NRW_UV_VERSION="${NRW_UV_VERSION:-}"
NRW_SYSTEM_CERTS="${NRW_SYSTEM_CERTS:-0}"
DRY_RUN="${NRW_INSTALL_DRY_RUN:-0}"

BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"

# The PATH as the user's shell handed it to us. Kept because this script adds
# to its own PATH when it installs uv, and the final check -- "will your next
# shell find nrw?" -- has to be asked of the PATH the user actually has.
# src/nr_workbench/project/toolpath.py documents the same trap: a child
# inheriting our environment finds the tool every time, so the check passes in
# exactly the case it exists to catch.
ORIGINAL_PATH="$PATH"

# ---------------------------------------------------------------- output ---

init_output() {
    # Everything informational goes to stderr, so progress is still visible
    # when stdout is redirected and a piped stdout stays clean.
    if [ -t 2 ] && [ -z "${NO_COLOR:-}" ]; then
        BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m')
        RED=$(printf '\033[31m'); YEL=$(printf '\033[33m'); OFF=$(printf '\033[0m')
    else
        BOLD=''; DIM=''; RED=''; YEL=''; OFF=''
    fi
}

status() { printf '%s>>>%s %s\n' "$BOLD" "$OFF" "$*" >&2; }
detail() { printf '    %s%s%s\n' "$DIM" "$*" "$OFF" >&2; }
warn()   { printf '%swarning:%s %s\n' "$YEL" "$OFF" "$*" >&2; }
error()  { printf '%serror:%s %s\n' "$RED" "$OFF" "$*" >&2; exit 1; }

available() { command -v "$1" >/dev/null 2>&1; }

# A URL with its userinfo removed. Anything derived from NRW_REPO is printed,
# and someone installing from a private fork will reasonably reach for
# https://user:token@github.com/...
redacted() {
    printf '%s' "$1" | sed -e 's#://[^/@]*@#://#'
}

# Print the command in dry-run mode, run it otherwise. Every state-changing
# step goes through this, which is what lets CI exercise the script without
# installing anything.
run() {
    if [ "$DRY_RUN" = "1" ]; then
        # Redacted, because the requirement embeds NRW_REPO and someone
        # installing from a private fork may have put a token in it.
        printf '    %s$ %s%s\n' "$DIM" "$(redacted "$*")" "$OFF" >&2
        return 0
    fi
    "$@"
}

# ------------------------------------------------------------- platform ---

detect_platform() {
    case "$(uname -s)" in
        Darwin) PLATFORM="macOS" ;;
        Linux)  PLATFORM="Linux" ;;
        *)
            error "this script installs on macOS and Linux only.
    On Windows, run this in PowerShell instead:
        irm https://raw.githubusercontent.com/neutrons-ai/aure-workbench/main/install.ps1 | iex
    Under Git Bash, MSYS or Cygwin, use PowerShell or WSL2."
            ;;
    esac
}

# A package-manager hint for a missing tool, so the error is actionable
# rather than merely true.
install_hint() {
    if [ "$PLATFORM" = "macOS" ]; then
        echo "xcode-select --install"
    elif available apt-get; then
        echo "sudo apt-get install -y $1"
    elif available dnf; then
        echo "sudo dnf install -y $1"
    elif available yum; then
        echo "sudo yum install -y $1"
    elif available zypper; then
        echo "sudo zypper install -y $1"
    elif available pacman; then
        echo "sudo pacman -S --noconfirm $1"
    else
        echo "install $1 with your package manager"
    fi
}

require() {
    available "$1" || error "\`$1\` is required but not installed.
    $(install_hint "$1")"
}

# ------------------------------------------------------------ validation ---

# These end up inside a PEP 508 requirement, which has its own grammar: a
# stray `]` or `@` in the extras turns `nr-workbench[x] @ git+<ours>` into a
# requirement pointing somewhere else entirely, and `uv tool install` on a git
# URL runs that repository's build backend. None of these variables is set by
# default, but a shared analysis machine is exactly where one might be.
validate_config() {
    case "$BIN_DIR" in
        /*) ;;
        *) error "XDG_BIN_HOME must be an absolute path (got: $BIN_DIR)" ;;
    esac

    case "$NRW_EXTRAS" in
        "") ;;
        *[!a-zA-Z0-9,_-]*)
            error "NRW_EXTRAS may contain only letters, digits, '-', '_' and ','." ;;
    esac

    case "$NRW_VERSION" in
        *[!a-zA-Z0-9./_-]*)
            error "NRW_VERSION must be a git ref: letters, digits, '.', '/', '_' and '-'." ;;
    esac

    case "$NRW_REPO" in
        https://*|ssh://*|git@*|file://*|/*) ;;
        *) error "NRW_REPO must be an https://, ssh://, git@ or file:// URL." ;;
    esac

    case "$NRW_UV_VERSION" in
        ""|*[!a-zA-Z0-9.]*)
            [ -z "$NRW_UV_VERSION" ] ||
                error "NRW_UV_VERSION must be a version number, e.g. 0.12.17." ;;
    esac
}

# -------------------------------------------------------------------- uv ---

ensure_uv() {
    if available uv; then
        detail "using uv $(uv --version 2>/dev/null | cut -d' ' -f2) at $(command -v uv)"
        return 0
    fi

    status "Installing uv (a single binary; it brings its own Python)"
    # curl is needed only here. git, checked separately, is needed even when
    # uv is already present.
    require curl

    if [ -n "$NRW_UV_VERSION" ]; then
        url="https://astral.sh/uv/$NRW_UV_VERSION/install.sh"
    else
        url="https://astral.sh/uv/install.sh"
    fi

    if [ "$DRY_RUN" = "1" ]; then
        detail "\$ curl -fLsS $url -o \$tmp && UV_INSTALL_DIR=$BIN_DIR sh \$tmp"
        return 0
    fi

    # Downloaded and run in two steps on purpose. Piped together, the pipeline
    # reports only `sh`'s status, so an unreachable astral.sh or a moved URL
    # feeds an empty file into a shell that exits 0 -- and the failure would
    # surface later as the misleading "uv did not end up on PATH". Passing the
    # install directory through the environment rather than building a
    # `sh -c` string also keeps a quote in $XDG_BIN_HOME from becoming shell.
    uv_script=$(mktemp)
    curl -fLsS "$url" -o "$uv_script" || {
        rm -f "$uv_script"
        error "could not download the uv installer from $url.
    Check your network, or set HTTPS_PROXY if you are behind a proxy."
    }
    UV_INSTALL_DIR="$BIN_DIR" sh "$uv_script" >&2 || {
        rm -f "$uv_script"
        error "the uv installer failed."
    }
    rm -f "$uv_script"

    PATH="$BIN_DIR:$PATH"
    export PATH
    available uv ||
        error "uv did not end up on PATH after installation; expected it in $BIN_DIR."
}

# uv renamed this: UV_SYSTEM_CERTS is current, UV_NATIVE_TLS is the deprecated
# spelling an older uv on the machine may still be looking for. Setting both
# costs one deprecation warning and covers either version.
use_system_certs() {
    UV_SYSTEM_CERTS=1
    UV_NATIVE_TLS=1
    export UV_SYSTEM_CERTS UV_NATIVE_TLS
}

# --force so re-running the installer upgrades in place rather than refusing
# over the existing `nrw` executable, and --reinstall-package so a moved
# `main` is fetched again instead of served from uv's cached checkout of that
# ref (it implies --refresh-package, and naming just this package leaves the
# heavy scientific dependencies cached).
uv_install() {
    run uv tool install \
        --force \
        --reinstall-package nr-workbench \
        --python "$NRW_PYTHON" \
        "$SPEC"
}

install_nrw() {
    if [ -n "$NRW_EXTRAS" ]; then
        SPEC="nr-workbench[$NRW_EXTRAS] @ git+$NRW_REPO@$NRW_VERSION"
    else
        SPEC="nr-workbench @ git+$NRW_REPO@$NRW_VERSION"
    fi

    # The requirement itself, not a second rendering of it: a reader must be
    # able to see the source that is actually about to be built.
    status "Installing $(redacted "$SPEC")"
    detail "this pulls refl1d, bumps, scipy and AuRE; the first run takes a minute"

    [ "$NRW_SYSTEM_CERTS" = "1" ] && use_system_certs

    if [ "$DRY_RUN" = "1" ]; then
        uv_install
        detail "(dry run: nothing was installed)"
        exit 0
    fi

    # Keep the output on screen *and* keep uv's exit status, which a plain
    # `uv_install | tee` would throw away: a pipeline reports the status of
    # its last command, so a failed install would look like a success.
    LOG=$(mktemp)
    RC=$(mktemp)
    trap 'rm -f "$LOG" "$RC"' EXIT INT TERM
    {
        if uv_install; then rc=0; else rc=$?; fi
        # Only the echo is redirected. Putting `>"$RC"` on the whole `if`
        # would send uv's stdout there too, and the check below would read
        # uv's output instead of the exit code -- a successful install
        # reported as a failure, the moment uv prints anything on stdout.
        echo "$rc" >"$RC"
    } 2>&1 | tee "$LOG" >&2

    [ "$(cat "$RC")" = "0" ] && return 0

    # uv ships its own certificate bundle, which a TLS-inspecting proxy -- the
    # normal arrangement on a lab or campus network -- is not part of. The
    # system trust store does have the proxy's root, so retrying against it is
    # the fix, and doing it automatically is worth it because the raw error
    # ("invalid peer certificate: UnknownIssuer") names none of this.
    #
    # This is not a downgrade: it changes which root store is used, and chain,
    # expiry and hostname verification all still happen. It does mean the
    # first machine to be intercepted says so only in passing, hence the note.
    if [ "${UV_SYSTEM_CERTS:-0}" != "1" ] &&
        grep -qiE 'invalid peer certificate|unknownissuer|self[ -]signed certificate|certificate verify failed' "$LOG"; then
        warn "TLS verification failed with uv's bundled certificates."
        detail "Retrying against your system's trust store (usual on a proxied network)."
        use_system_certs
        uv_install >&2 || error "installation failed even with the system trust store.
    Behind a proxy, set HTTPS_PROXY and try again."
        detail "Succeeded via the system trust store. If you did not expect TLS"
        detail "inspection on this network, that is worth asking about."
    else
        error "installation failed; see the output above."
    fi
}

# ---------------------------------------------------------------- verify ---

verify_install() {
    # Ask uv where it put the executables rather than assuming: it honours
    # UV_TOOL_BIN_DIR and XDG_BIN_HOME, and guessing wrong would report
    # success while pointing at nothing.
    TOOL_BIN="$(uv tool dir --bin 2>/dev/null || echo "$BIN_DIR")"
    NRW="$TOOL_BIN/nrw"

    [ -x "$NRW" ] || error "installation reported success but $NRW is missing."

    # Not a best-effort read: the common partial failure is an executable that
    # exists and does not run -- a dependency that failed to build, a wheel
    # for the wrong architecture -- and swallowing that would announce success
    # and leave `nrw doctor` to break the news.
    VERSION=$("$NRW" --version 2>&1) || error "$NRW was installed but does not run:
    $VERSION"

    # Being on PATH is not the same as being *first* on PATH. A stale
    # virtualenv ahead of the tool directory is a failure this project has
    # already been bitten by, and it resolves rather than erroring, which is
    # the worse outcome. Asked of the caller's PATH, not ours.
    RESOLVED="$(PATH="$ORIGINAL_PATH"; command -v nrw 2>/dev/null || true)"
    if [ -z "$RESOLVED" ]; then
        status "Adding $TOOL_BIN to your PATH"
        run uv tool update-shell >&2 || true
        warn "restart your shell, or run: export PATH=\"$TOOL_BIN:\$PATH\""
    elif [ "$RESOLVED" != "$NRW" ]; then
        warn "\`nrw\` on your PATH is $RESOLVED, not the one just installed."
        detail "Put $TOOL_BIN ahead of it, or remove the older install."
    fi

    status "$VERSION installed"
    printf '\n' >&2
    printf '    nrw doctor        check the environment\n' >&2
    printf '    nrw init          scaffold a project in the current directory\n' >&2
    printf '\n' >&2
    printf '  Upgrade by re-running this installer.\n' >&2
    printf '  Remove with: uv tool uninstall nr-workbench\n' >&2
}

# ------------------------------------------------------------------ main ---

main() {
    init_output
    detect_platform
    validate_config

    status "Installing nr-workbench on $PLATFORM"

    # git is needed twice over: to resolve the AuRE dependency, which is
    # pinned by commit SHA rather than published to PyPI, and at run time,
    # because every fit records the commit of the project it ran in.
    require git

    ensure_uv
    install_nrw
    verify_install
}

main "$@"
