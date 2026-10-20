"""PROD-local reviewer key/signature utility; no private key is held by the web app."""
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.maxis import qualification as q  # noqa: E402


def cache_output(value: str) -> Path:
    path = Path(value).resolve()
    # An operator must deliberately select an ignored local location, never sources.
    if not any(part in {".cache", ".tmp", ".test"} for part in path.parts):
        raise ValueError("qualification_output_requires_cache")
    if path.exists():
        raise ValueError("qualification_output_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def create_key(output: str) -> dict:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from app.agent.maxis.credentials import WindowsDpapiBackend
    path = cache_output(output)
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")
    fingerprint, _ = q.public_key_identity(public)
    encrypted = WindowsDpapiBackend().protect(key.private_bytes_raw())
    result = {"publicKey": public, "fingerprint": fingerprint, "keyId": fingerprint}
    with path.open("x", encoding="utf-8") as stream:
        json.dump({**result, "encryptedPrivateKey": base64.b64encode(encrypted).decode("ascii")},
                  stream, ensure_ascii=False)
    return result


def sign(report_path: str, review_path: str, key_path: str, output: str) -> dict:
    from app.agent.maxis import profiles
    from app.agent.maxis.credentials import WindowsDpapiBackend
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    path = cache_output(output)
    report = q.read_json(Path(report_path))
    report = report.get("report", report)
    review = q.read_json(Path(review_path))
    stored = q.read_json(Path(key_path))
    key = Ed25519PrivateKey.from_private_bytes(WindowsDpapiBackend().unprotect(
        base64.b64decode(stored["encryptedPrivateKey"], validate=True)))
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")
    fingerprint, _ = q.public_key_identity(public)
    payload = {"report": report, "review": review,
               "expiresAt": (q._instant(report["completedAt"]) + timedelta(days=7)).isoformat().replace("+00:00", "Z")}
    envelope = {"contract": q.CONTRACT, "keyId": fingerprint, "payload": payload,
                "signature": base64.b64encode(key.sign(q.canonical(payload))).decode("ascii")}
    # Requires prior independent public-key enrollment. No registration side effect.
    result = q.verify(envelope, profiles.resolve("prod"))
    with path.open("x", encoding="utf-8") as stream:
        json.dump(envelope, stream, ensure_ascii=False, allow_nan=False)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    key = commands.add_parser("reviewer-key")
    key.add_argument("--output", required=True)
    signer = commands.add_parser("sign")
    for field in ("report", "review", "key", "output"):
        signer.add_argument("--" + field, required=True)
    runner = commands.add_parser("run")
    runner.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "reviewer-key":
            result = create_key(args.output)
        elif args.command == "sign":
            result = sign(args.report, args.review, args.key, args.output)
        else:
            from app.agent.maxis.qualification_runs import execute
            execute(args.id)
            return 0
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        # Credentials, provider responses and local paths never enter CLI diagnostics.
        print("Qualification failed. Check the local report, reviewer enrollment and completed review.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
