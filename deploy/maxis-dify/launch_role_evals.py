"""Launch bounded, repository-fixture-only DEV Role evaluation after preflight."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from launch_knowledge_evals import (
    LaunchError, MAXIS_EVAL_DESTINATION_IDENTITY_ENV, NETWORK_PREFLIGHT_MARKER,
    _bounded_timeout, _probe_dev_endpoint, credential_free_dev_api_base,
)

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    from run_role_evals import arguments
    args = arguments(argv)  # Validate fixed case selection before any network call.
    env = dict(os.environ)
    env.pop(NETWORK_PREFLIGHT_MARKER, None)
    env.pop(MAXIS_EVAL_DESTINATION_IDENTITY_ENV, None)
    try:
        identity = _probe_dev_endpoint(credential_free_dev_api_base(), timeout=_bounded_timeout())
        env.update({NETWORK_PREFLIGHT_MARKER: '1', MAXIS_EVAL_DESTINATION_IDENTITY_ENV: identity})
        command = [sys.executable, '-B', '-X', 'utf8', '-u',
                   str(ROOT / 'deploy/maxis-dify/run_role_evals.py'), '--label', args.label]
        if args.cases:
            command += ['--cases', *args.cases]
        return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
    except (LaunchError, OSError):
        print('MAXIS Role evaluation preflight failed; no cases started', file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
