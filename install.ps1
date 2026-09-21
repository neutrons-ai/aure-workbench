# nr-workbench installer for Windows.
#
#   irm https://raw.githubusercontent.com/neutrons-ai/aure-workbench/main/install.ps1 | iex
#
# What it does: installs `uv` if it is not already there, then installs
# nr-workbench as an isolated uv tool, which puts `nrw` on your PATH with its
# own Python. Nothing lands outside your user profile and nothing needs
# administrator rights.
#
# Why uv rather than pip: nr-workbench needs Python >= 3.11. uv is a single
# binary that downloads a private CPython, so this works on a machine with no
# Python at all, and you never have to activate a virtual environment.
#
# Configuration is by environment variable, because a script run through
# `irm | iex` has no way to accept parameters:
#
#   $env:NRW_VERSION            git ref to install               (default: main)
#   $env:NRW_REPO               git URL to install from
#   $env:NRW_PYTHON             Python for the tool environment  (default: 3.13)
#   $env:NRW_EXTRAS             extras to include, e.g. "nexus"
#   $env:NRW_UV_VERSION         pin uv to this version instead of the latest
#   $env:NRW_SYSTEM_CERTS = 1   use the system trust store from the start
#   $env:NRW_INSTALL_DRY_RUN = 1  print what would run, touch nothing

$ErrorActionPreference = 'Stop'

$NrwUvBootstrapUrl = 'https://astral.sh/uv/install.ps1'

# Windows PowerShell 5.1 still negotiates TLS 1.0 by default, which astral.sh
# and github.com both refuse. Harmless on PowerShell 7+.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    # Newer .NET manages this itself and the property may be unavailable.
}

# Names are prefixed because `irm | iex` runs this in the caller's session and
# plain verbs would overwrite whatever they already have defined there.
function Write-NrwStatus([string] $Message) {
    Write-Host ">>> $Message" -ForegroundColor Cyan
}
function Write-NrwDetail([string] $Message) {
    Write-Host "    $Message" -ForegroundColor DarkGray
}
function Write-NrwWarning([string] $Message) {
    Write-Host "warning: $Message" -ForegroundColor Yellow
}
function Test-NrwCommand([string] $Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-NrwRedacted([string] $Text) {
    <#
      Anything derived from NRW_REPO gets printed, and someone installing
      from a private fork will reasonably put a token in the URL.
    #>
    return ($Text -replace '://[^/@]*@', '://')
}

function Assert-NrwConfig {
    <#
      These end up inside a PEP 508 requirement, which has its own grammar: a
      stray ']' or '@' in the extras turns "nr-workbench[x] @ git+<ours>"
      into a requirement pointing somewhere else, and `uv tool install` on a
      git URL runs that repository's build backend. None of these is set by
      default, but a shared machine is exactly where one might be.
    #>
    param([string] $Extras, [string] $Version, [string] $Repo, [string] $UvVersion)

    if ($Extras -and $Extras -notmatch '^[A-Za-z0-9,_-]+$') {
        throw "NRW_EXTRAS may contain only letters, digits, '-', '_' and ','."
    }
    if ($Version -notmatch '^[A-Za-z0-9./_-]+$') {
        throw "NRW_VERSION must be a git ref: letters, digits, '.', '/', '_' and '-'."
    }
    if ($Repo -notmatch '^(https://|ssh://|git@|file://|[A-Za-z]:\\)') {
        throw "NRW_REPO must be an https://, ssh://, git@ or file:// URL."
    }
    if ($UvVersion -and $UvVersion -notmatch '^[0-9A-Za-z.]+$') {
        throw "NRW_UV_VERSION must be a version number, e.g. 0.12.17."
    }
}

function Invoke-NrwUv {
    <#
      Runs uv, echoing its output live while keeping both the exit code and a
      copy of the text -- the copy is what the TLS fallback below inspects.
    #>
    param([string[]] $UvArgs)

    $captured = New-Object System.Collections.Generic.List[string]

    # Windows PowerShell 5.1 wraps a native command's stderr in a
    # NativeCommandError the moment that stream is captured, and
    # $ErrorActionPreference = 'Stop' promotes it to a terminating error. uv
    # writes all of its progress to stderr, so leaving the preference alone
    # here would abort the install on uv's first line of perfectly normal
    # output. The exit code, which the caller checks, is what actually says
    # whether uv succeeded.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & uv @UvArgs 2>&1 | ForEach-Object {
            $line = "$_"
            Write-Host $line
            $captured.Add($line)
        }
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }

    return [pscustomobject]@{
        Code   = $code
        Output = ($captured -join "`n")
    }
}

function Get-NrwToolBin([string] $Fallback) {
    <#
      Where uv puts tool executables. Asking is better than assuming, because
      uv honours UV_TOOL_BIN_DIR -- and the stderr caveat above applies to
      this capture too.
    #>
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $dir = (& uv tool dir --bin 2>$null | Select-Object -First 1)
    }
    catch {
        $dir = $null
    }
    finally {
        $ErrorActionPreference = $previous
    }

    if ($dir) { return "$dir".Trim() }
    return $Fallback
}

function Install-NrWorkbench {
    if ($PSVersionTable.PSVersion.Major -lt 5) {
        throw "PowerShell 5.1 or newer is required (this is $($PSVersionTable.PSVersion))."
    }

    $repo    = if ($env:NRW_REPO)    { $env:NRW_REPO }    else { 'https://github.com/neutrons-ai/aure-workbench.git' }
    $version = if ($env:NRW_VERSION) { $env:NRW_VERSION } else { 'main' }
    $python  = if ($env:NRW_PYTHON)  { $env:NRW_PYTHON }  else { '3.13' }
    $extras  = $env:NRW_EXTRAS
    $uvPin   = $env:NRW_UV_VERSION
    $dryRun  = $env:NRW_INSTALL_DRY_RUN -eq '1'

    Assert-NrwConfig -Extras $extras -Version $version -Repo $repo -UvVersion $uvPin
    # The PATH as the user's session handed it to us. Kept because this
    # script adds to its own PATH below, and the check at the end -- "will a
    # new PowerShell window find nrw?" -- has to be asked of the PATH the user
    # actually has, not the one we just improved for ourselves.
    $originalPath = $env:Path

    # $HOME is the fallback so the script can be parsed and dry-run under
    # pwsh on Linux in CI, where USERPROFILE does not exist.
    $profileDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
    $binDir  = Join-Path $profileDir '.local\bin'

    Write-NrwStatus 'Installing nr-workbench on Windows'

    # git is needed twice over: to resolve the AuRE dependency, which is
    # pinned by commit SHA rather than published to PyPI, and at run time,
    # because every fit records the commit of the project it ran in.
    if (-not (Test-NrwCommand git)) {
        throw @'
git is required but not installed. Install it with:

    winget install --id Git.Git -e

(or from https://git-scm.com/download/win), then open a new PowerShell window
and run this installer again.
'@
    }

    # ------------------------------------------------------------- uv ---
    if (Test-NrwCommand uv) {
        $uvVersion = (& uv --version) -join ''
        Write-NrwDetail "using $uvVersion at $((Get-Command uv).Source)"
    }
    else {
        Write-NrwStatus 'Installing uv (a single binary; it brings its own Python)'
        $bootstrap = if ($uvPin) { "https://astral.sh/uv/$uvPin/install.ps1" }
                     else { $NrwUvBootstrapUrl }
        if ($dryRun) {
            Write-NrwDetail "`$ irm $bootstrap | iex"
        }
        else {
            Invoke-RestMethod $bootstrap | Invoke-Expression
            # uv's installer updates the stored user PATH, which this process
            # will not see until it restarts -- so put it on ours by hand.
            $env:Path = "$binDir;$env:Path"
            if (-not (Test-NrwCommand uv)) {
                throw "uv did not end up on PATH after installation; expected it in $binDir."
            }
        }
    }

    # -------------------------------------------------------- install ---
    $spec = if ($extras) {
        "nr-workbench[$extras] @ git+$repo@$version"
    } else {
        "nr-workbench @ git+$repo@$version"
    }

    # --force so re-running the installer upgrades in place rather than
    # refusing over the existing nrw.exe, and --reinstall-package so a moved
    # `main` is fetched again instead of served from uv's cached checkout of
    # that ref (it implies --refresh-package, and naming just this package
    # leaves the heavy scientific dependencies cached).
    $uvArgs = @(
        'tool', 'install',
        '--force',
        '--reinstall-package', 'nr-workbench',
        '--python', $python,
        $spec
    )

    # The requirement itself, not a second rendering of it: a reader must be
    # able to see the source that is actually about to be built.
    Write-NrwStatus "Installing $(Get-NrwRedacted $spec)"
    Write-NrwDetail 'this pulls refl1d, bumps, scipy and AuRE; the first run takes a minute'

    if ($env:NRW_SYSTEM_CERTS -eq '1') {
        # uv renamed this: UV_SYSTEM_CERTS is current, UV_NATIVE_TLS is the
        # deprecated spelling an older uv may still be looking for. Setting
        # both costs one deprecation warning and covers either version.
        $env:UV_SYSTEM_CERTS = '1'
        $env:UV_NATIVE_TLS = '1'
    }

    if ($dryRun) {
        Write-NrwDetail "$ uv $($uvArgs -join ' ')"
        Write-NrwDetail '(dry run: nothing was installed)'
        return
    }

    $result = Invoke-NrwUv $uvArgs
    if ($result.Code -ne 0) {
        # uv ships its own certificate bundle, which a TLS-inspecting proxy --
        # the normal arrangement on a lab or campus network -- is not part of.
        # The system trust store does have the proxy's root, so retrying
        # against it is the fix, and doing it automatically is worth it
        # because the raw error ("invalid peer certificate: UnknownIssuer")
        # names none of this.
        if ($env:UV_SYSTEM_CERTS -ne '1' -and
            $result.Output -match '(?i)invalid peer certificate|unknownissuer|self[ -]signed certificate|certificate verify failed') {
            Write-NrwWarning "TLS verification failed with uv's bundled certificates."
            Write-NrwDetail 'Retrying with the system trust store (usual on a proxied network).'
            $env:UV_SYSTEM_CERTS = '1'
            $env:UV_NATIVE_TLS = '1'
            $result = Invoke-NrwUv $uvArgs
            if ($result.Code -ne 0) {
                throw "installation failed even with the system trust store. Behind a proxy, set HTTPS_PROXY and try again."
            }
            Write-NrwDetail 'Succeeded via the system trust store. If you did not expect TLS'
            Write-NrwDetail 'inspection on this network, that is worth asking about.'
        }
        else {
            throw 'installation failed; see the output above.'
        }
    }

    # --------------------------------------------------------- verify ---
    $toolBin = Get-NrwToolBin $binDir
    $nrw = Join-Path $toolBin 'nrw.exe'

    if (-not (Test-Path $nrw)) {
        throw "installation reported success but $nrw is missing."
    }
    # Not a best-effort read: the common partial failure is an executable
    # that exists and does not run, and swallowing that would announce
    # success and leave `nrw doctor` to break the news.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $installed = (& $nrw --version 2>&1) -join ''
        $versionCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($versionCode -ne 0) {
        throw "$nrw was installed but does not run: $installed"
    }

    # Being on PATH is not the same as being first on PATH. A stale virtual
    # environment ahead of the tool directory resolves rather than erroring,
    # which is the worse outcome.
    $current = $env:Path
    try {
        $env:Path = $originalPath
        $resolved = Get-Command nrw -ErrorAction SilentlyContinue
    }
    finally {
        $env:Path = $current
    }
    if (-not $resolved) {
        Write-NrwStatus "Adding $toolBin to your PATH"
        & uv tool update-shell
        Write-NrwWarning "open a new PowerShell window before running nrw."
    }
    elseif ($resolved.Source -ne $nrw) {
        Write-NrwWarning "nrw on your PATH is $($resolved.Source), not the one just installed."
        Write-NrwDetail "Put $toolBin ahead of it, or remove the older install."
    }

    Write-NrwStatus "$installed installed"
    Write-Host @'

    nrw doctor        check the environment
    nrw init          scaffold a project in the current directory

  Upgrade by re-running this installer.
  Remove with: uv tool uninstall nr-workbench

  Windows support is newer than the rest of this project and is not covered
  by CI. `nrw init`, the model and fit commands and `nrw serve` are expected
  to work; `nrw agent run` and linked data imports are POSIX-only today.
  See https://github.com/neutrons-ai/aure-workbench/blob/main/docs/install.md
'@
}

try {
    Install-NrWorkbench
}
catch {
    Write-Host "error: $($_.Exception.Message)" -ForegroundColor Red
    # `exit` would close the console window when this script is run through
    # `irm | iex`, because iex evaluates it in the caller's own session. Only
    # exit non-zero when we really are a file being executed, which is what
    # CI needs. A wrapper that checks $LASTEXITCODE -- a Dockerfile, an Intune
    # script, a setup runbook -- would otherwise record a failed install as a
    # success, so set that either way.
    $global:LASTEXITCODE = 1
    if ($MyInvocation.MyCommand.Path) { exit 1 }
}
