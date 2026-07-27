"""Route-script idempotency disposition contracts."""

from hermes_constants import get_hermes_home
from gateway.platforms.webhook_filters import WebhookRouteProcessor


def _write_script(name: str, source: str):
    path = get_hermes_home() / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_missing_script_is_retryable_operational_failure():
    result = WebhookRouteProcessor().run_route_script("missing.py", {})
    assert result == (False, None, True)


def test_nonzero_script_exit_is_intentional_consuming_ignore():
    _write_script("nonzero.py", "raise SystemExit(7)\n")
    result = WebhookRouteProcessor().run_route_script("nonzero.py", {})
    assert result == (False, None, False)


def test_silent_script_output_is_intentional_consuming_ignore():
    _write_script("silent.py", "print('[SILENT]')\n")
    result = WebhookRouteProcessor().run_route_script("silent.py", {})
    assert result == (False, None, False)