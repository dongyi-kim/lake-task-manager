"""Network-authorized launcher for the five modern real-provider Agent batteries.

Direct runner execution is intentionally network-closed. Run this launcher from a shell with
the required LAN/Internet permission; it checks the configured endpoint first and hands a
short-lived process marker to the child only after success.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.agent_eval_isolation import (
    EFFECTIVE_PROVIDER_IDENTITY_ENV,
    EvaluationPreflightError,
    NETWORK_PREFLIGHT_MARKER,
    REAL_LLM_PROVIDERS,
    _failure_category,
    _probe_real_provider,
    _provider_context,
)
from tools.agent_scenario_eval import (
    configure_model_routing,
    parse_scenario_args,
    validate_eval_argv,
)

RUNNERS = {
    "conversation": ("agent_lang_ab.py", "gpt-4o-mini"),
    "create": ("agent_create_suite.py", "gpt-4o-mini"),
    "compose": ("agent_compose_eval.py", "gpt-4o-mini"),
    "meeting": ("agent_meeting_eval.py", "gpt-4o"),
    "context": ("agent_context_change_eval.py", "gpt-4o"),
    "user-review": ("agent_user_review.py", "gpt-4o-mini"),
}
STRUCTURED_FALLBACK_POLICY_ENV = "LTM_AGENT_STRUCTURED_OUTPUT_FALLBACK"
USAGE = (
    "usage: python -X utf8 tools/agent_eval_launcher.py "
    "{conversation|create|compose|meeting|context|user-review} "
    "[model] [case ...] [--out PATH]"
)


def _simple_model() -> str:
    # Mirror the five existing runners. configure_model_routing translates this semantic
    # simple choice to the active provider's environment key.
    provider = str(os.getenv("LAKE_AGENT_PROVIDER") or "openai").strip().lower()
    provider_key = {
        "openai": "LAKE_AGENT_OPENAI_CHAT_SIMPLE",
        "openai_compat": "LAKE_AGENT_COMPAT_CHAT_SIMPLE",
        "aoai": "LAKE_AGENT_AOAI_CHAT_SIMPLE",
    }.get(provider, "LAKE_AGENT_OPENAI_CHAT_SIMPLE")
    return str(
        os.getenv(provider_key)
        or os.getenv("LAKE_AGENT_OPENAI_CHAT_SIMPLE")
        or "gpt-4o-mini"
    )


def _authorize_network(model: str, simple_model: str, timeout: float) -> str:
    """Probe without trusting an inherited handoff marker; never expose raw provider errors."""
    os.environ.pop(NETWORK_PREFLIGHT_MARKER, None)
    os.environ.setdefault("LAKE_AGENT_PROVIDER", "openai")
    # Custom runners historically read this neutral handoff key before provider routing.
    os.environ["LAKE_AGENT_OPENAI_CHAT_SIMPLE"] = simple_model
    provider = configure_model_routing(model, simple_model)
    try:
        routed_provider, identity = _provider_context()
        if routed_provider != provider:
            raise RuntimeError("provider routing mismatch")
        if provider == "fake":
            return hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if provider not in REAL_LLM_PROVIDERS:
            raise RuntimeError("unsupported provider")
        _probe_real_provider(provider, timeout)
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise RuntimeError("invalid provider identity")
        return identity
    except Exception as exc:
        raise EvaluationPreflightError(_failure_category(exc), provider) from None


def _run_runner(script: str, argv: Sequence[str]) -> int:
    command = [sys.executable, "-X", "utf8", "-u", str(ROOT / "tools" / script), *argv]
    completed = subprocess.run(command, cwd=ROOT, env=dict(os.environ), check=False)
    return int(completed.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw or any(arg in {"-h", "--help"} for arg in raw):
        print(USAGE)
        return 0 if raw else 2

    runner_name, runner_args = raw[0].strip().lower(), raw[1:]
    definition = RUNNERS.get(runner_name)
    if definition is None:
        print(f"unknown evaluation runner: {runner_name}\n{USAGE}", file=sys.stderr)
        return 2
    validate_eval_argv(runner_args)
    script, default_model = definition
    model, _selected, _out = parse_scenario_args(runner_args, default_model=default_model)
    simple_model = _simple_model()
    try:
        timeout = max(1.0, min(float(os.getenv("LTM_EVAL_PREFLIGHT_TIMEOUT", "10")), 60.0))
    except ValueError:
        print("invalid LTM_EVAL_PREFLIGHT_TIMEOUT; evaluation not started", file=sys.stderr)
        return 2
    prior_fallback_policy = os.environ.get(STRUCTURED_FALLBACK_POLICY_ENV)
    # An evaluation candidate must be the backend recorded in its metadata. Production may
    # fall back before spending a wire call, but a benchmark cannot silently relabel legacy
    # parsing as Instructor.
    os.environ[STRUCTURED_FALLBACK_POLICY_ENV] = "forbid"
    try:
        try:
            effective_provider_identity = _authorize_network(model, simple_model, timeout)
        except EvaluationPreflightError as exc:
            print(str(exc), file=sys.stderr)
            return 3

        # This marker is intentionally created here, after the endpoint check, and inherited
        # only by the selected child process. Direct runner invocations never manufacture it.
        os.environ[NETWORK_PREFLIGHT_MARKER] = "1"
        prior_provider_identity = os.environ.get(EFFECTIVE_PROVIDER_IDENTITY_ENV)
        os.environ[EFFECTIVE_PROVIDER_IDENTITY_ENV] = effective_provider_identity
        try:
            return _run_runner(script, runner_args)
        finally:
            os.environ.pop(NETWORK_PREFLIGHT_MARKER, None)
            if prior_provider_identity is None:
                os.environ.pop(EFFECTIVE_PROVIDER_IDENTITY_ENV, None)
            else:
                os.environ[EFFECTIVE_PROVIDER_IDENTITY_ENV] = prior_provider_identity
    finally:
        if prior_fallback_policy is None:
            os.environ.pop(STRUCTURED_FALLBACK_POLICY_ENV, None)
        else:
            os.environ[STRUCTURED_FALLBACK_POLICY_ENV] = prior_fallback_policy


if __name__ == "__main__":
    raise SystemExit(main())
