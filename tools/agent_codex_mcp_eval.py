"""Entry adapter for `agent_eval_launcher.py codex-mcp`; direct runs never open sockets."""
from tools.codex_mcp_eval.runner import launch


def main(argv=None):
    return launch(argv)


if __name__ == "__main__":
    raise SystemExit("Use tools/agent_eval_launcher.py codex-mcp (network preflight required)")
