"""Small operational CLI for LTM model probes and benchmarks."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent import capabilities, config, profiles  # noqa: E402
from app.agent.model_profiles import resolve  # noqa: E402


def config_id(value: str) -> str:
    if not value:
        return str((profiles.active() or {}).get("id") or "")
    row = profiles.get(value) or next((x for x in profiles.list_all()
                                       if str(x.get("name") or "").casefold() == value.casefold()), None)
    if not row:
        raise SystemExit(f"설정을 찾을 수 없습니다: {value}")
    return row["id"]


def save(kind: str, target: str, payload: dict) -> Path:
    out = ROOT / ".cache" / "agent-evaluation" / "llm-probes"
    out.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in (target or "active"))
    path = out / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe}-{kind}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def probe(target: str) -> None:
    cid = config_id(target)
    chat = config.chat_definition(config_id=cid)
    effective = resolve(chat.model, chat.provider, "balanced",
                        explicit_model_profile=chat.model_profile)
    started = time.perf_counter()
    result = config.probe(config_id=cid)
    payload = {"target": target or cid, "createdAt": datetime.now(timezone.utc).isoformat(),
               "elapsedS": round(time.perf_counter() - started, 3), "chat": chat.debug(),
               "effective": effective.debug(),
               "embedding": config.embedding_definition(cid).debug(),
               "embeddingIdentity": config.embedding_identity(cid), "result": result}
    path = save("probe", target or cid, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2)); print(f"saved: {path}")
    if not result.get("ok"):
        raise SystemExit(1)


def benchmark(target: str) -> None:
    cid = config_id(target)
    rows = []
    for task_profile in ("fast_structured", "balanced", "reasoning"):
        for chars in (512, 5_000, 16_000, 24_000):
            prompt = ("LTM benchmark input. " * ((chars // 21) + 1))[:chars]
            started = time.perf_counter()
            try:
                answer = config.get_llm(config_id=cid, profile=task_profile,
                                        max_tokens=128).invoke(prompt)
                rows.append({"profile": task_profile, "inputChars": chars, "ok": True,
                             "latencyS": round(time.perf_counter() - started, 3),
                             "outputChars": len(str(getattr(answer, "content", answer) or ""))})
            except Exception as exc:
                rows.append({"profile": task_profile, "inputChars": chars, "ok": False,
                             "latencyS": round(time.perf_counter() - started, 3),
                             "error": str(exc)[:500]})
    payload = {"target": target or cid, "createdAt": datetime.now(timezone.utc).isoformat(), "rows": rows}
    path = save("benchmark", target or cid, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2)); print(f"saved: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="ltm")
    groups = parser.add_subparsers(dest="group", required=True)
    llm = groups.add_parser("llm")
    commands = llm.add_subparsers(dest="command", required=True)
    for name in ("probe", "benchmark"):
        command = commands.add_parser(name)
        command.add_argument("target", nargs="?", default="", help="named config id or name; default active")
    args = parser.parse_args()
    if args.group == "llm" and args.command == "probe":
        probe(args.target)
    elif args.group == "llm" and args.command == "benchmark":
        benchmark(args.target)


if __name__ == "__main__":
    main()
