"""Launch one synthetic Editor component suite through the DEV network boundary."""
from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True, choices=('baseline', 'candidate'))
    args = parser.parse_args(argv)
    env = dict(os.environ)
    env.pop(NETWORK_PREFLIGHT_MARKER, None)
    env.pop(MAXIS_EVAL_DESTINATION_IDENTITY_ENV, None)
    try:
        base = credential_free_dev_api_base()
        identity = _probe_dev_endpoint(base, timeout=_bounded_timeout())
        env[NETWORK_PREFLIGHT_MARKER] = '1'
        env[MAXIS_EVAL_DESTINATION_IDENTITY_ENV] = identity
        return subprocess.run([
            sys.executable, '-B', '-X', 'utf8', '-u',
            str(ROOT / 'deploy/maxis-dify/run_editor_evals.py'), '--label', args.label,
        ], cwd=ROOT, env=env, check=False).returncode
    except (LaunchError, OSError):
        print('MAXIS editor evaluation preflight failed; no cases started', file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
