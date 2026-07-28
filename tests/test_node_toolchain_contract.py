"""Cross-file invariants for the repository's Node.js baseline.

The root ``package.json`` owns the minimum supported Node major. Runtime,
installer, Nix, CI, lockfile, and user-facing setup surfaces must follow that
single contract instead of drifting independently.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_USING_ROOT_NPM = (
    ".github/workflows/typecheck.yml",
    ".github/workflows/build-windows-installer.yml",
    ".github/workflows/deploy-site.yml",
    ".github/workflows/docs-site-checks.yml",
    ".github/workflows/upload_to_pypi.yml",
)
MANAGED_NODE_DOCS = (
    "CONTRIBUTING.md",
    "website/docs/getting-started/installation.md",
    "website/docs/getting-started/nix-setup.md",
    "website/docs/developer-guide/contributing.md",
    "website/docs/user-guide/features/acp.md",
    "website/docs/user-guide/windows-native.md",
    "website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/getting-started/installation.md",
    "website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/getting-started/nix-setup.md",
    "website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/developer-guide/contributing.md",
    "website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/user-guide/features/acp.md",
    "website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/user-guide/windows-native.md",
)


def _text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _node_major() -> int:
    package = json.loads(_text("package.json"))
    requirement = package["engines"]["node"]
    match = re.fullmatch(r">=(\d+)", requirement)
    assert match, "root engines.node must declare one minimum Node major"
    return int(match.group(1))


def _required_match(pattern: str, text: str, surface: str) -> re.Match[str]:
    match = re.search(pattern, text, re.MULTILINE)
    assert match, f"could not find the Node selector in {surface}"
    return match


def test_root_lockfile_tracks_package_engine_contract() -> None:
    package = json.loads(_text("package.json"))
    lockfile = json.loads(_text("package-lock.json"))

    assert lockfile["packages"][""]["engines"]["node"] == package["engines"]["node"]


def test_all_first_party_node_engine_declarations_meet_root_baseline() -> None:
    required_major = _node_major()

    for package_json in REPO_ROOT.rglob("package.json"):
        if "node_modules" in package_json.parts:
            continue
        package = json.loads(package_json.read_text(encoding="utf-8"))
        requirement = (package.get("engines") or {}).get("node")
        if not requirement:
            continue
        declared_majors = [
            int(value)
            for value in re.findall(
                r"(?:^|\|\|)\s*[<>=~^ ]*(\d+)",
                requirement,
            )
        ]
        assert declared_majors, f"could not parse engines.node in {package_json}"
        assert min(declared_majors) >= required_major, (
            f"{package_json.relative_to(REPO_ROOT)} allows Node {min(declared_majors)} "
            f"below the repository baseline {required_major}"
        )

        local_lock_path = package_json.parent / "package-lock.json"
        if local_lock_path.is_file():
            lockfile = json.loads(local_lock_path.read_text(encoding="utf-8"))
            locked_requirement = lockfile["packages"][""]["engines"]["node"]
        else:
            root_lock = json.loads(_text("package-lock.json"))
            workspace = package_json.parent.relative_to(REPO_ROOT).as_posix()
            locked_requirement = root_lock["packages"][workspace]["engines"]["node"]
        assert locked_requirement == requirement, (
            f"lockfile Node engine for {package_json.relative_to(REPO_ROOT)} "
            f"does not match {requirement}"
        )


@pytest.mark.parametrize("workflow", WORKFLOWS_USING_ROOT_NPM)
def test_workflow_node_versions_match_root_engine(workflow: str) -> None:
    match = _required_match(
        r"node-version:\s*['\"]?(\d+)",
        _text(workflow),
        workflow,
    )

    assert int(match.group(1)) == _node_major()


def test_runtime_and_nix_node_versions_match_root_engine() -> None:
    major = _node_major()
    dockerfile = _text("Dockerfile")
    dep_ensure = _text("hermes_cli/dep_ensure.py")
    tui_launcher = _text("hermes_cli/main.py")
    nix_checks = _text("nix/checks.nix")
    nix_package = _text("nix/hermes-agent.nix")
    nixos_module = _text("nix/nixosModules.nix")

    assert int(_text(".nvmrc").strip()) == major
    assert int(
        _required_match(
            r"^FROM node:(\d+)-bookworm-slim@sha256:",
            dockerfile,
            "Dockerfile",
        ).group(1)
    ) == major
    assert f"nodejs_{major}," in nix_package
    assert f'nodejs = nodejs_{major};' in nix_package
    assert f'test "$NODE_MAJOR" -ge {major}' in nix_checks
    assert f"https://deb.nodesource.com/node_{major}.x" in nixos_module
    assert re.search(rf"^NODE_MIN_MAJOR\s*=\s*{major}$", dep_ensure, re.MULTILINE)
    assert "_node_is_supported(node)" in tui_launcher


def test_installers_provision_and_require_the_root_node_major() -> None:
    major = _node_major()
    install_sh = _text("scripts/install.sh")
    install_ps1 = _text("scripts/install.ps1")
    node_bootstrap = _text("scripts/lib/node-bootstrap.sh")

    sh_version = _required_match(
        r'^NODE_VERSION="(\d+)"$',
        install_sh,
        "scripts/install.sh",
    )
    ps_version = _required_match(
        r'^\$NodeVersion = "(\d+)"$',
        install_ps1,
        "scripts/install.ps1",
    )

    assert int(sh_version.group(1)) == major
    assert int(ps_version.group(1)) == major
    assert f'HERMES_NODE_MIN_VERSION="${{HERMES_NODE_MIN_VERSION:-{major}}}"' in node_bootstrap
    assert f'HERMES_NODE_TARGET_MAJOR="${{HERMES_NODE_TARGET_MAJOR:-{major}}}"' in node_bootstrap
    assert '[ "$major" -ge "$NODE_VERSION" ]' in install_sh
    assert "$v.Major -ge [int]$NodeVersion" in install_ps1


@pytest.mark.parametrize("doc", MANAGED_NODE_DOCS)
def test_managed_node_docs_name_the_installer_node_major(doc: str) -> None:
    major = _node_major()
    docs = _text(doc)

    assert re.search(rf"\bNode(?:\.js)?\s+v?{major}\b", docs)
    minimum_majors = [
        int(match.group(1))
        for match in re.finditer(r"\bNode(?:\.js)?\s+v?(\d+)\+", docs)
    ]
    assert not minimum_majors or min(minimum_majors) >= major
