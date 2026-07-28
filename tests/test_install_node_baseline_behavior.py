"""Behavioral regression tests for the installers' Node.js baseline gates."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"


def _extract_function(text: str, signature: str) -> str:
    match = re.search(
        rf"(?ms)^{'function ' if signature[0].isupper() else ''}{re.escape(signature)}"
        r"(?:\(\))?\s*\{.*?^\}",
        text,
    )
    assert match is not None, f"function {signature} not found"
    return match.group(0)


def _powershell() -> str:
    executable = (
        shutil.which("powershell")
        or shutil.which("powershell.exe")
        or shutil.which("pwsh")
    )
    if not executable:
        pytest.skip("PowerShell is not available on this platform")
    return executable


def _bash() -> str:
    candidates = [os.environ.get("HERMES_GIT_BASH_PATH")]
    if os.name == "nt":
        candidates.append(r"C:\Program Files\Git\bin\bash.exe")
    candidates.append(shutil.which("bash"))

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.skip("bash is not available on this platform")


def test_shell_desktop_stage_fails_when_node_install_stays_below_baseline(
    tmp_path: Path,
) -> None:
    install_desktop = _extract_function(
        INSTALL_SH.read_text(encoding="utf-8"), "install_desktop"
    )

    harness = f"""
set -u
NODE_VERSION=24
HAS_NODE=false
INSTALL_DIR={shlex_quote(str(tmp_path / 'checkout'))}
check_node() {{ HAS_NODE=false; return 0; }}
log_error() {{ printf '%s\\n' "$*"; }}
log_info() {{ printf '%s\\n' "$*"; }}
log_warn() {{ printf '%s\\n' "$*"; }}
log_success() {{ printf '%s\\n' "$*"; }}
{install_desktop}
install_desktop
"""
    result = subprocess.run(
        [_bash(), "-s"],
        input=harness,
        env=os.environ,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Node.js 24" in result.stdout


def test_shell_node_version_predicate_rejects_23_and_accepts_24_plus() -> None:
    node_satisfies = _extract_function(
        INSTALL_SH.read_text(encoding="utf-8"),
        "node_satisfies_build",
    )
    harness = f"""
{node_satisfies}
NODE_VERSION=24
if node_satisfies_build v23.11.0; then exit 23; fi
node_satisfies_build v24.0.0 || exit 24
node_satisfies_build v25.1.0 || exit 25
"""

    result = subprocess.run(
        [_bash(), "-s"],
        input=harness,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_powershell_node_probe_reports_failed_fallback_as_false(tmp_path: Path) -> None:
    test_node = _extract_function(
        INSTALL_PS1.read_text(encoding="utf-8"), "Test-Node"
    )
    script = f"""
$ErrorActionPreference = 'Stop'
$NodeVersion = '24'
$HermesHome = {ps_quote(str(tmp_path))}
$script:HasNode = $false
function Write-Info {{ param([string]$Message) }}
function Write-Warn {{ param([string]$Message) }}
function Write-Success {{ param([string]$Message) }}
function Get-Command {{ param([string]$Name) return $null }}
{test_node}
if ((Test-Node) -ne $false) {{ throw 'failed Node fallback reported success' }}
"""
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_powershell_node_version_predicate_rejects_23_and_accepts_24_plus() -> None:
    version_test = _extract_function(
        INSTALL_PS1.read_text(encoding="utf-8"),
        "Test-NodeVersionOk",
    )
    script = f"""
$NodeVersion = "24"
{version_test}
if (Test-NodeVersionOk 'v23.11.0') {{ exit 23 }}
if (-not (Test-NodeVersionOk 'v24.0.0')) {{ exit 24 }}
if (-not (Test-NodeVersionOk 'v25.1.0')) {{ exit 25 }}
"""

    result = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_powershell_desktop_stage_fails_without_supported_node(tmp_path: Path) -> None:
    install_desktop = _extract_function(
        INSTALL_PS1.read_text(encoding="utf-8"), "Install-Desktop"
    )
    script = f"""
$ErrorActionPreference = 'Stop'
$NodeVersion = '24'
$InstallDir = {ps_quote(str(tmp_path / 'checkout'))}
$script:_StageSkippedReason = $null
function Test-Node {{ return $false }}
function Write-Info {{ param([string]$Message) }}
function Write-Warn {{ param([string]$Message) }}
function Write-Success {{ param([string]$Message) }}
{install_desktop}
Install-Desktop
"""
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Node.js 24" in f"{result.stdout}\n{result.stderr}"


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
