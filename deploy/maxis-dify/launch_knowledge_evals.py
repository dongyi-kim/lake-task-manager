#!/usr/bin/env python3
"""Guard one explicitly launched DEV MAXIS evaluation without reading API keys.

Invoking this launcher is the process-local network handoff used by the existing
evaluation-launcher threat model. It prevents accidental direct execution; it is not
hostile same-user authorization. The launcher validates the configured DEV destination,
performs one credential-free bounded GET, then gives the marker only to its child.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.maxis import profiles  # noqa: E402
from app.agent.maxis.destination_policy import (  # noqa: E402
    canonical_maxis_api_base,
)
from app.agent.maxis.knowledge_eval import DEFAULT_PILOT_CASE  # noqa: E402
from tools.agent_eval_isolation import NETWORK_PREFLIGHT_MARKER  # noqa: E402


MAXIS_EVAL_DESTINATION_IDENTITY_ENV = (
    "LTM_MAXIS_EVAL_DESTINATION_IDENTITY_SHA256"
)
RUNNER = ROOT / "deploy" / "maxis-dify" / "run_knowledge_evals.py"


class LaunchError(RuntimeError):
    """A content-free preflight failure safe to show on the command line."""

    def __init__(self, category: str):
        self.category = str(category or "preflight")
        super().__init__(
            f"MAXIS evaluation launch failed (category={self.category}); "
            "no evaluation cases started"
        )


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Authorize and run the synthetic-only DEV MAXIS/Dify Knowledge evaluation."
        )
    )
    parser.add_argument(
        "--pilot-case",
        default=DEFAULT_PILOT_CASE,
        help="Positive governed-document case sent as the mandatory pilot.",
    )
    parser.add_argument(
        "--pilot-only",
        action="store_true",
        help="Run only the mandatory one-case identity pilot.",
    )
    return parser.parse_args(argv)


def credential_free_dev_api_base() -> str:
    """Resolve only non-secret DEV destination configuration."""

    try:
        profile = profiles.get("dev")
        if (
            profile.get("name") != "dev"
            or profile.get("stage") != "dev"
            or profile.get("data_policy") != "synthetic_only"
        ):
            raise ValueError("invalid dev profile")
        raw_base = profile.get("base_url")
        if str(os.getenv("LAKE_MAXIS_PROFILE") or "").strip().lower() == "dev":
            raw_base = (
                str(os.getenv(profiles.CONFIG_ENV_NAMES["base_url"]) or "").strip()
                or raw_base
            )
        return canonical_maxis_api_base(
            str(raw_base or ""),
            stage="dev",
            require_https=False,
        )
    except Exception:
        raise LaunchError("configuration") from None


def _bounded_timeout() -> float:
    try:
        return max(
            1.0,
            min(float(os.getenv("LTM_MAXIS_EVAL_PREFLIGHT_TIMEOUT", "10")), 60.0),
        )
    except (TypeError, ValueError):
        raise LaunchError("configuration") from None


def _probe_dev_endpoint(base_url: str, *, timeout: float) -> str:
    """Perform a body-free, credential-free reachability probe."""

    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                "GET",
                f"{base_url}/parameters",
                headers={"Accept": "application/json"},
            ) as response:
                status = int(response.status_code)
    except httpx.TimeoutException:
        raise LaunchError("timeout") from None
    except httpx.TransportError:
        raise LaunchError("connection") from None
    except Exception:
        raise LaunchError("preflight") from None
    # An unauthenticated Dify endpoint normally returns 401/403. Any bounded
    # non-redirect client response proves endpoint reachability without a key.
    if not (200 <= status < 500) or 300 <= status < 400:
        raise LaunchError("status")
    return hashlib.sha256(base_url.encode("utf-8")).hexdigest()


def _run_runner(args: argparse.Namespace, child_env: dict[str, str]) -> int:
    command = [
        sys.executable,
        "-B",
        "-X",
        "utf8",
        "-u",
        str(RUNNER),
        "--profile",
        "dev",
        "--pilot-case",
        args.pilot_case,
    ]
    if args.pilot_only:
        command.append("--pilot-only")
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=child_env,
        check=False,
    )
    return int(completed.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    child_env: dict[str, Any] = dict(os.environ)
    # Never trust an inherited handoff. The child receives a fresh marker only
    # after this launcher's own allowlist and reachability checks succeed.
    child_env.pop(NETWORK_PREFLIGHT_MARKER, None)
    child_env.pop(MAXIS_EVAL_DESTINATION_IDENTITY_ENV, None)
    try:
        base_url = credential_free_dev_api_base()
        destination_identity = _probe_dev_endpoint(
            base_url,
            timeout=_bounded_timeout(),
        )
        child_env[NETWORK_PREFLIGHT_MARKER] = "1"
        child_env[MAXIS_EVAL_DESTINATION_IDENTITY_ENV] = destination_identity
        return _run_runner(args, child_env)
    except LaunchError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except OSError:
        print(
            "MAXIS evaluation launch failed (category=runner); "
            "no evaluation cases started",
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
