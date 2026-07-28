from types import SimpleNamespace

import pytest


@pytest.fixture
def main_mod(monkeypatch):
    import hermes_cli.main as module

    monkeypatch.setattr(
        module,
        "_node_is_supported",
        lambda _path=None: True,
        raising=False,
    )
    return module


def test_tui_finds_bundled_entry_js(tmp_path):
    """_find_bundled_tui finds entry.js bundled in the package."""
    tui_dist = tmp_path / "hermes_cli" / "tui_dist"
    tui_dist.mkdir(parents=True)
    entry = tui_dist / "entry.js"
    entry.write_text("// bundled TUI", encoding="utf-8")

    from hermes_cli.main import _find_bundled_tui
    result = _find_bundled_tui(hermes_cli_dir=tmp_path / "hermes_cli")
    assert result is not None
    assert result.name == "entry.js"


def test_tui_returns_none_when_no_bundle(tmp_path):
    """_find_bundled_tui returns None when no bundle exists."""
    from hermes_cli.main import _find_bundled_tui
    result = _find_bundled_tui(hermes_cli_dir=tmp_path / "hermes_cli")
    assert result is None


def test_dev_external_bundle_rejects_before_resolving_node(
    tmp_path, main_mod, monkeypatch, capsys
):
    monkeypatch.setenv("HERMES_TUI_DIR", str(tmp_path))
    monkeypatch.setattr(main_mod, "_ensure_tui_node", lambda: None)

    def unexpected_node_check(_path=None):
        raise AssertionError("node should not be resolved for an invalid --dev launch")

    monkeypatch.setattr(main_mod, "_node_is_supported", unexpected_node_check)
    monkeypatch.setattr(main_mod.shutil, "which", lambda name: f"/bin/{name}")

    with pytest.raises(SystemExit):
        main_mod._make_tui_argv(tmp_path, tui_dev=True)

    assert "--dev is incompatible with HERMES_TUI_DIR" in capsys.readouterr().err


def test_ensure_tui_node_bootstraps_when_discovered_node_is_too_old(
    tmp_path, main_mod, monkeypatch
):
    helper = tmp_path / "scripts" / "lib" / "node-bootstrap.sh"
    helper.parent.mkdir(parents=True)
    helper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    calls = []

    monkeypatch.setattr(main_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(main_mod, "_node_is_supported", lambda _path=None: False)
    monkeypatch.setattr(main_mod.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        main_mod.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command)
        or SimpleNamespace(returncode=0, stdout=""),
    )

    main_mod._ensure_tui_node()

    assert calls
    assert calls[0][:2] == ["bash", "-c"]
    assert str(helper) in calls[0][2]


def test_tui_rejects_unsupported_hermes_node(
    tmp_path, main_mod, monkeypatch, capsys
):
    entry = tmp_path / "dist" / "entry.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("console.log('tui')", encoding="utf-8")
    old_node = tmp_path / "node"
    old_node.write_text("#!/bin/sh\n", encoding="utf-8")
    old_node.chmod(0o755)

    monkeypatch.setenv("HERMES_TUI_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_NODE", str(old_node))
    monkeypatch.setenv("HERMES_SKIP_NODE_BOOTSTRAP", "1")
    monkeypatch.setattr(main_mod, "_node_is_supported", lambda _path=None: False)
    monkeypatch.setattr(main_mod.shutil, "which", lambda name: f"/bin/{name}")

    with pytest.raises(SystemExit):
        main_mod._make_tui_argv(tmp_path, tui_dev=False)

    assert "Node.js 24" in capsys.readouterr().out


def test_skip_node_bootstrap_does_not_fall_through_to_installer(
    tmp_path, main_mod, monkeypatch
):
    monkeypatch.setenv("HERMES_SKIP_NODE_BOOTSTRAP", "1")
    monkeypatch.setattr(main_mod, "_node_is_supported", lambda _path=None: False)
    monkeypatch.setattr(main_mod.shutil, "which", lambda _name: None)

    def unexpected_install(*_args, **_kwargs):
        raise AssertionError("skip bootstrap must disable every automatic Node installer")

    monkeypatch.setattr(main_mod, "_ensure_node_dependency", unexpected_install)

    assert main_mod._node_bin("node") is None


def test_supported_npm_bin_rejects_npm_backed_by_unsupported_node(
    main_mod, monkeypatch
):
    monkeypatch.setattr(
        main_mod.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(main_mod, "_node_is_supported", lambda _path=None: False)

    assert main_mod._supported_npm_bin() is None

    monkeypatch.setattr(main_mod, "_node_is_supported", lambda _path=None: True)
    assert main_mod._supported_npm_bin() == "/usr/bin/npm"
