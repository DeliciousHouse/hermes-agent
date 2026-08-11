"""Keep every project Node runtime on the same major version."""

from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_node_runtime_major_is_consistent() -> None:
    nvmrc = REPO_ROOT / ".nvmrc"
    assert nvmrc.is_file(), ".nvmrc must declare the canonical Node major"
    node_major = int(nvmrc.read_text(encoding="utf-8").strip())

    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    docker_major = int(re.search(r"^FROM node:(\d+)-", dockerfile, re.MULTILINE).group(1))

    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    engine_major = int(re.fullmatch(r">=(\d+)(?:\.0\.0)?", package["engines"]["node"]).group(1))

    package_lock = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    lock_engine_major = int(
        re.fullmatch(r">=(\d+)(?:\.0\.0)?", package_lock["packages"][""]["engines"]["node"]).group(1)
    )

    installer = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    installer_major = int(re.search(r'^NODE_VERSION="(\d+)"', installer, re.MULTILINE).group(1))

    nix_package = (REPO_ROOT / "nix" / "hermes-agent.nix").read_text(encoding="utf-8")
    nix_parameter_major = int(re.search(r"^  nodejs_(\d+),$", nix_package, re.MULTILINE).group(1))
    nix_alias_major = int(re.search(r"^  nodejs = nodejs_(\d+);$", nix_package, re.MULTILINE).group(1))

    nixos_module = (REPO_ROOT / "nix" / "nixosModules.nix").read_text(encoding="utf-8")
    nixos_node_source_major = int(re.search(r"nodesource\.com/node_(\d+)\.x", nixos_module).group(1))

    workflow_majors = {
        int(match)
        for workflow in (REPO_ROOT / ".github" / "workflows").glob("*.yml")
        for match in re.findall(r"node-version:\s*['\"]?(\d+)", workflow.read_text(encoding="utf-8"))
    }

    assert docker_major == node_major
    assert engine_major == node_major
    assert lock_engine_major == node_major
    assert installer_major == node_major
    assert nix_parameter_major == node_major
    assert nix_alias_major == node_major
    assert nixos_node_source_major == node_major
    assert workflow_majors == {node_major}
