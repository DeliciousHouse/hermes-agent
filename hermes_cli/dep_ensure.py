"""Lazy dependency bootstrapper for non-Python runtime deps.

Detection and prompting live here in Python — not in install.sh — because:
  1. shutil.which() works on every platform; install.sh needs bash.
  2. Detection is instant; spawning bash for a "is node installed?" check is waste.
  3. Python controls the UX (rich prompts, non-interactive fallback, TTY detection).

install.sh is still the *installation* backend because it has 1900 lines of
battle-tested OS detection and package-manager logic (apt/brew/pacman/dnf/
zypper/Termux/…).  Reimplementing that in Python would be huge duplication.

Deps that degrade gracefully (ripgrep → grep fallback, ffmpeg → skip conversion)
don't need ensure_dependency wired in — only hard-fail sites do (TUI needs node,
browser tool needs agent-browser).
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


_IS_WINDOWS = platform.system() == "Windows"
NODE_MIN_MAJOR = 24


def _get_hermes_home() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home()


def node_satisfies_build(version: str) -> bool:
    """Return whether a Node version satisfies the repository baseline."""
    try:
        normalized = version.strip().removeprefix("v").split("-", 1)[0]
        major = int(normalized.split(".", 1)[0])
    except (AttributeError, TypeError, ValueError):
        return False
    return major >= NODE_MIN_MAJOR


def _managed_node_path() -> Path:
    home = _get_hermes_home()
    if _IS_WINDOWS:
        return home / "node" / "node.exe"
    return home / "node" / "bin" / "node"


def _promote_node_directory(node_path: str) -> None:
    """Expose npm/npx installed beside a selected Node to this process."""
    node_dir = str(Path(node_path).expanduser().parent)
    parts = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    if node_dir in parts:
        return
    os.environ["PATH"] = os.pathsep.join([node_dir, *parts])


def resolve_supported_node(node_path: str | None = None) -> str | None:
    """Resolve Node >= the repository baseline, including Hermes-managed installs."""
    if node_path:
        candidates = [node_path]
    else:
        managed_node = _managed_node_path()
        candidates = [
            os.environ.get("HERMES_NODE"),
            shutil.which("node"),
            str(managed_node) if managed_node.is_file() else None,
        ]

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and node_satisfies_build(result.stdout):
            _promote_node_directory(candidate)
            return candidate
    return None


def node_is_supported(node_path: str | None = None) -> bool:
    """Check that a supported Node executable is available."""
    return resolve_supported_node(node_path) is not None


_DEP_CHECKS = {
    "node": node_is_supported,
    "browser": lambda: (
        shutil.which("agent-browser") is not None
        or _has_system_browser()
        or _has_hermes_agent_browser()
    ),
    "ripgrep": lambda: shutil.which("rg") is not None,
    "ffmpeg": lambda: shutil.which("ffmpeg") is not None,
}

_DEP_DESCRIPTIONS = {
    "node": "Node.js (required for browser tools and TUI)",
    "browser": "Browser engine (Chromium, for web browsing tools)",
    "ripgrep": "ripgrep (fast file search)",
    "ffmpeg": "ffmpeg (TTS voice messages)",
}


def _has_system_browser() -> bool:
    if _IS_WINDOWS:
        names = ("chrome", "msedge", "chromium")
    else:
        names = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")
    for name in names:
        if shutil.which(name):
            return True
    return False


def _has_hermes_agent_browser() -> bool:
    home = _get_hermes_home()
    if _IS_WINDOWS:
        # npm -g --prefix puts .cmd shims directly in the prefix dir on Windows
        return (home / "node" / "agent-browser.cmd").is_file()
    # install.sh installs globally into $HERMES_HOME/node/bin/ via npm -g --prefix
    # Also check legacy node_modules/.bin/ path for git-clone installs.
    return (
        (home / "node" / "bin" / "agent-browser").is_file()
        or (home / "node_modules" / ".bin" / "agent-browser").is_file()
    )


def _find_install_script(
    package_dir: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[Path | None, str | None]:
    """Locate the install script — bundled in wheel or in git checkout.

    On Windows, prefers install.ps1; on POSIX, prefers install.sh.
    Returns a (path, shell) tuple, or (None, None) if neither is found.
    """
    if package_dir is None:
        package_dir = Path(__file__).parent
    if repo_root is None:
        repo_root = package_dir.parent

    if _IS_WINDOWS:
        preferred = ("install.ps1", "powershell")
        fallback = ("install.sh", "bash")
    else:
        preferred = ("install.sh", "bash")
        fallback = ("install.ps1", "powershell")

    for script_name, shell in (preferred, fallback):
        bundled = package_dir / "scripts" / script_name
        if bundled.is_file():
            return bundled, shell
        repo = repo_root / "scripts" / script_name
        if repo.is_file():
            return repo, shell

    return None, None


def ensure_dependency(
    dep: str,
    interactive: bool = True,
) -> bool:
    """Ensure a non-Python dependency is available. Returns True if available."""
    check = _DEP_CHECKS.get(dep)
    if check is None:
        # Unknown dep — don't silently forward to install script.
        return False
    if check():
        return True

    script, shell = _find_install_script()
    if script is None:
        if interactive:
            desc = _DEP_DESCRIPTIONS.get(dep, dep)
            print(f"  {desc} is not installed and no install script was found.")
            print(f"  Install {dep} manually and try again.")
        return False

    if interactive and sys.stdin.isatty():
        desc = _DEP_DESCRIPTIONS.get(dep, dep)
        try:
            reply = input(f"{desc} is not installed. Install now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if reply not in ("", "y", "yes"):
            return False

    if shell == "powershell":
        ps_bin = shutil.which("powershell") or shutil.which("pwsh")
        if not ps_bin:
            if interactive:
                print("  PowerShell not found. Install PowerShell or run install.ps1 manually.")
            return False
        cmd = [
            ps_bin,
            "-ExecutionPolicy", "Bypass",
            "-File", str(script),
            "-Ensure", dep,
            "-HermesHome", str(_get_hermes_home()),
        ]
    else:
        cmd = ["bash", str(script), "--ensure", dep]

    run_env = {**os.environ, "IS_INTERACTIVE": "false"}
    result = subprocess.run(
        cmd,
        env=run_env,
    )
    if result.returncode != 0:
        return False

    if check:
        return check()
    return True
