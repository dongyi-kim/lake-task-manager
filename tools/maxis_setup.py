"""Prepare PROD imports and connect through an already running local LTM API."""
from __future__ import annotations

import argparse
import getpass
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit
import warnings
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
SETUP = "/api/agent/maxis/setup"
STATUS = "/api/agent/maxis/status"
RUNTIME = "/api/agent/runtime/status"
CREDENTIALS = "/api/agent/maxis/credentials/prod"
PROBE = "/api/agent/maxis/probe/prod"
WORKFLOWS = ("agent_core", "editor_author", "pmo_brief")
MODEL_KEYS = ("plugin_unique_identifier", "provider", "name")
ASSET_KEYS = ("runId", "baseUrl", "datasetId", "sentinelDatasetId",
              "roleMetadataFieldId", "sentinelDocumentId", "documentIds", "model")
LOG_KEYS = ("remoteLogPolicyId", "remoteLogMode", "remoteLogRetentionDays")
CONFIG_KEYS = {*ASSET_KEYS, "workflowVersions", *LOG_KEYS}
MAX_JSON = 2 * 1024 * 1024
MAX_ZIP = 16 * 1024 * 1024


class SetupError(Exception):
    """Only fixed, content-free messages may cross this CLI's error boundary."""


def require(condition, message):
    if not condition:
        raise SetupError(message)


class LocalAPI:
    def __init__(self, port):
        require(1 <= port <= 65535, "로컬 LTM 포트를 확인하세요.")
        self.base = f"http://127.0.0.1:{port}"
        self.client = httpx.Client(trust_env=False, follow_redirects=False,
                                  timeout=httpx.Timeout(180, connect=3))

    def request(self, method, path, body=None, *, binary=False):
        allowed = {("GET", SETUP), ("GET", STATUS), ("GET", RUNTIME),
                   ("PUT", SETUP + "/host"), ("PUT", CREDENTIALS), ("POST", PROBE),
                   *(("POST", SETUP + "/" + name) for name in ("knowledge", "bundle", "model", "candidate"))}
        download = method == "GET" and re.fullmatch(
            re.escape(SETUP) + r"/download/[0-9a-f]{32}/(knowledge|workflows)", path)
        require((method, path) in allowed or download, "허용되지 않은 로컬 API 경로입니다.")
        maximum = MAX_ZIP if binary else MAX_JSON
        chunks, size, deadline = [], 0, time.monotonic() + 180
        try:
            with self.client.stream(method, self.base + path, json=body) as response:
                if not 200 <= response.status_code < 300:
                    hint = {
                        404: "이 포트의 앱에서 요청 경로를 찾지 못했습니다. 구버전 또는 다른 체크아웃의 "
                             "LTM인지 확인하고, 기존 앱을 정상 종료한 뒤 이 스크립트와 같은 앱 저장소에서 "
                             "LTM을 재시작하세요. MAXIS/Dify 키 오류가 아닙니다.",
                        403: "로컬 LTM에서 Jira 로그인과 매니저 권한을 확인하세요.",
                    }.get(response.status_code, "로컬 LTM 설정과 실행 로그를 확인하세요.")
                    # Only the already-allowlisted local route is diagnostic output.
                    # Response bodies and redirect destinations may contain secrets.
                    raise SetupError(f"로컬 LTM API 요청 실패 (HTTP {response.status_code}): "
                                     f"{method} {self.base}{path}\n{hint}")
                for chunk in response.iter_bytes(chunk_size=65536):
                    size += len(chunk)
                    require(size <= maximum and time.monotonic() <= deadline,
                            "로컬 LTM API 응답 크기 또는 시간 제한을 초과했습니다.")
                    chunks.append(chunk)
        except httpx.HTTPError:
            raise SetupError("로컬 LTM API에 연결하지 못했습니다. 실행 상태와 포트를 확인하세요.") from None
        raw = b"".join(chunks)
        if binary:
            require(zipfile.is_zipfile(io.BytesIO(raw)), "준비 파일 응답이 ZIP 형식이 아닙니다.")
            return raw
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise SetupError("로컬 LTM API 응답 형식을 확인하세요.") from None
        require(isinstance(result, dict) and result.get("ok") is not False,
                "로컬 LTM API 작업이 실패했습니다. LTM 설정에서 상태를 확인하세요.")
        if method != "GET":
            require(result.get("ok") is True, "로컬 LTM API가 작업 완료를 확인하지 않았습니다.")
        return result


def cache_path(value):
    candidate = Path(value)
    require(".." not in candidate.parts, "입력과 출력 경로는 저장소 .cache 아래여야 합니다.")
    candidate = candidate if candidate.is_absolute() else ROOT / candidate
    cache = ROOT / ".cache"
    require(candidate.is_relative_to(cache) and candidate != cache,
            "입력과 출력 경로는 저장소 .cache 아래여야 합니다.")
    for part in (candidate, *candidate.parents):
        if part == ROOT:
            break
        require(":" not in part.name, "파일 경로 형식을 확인하세요.")
        if part.exists() or part.is_symlink():
            attributes = getattr(part.lstat(), "st_file_attributes", 0)
            require(not part.is_symlink() and not attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                    ".cache 파일 경로에 링크 또는 junction을 사용할 수 없습니다.")
    require(candidate.resolve().is_relative_to(cache.resolve()), ".cache 외부 경로는 사용할 수 없습니다.")
    return candidate


def read_file(path, maximum=MAX_JSON):
    with path.open("rb") as source:
        raw = source.read(maximum + 1)
    require(len(raw) <= maximum, "입력 파일 크기 제한을 초과했습니다.")
    return raw


def read_config(path):
    try:
        value = json.loads(read_file(path))
    except (ValueError, UnicodeError):
        raise SetupError("PROD 입력 JSON 형식을 확인하세요.") from None
    require(isinstance(value, dict) and set(value) == CONFIG_KEYS,
            "PROD 입력 JSON 필드를 확인하세요. token·비밀번호·추가 필드는 허용하지 않습니다.")
    model = value["model"]
    require(isinstance(model, dict) and set(MODEL_KEYS) <= set(model)
            and not set(model) - set(MODEL_KEYS) - {"completion_params", "output_mode"}
            and all(isinstance(model[key], str) for key in MODEL_KEYS)
            and ("output_mode" not in model or model["output_mode"] in ("native_schema", "guarded_json", "structured_work"))
            and ("completion_params" not in model or isinstance(model["completion_params"], dict)),
            "모델 입력 필드를 확인하세요. completion_params는 공개 모델 옵션의 JSON 객체입니다.")
    versions = value["workflowVersions"]
    require(isinstance(versions, dict) and set(versions) == set(WORKFLOWS)
            and all(isinstance(item, str) for item in versions.values()),
            "Workflow 입력 필드를 확인하세요.")
    require(isinstance(value["documentIds"], dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in value["documentIds"].items()),
            "문서 ID 입력 필드를 확인하세요.")
    require(all(isinstance(value[key], str) for key in (*ASSET_KEYS[:6], *LOG_KEYS[:2]))
            and type(value["remoteLogRetentionDays"]) is int
            and 0 <= value["remoteLogRetentionDays"] <= 90
            and re.fullmatch(r"[0-9a-f]{32}", value["runId"]),
            "PROD 입력 값의 형식을 확인하세요.")
    return value


def store(path, raw, *, replace=False):
    if path.exists():
        if read_file(path, MAX_ZIP) == raw:
            return
        require(replace, "기존 ZIP 내용이 다릅니다. 확인 후 --replace-files로 다시 실행하세요.")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Only completed files replace an explicitly approved existing output.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as target:
            temporary = Path(target.name)
            target.write(raw)
        if replace:
            os.replace(temporary, path)
        else:
            with path.open("xb") as target:
                target.write(raw)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def manager(api):
    result = api.request("GET", STATUS)
    require(result.get("canManage") is True, "현재 Jira 매니저로 LTM에 로그인한 뒤 실행하세요.")
    require(not result.get("environmentError"), "LTM의 MAXIS 환경 설정 오류를 먼저 해결하세요.")
    return result


def current_run(api, config):
    state = api.request("GET", SETUP)
    run = state.get("currentRun") or {}
    require(run.get("sourceCurrent") is True and run.get("runId") == config["runId"]
            and run.get("sourceDigest") == (state.get("source") or {}).get("sourceDigest"),
            "준비 소스 또는 runId가 다릅니다. 확인 후 prepare --refresh-source로 갱신하세요.")
    require(set(config["documentIds"]) == {row["id"] for row in run["documents"]},
            "정본 문서 목록이 다릅니다. prepare --refresh-source로 갱신하세요.")
    return state, run


def base_url(value):
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    path = parsed.path.rstrip("/") or "/v1"
    require(parsed.scheme == "https" and parsed.port in (None, 443)
            and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
            and re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", host)
            and not re.fullmatch(r"[0-9.]+", host)
            and path.endswith("/v1") and ".." not in path and "%" not in path
            and not any(char.isspace() or char == "\\" for char in value),
            "PROD Service API는 인증정보 없는 HTTPS FQDN과 /v1 경로여야 합니다.")
    return urlunsplit(("https", host.lower(), path, "", ""))


def download(api, result, kind, replace):
    run_id = result.get("runId", "")
    require(isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{32}", run_id),
            "준비 실행 식별자 형식을 확인하세요.")
    expected = SETUP + f"/download/{run_id}/{kind}"
    require(result.get("downloadUrl") == expected, "준비 파일 다운로드 경로가 일치하지 않습니다.")
    output = cache_path(f".cache/maxis/{kind}.zip")
    store(output, api.request("GET", expected, binary=True), replace=replace)
    return output


def prepare(api, args, path, config):
    manager(api)
    if config and not args.refresh_source:
        current_run(api, config)
    result = api.request("POST", SETUP + "/knowledge", {})
    ids = [row["id"] for row in result["documents"]]
    require(ids and all(isinstance(key, str) and re.fullmatch(r"[a-z0-9_-]+", key) for key in ids),
            "정본 문서 목록 형식을 확인하세요.")
    if config and not args.refresh_source:
        require(result.get("runId") == config["runId"], "준비 실행이 변경되었습니다. 다시 확인하세요.")
    output = download(api, result, "knowledge", args.replace_files)
    if not config:
        config = {key: "" for key in (*ASSET_KEYS[:6], *LOG_KEYS[:2])}
        config.update(model=dict.fromkeys(MODEL_KEYS, ""), workflowVersions=dict.fromkeys(WORKFLOWS, ""),
                      remoteLogRetentionDays=0, documentIds={})
        config["model"]["completion_params"] = {}
    if not path.exists() or args.refresh_source:
        config = {**config, "runId": result["runId"],
                  "documentIds": {key: config["documentIds"].get(key, "") for key in ids}}
        store(path, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
              replace=args.refresh_source)
    print(f"Knowledge 준비 완료\nZIP: {output}\n설정 JSON: {path}")
    print("위 ZIP을 압축 해제해 현지 Dify에 등록하고, 설정 JSON에 실제 ID를 기록하세요.")


def assets(config, model=None):
    body = {key: config[key] for key in ASSET_KEYS}
    body["baseUrl"] = base_url(config["baseUrl"])
    body["model"] = model or config["model"]
    require(all(body[key] for key in ASSET_KEYS[2:6]) and all(body["documentIds"].values())
            and set(MODEL_KEYS) <= set(body["model"])
            and not set(body["model"]) - set(MODEL_KEYS) - {"completion_params", "output_mode"}
            and all(body["model"].get(key) for key in MODEL_KEYS),
            "실제 PROD 자산 ID와 승인된 모델 식별자를 입력하세요.")
    return body


def bundle(api, args, config):
    require(args.confirm_host, "PROD 주소를 확인하고 bundle --confirm-host로 실행하세요.")
    manager(api)
    _, run = current_run(api, config)
    model = None
    if args.model_file:
        yaml_text = read_file(cache_path(args.model_file)).decode("utf-8-sig")
        model = api.request("POST", SETUP + "/model", {"yamlText": yaml_text})["model"]
        if "output_mode" in config["model"]:
            model = {**model, "output_mode": config["model"]["output_mode"]}
        require(not any(config["model"].get(key) for key in MODEL_KEYS) or config["model"] == model,
                "입력 JSON 모델과 내보내기 모델이 다릅니다. 모델 입력을 확인하세요.")
    body = assets(config, model)
    api.request("PUT", SETUP + "/host", {"baseUrl": body["baseUrl"], "confirmed": True})
    if run.get("bundleReady") is True and run.get("assetInputs") == body and not args.replace_files:
        result = {**run, "downloadUrl": SETUP + f"/download/{config['runId']}/workflows"}
    else:
        result = api.request("POST", SETUP + "/bundle", body)
    require(result.get("runId") == config["runId"], "준비 실행이 변경되었습니다. 다시 확인하세요.")
    output = download(api, result, "workflows", args.replace_files)
    print(f"Workflow 준비 완료\nZIP: {output}\n현지 Dify에서 import·게시 후 Workflow UUID를 기록하세요.")


def check_bundle(api, config):
    state, run = current_run(api, config)
    prior = run.get("assetInputs") or {}
    require((prior.get("model") or {}).get("output_mode", "native_schema")
            == config["model"].get("output_mode", "native_schema"),
            "모델 출력 형식이 준비 파일과 다릅니다. bundle을 다시 생성하세요.")
    expected = assets(config, prior.get("model") if not any(config["model"].get(key) for key in MODEL_KEYS) else None)
    require(run.get("bundleReady") is True and run.get("baseUrl") == expected["baseUrl"]
            and urlsplit(expected["baseUrl"]).hostname in state.get("registeredHosts", [])
            and prior == expected, "등록 호스트·입력값·Workflow 준비 파일이 다릅니다. bundle을 다시 확인하세요.")
    return run


def check_candidate(status, config, run):
    profile = next((row for row in status.get("profiles", []) if row.get("name") == "prod"), {})
    expected = {"baseUrl": base_url(config["baseUrl"]), "knowledgeDatasetId": config["datasetId"],
                "workflowBundleVersion": run["workflowBundleVersion"], "knowledgeVersion": run["knowledgeVersion"],
                "knowledgeSentinelSha256": run["knowledgeSentinelSha256"],
                **{key: config[key] for key in LOG_KEYS}}
    overrides = status.get("environmentOverrides") or {}
    require(run.get("candidateSaved") is True and not profile.get("configError")
            and all(profile.get(key) == value for key, value in expected.items())
            and all(profile.get("effective" + key[0].upper() + key[1:]) == expected[key]
                    for key in ("baseUrl", "knowledgeDatasetId", "knowledgeSentinelSha256", *LOG_KEYS))
            and all((profile.get("workflows", {}).get(key) or {}).get("version", "")
                    == config["workflowVersions"][key] for key in WORKFLOWS)
            and not any(overrides.get(key) for key in ("workflowVersions", "workflowBundleVersion", "knowledgeVersion")),
            "저장된 PROD 후보 또는 환경 override가 입력과 다릅니다. LTM 설정을 확인하세요.")


def connect(api, args, config):
    require(args.confirm_remote, "실제 canary LLM 호출에 동의하면 connect --confirm-remote로 실행하세요.")
    require(sys.stdin.isatty(), "토큰 입력에는 대화형 터미널이 필요합니다.")
    manager(api)
    check_bundle(api, config)
    versions = {key: value for key, value in config["workflowVersions"].items() if value}
    require({"agent_core", "editor_author"} <= set(versions)
            and config["remoteLogPolicyId"] and config["remoteLogMode"] in {"redacted", "approved_bounded_content"},
            "Core·Editor 게시 Workflow UUID와 실제 승인된 로그 정책을 입력하세요.")
    api.request("POST", SETUP + "/candidate",
                {"runId": config["runId"], "workflowVersions": versions, **{key: config[key] for key in LOG_KEYS}})
    check_candidate(manager(api), config, check_bundle(api, config))
    patch = {}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            for key, field in zip(WORKFLOWS, ("agentCoreApiKey", "editorAuthorApiKey", "pmoBriefApiKey")):
                if key in versions:
                    token = getpass.getpass(f"{key} Service API token (Enter: 기존 값 유지): ").strip()
                    if token:
                        patch[field] = token
        # Recheck after a human may have spent time in the token prompts.
        check_candidate(manager(api), config, check_bundle(api, config))
        if patch:
            api.request("PUT", CREDENTIALS, patch)
    finally:
        patch.clear()
    api.request("POST", PROBE, {})
    print("PROD 연결 확인 완료. 운영 자격 검증과 적용은 LTM 설정에서 별도로 진행하세요.")


def status(api):
    state = api.request("GET", STATUS)
    runtime = api.request("GET", RUNTIME)
    profile = next((row for row in state.get("profiles", []) if row.get("name") == "prod"), {})
    verified = profile.get("verified") is True
    print("PROD 연결 확인: " + ("통과" if verified else "미완료"))
    for feature, label in (("conversation", "대화"), ("editor", "편집기")):
        row = runtime.get(feature) or {}
        ready = row.get("ready") is True and row.get("engine") == "maxis" and row.get("profile") == "prod"
        print("PROD " + label + " 준비: " + ("완료" if ready else "미완료"))
    print("다음 단계: " + ("LTM 설정에서 운영 자격 검증·적용 상태 확인" if verified else "PROD 준비·게시 후 connect --confirm-remote"))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "명령 또는 옵션을 확인하세요. --help를 사용하고 토큰은 명령행에 입력하지 마세요.\n")


def main(argv=None):
    parser = Parser(description="실행 중인 로컬 LTM을 통한 PROD MAXIS 준비·연결")
    subparsers = parser.add_subparsers(dest="command")
    for command in ("prepare", "bundle", "connect", "status"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--port", type=int, default=4457)
        if command != "status":
            sub.add_argument("--config", default=".cache/maxis/prod-setup.json")
        if command in {"prepare", "bundle"}:
            sub.add_argument("--replace-files", action="store_true", help="내용이 다른 준비 ZIP 교체")
        if command == "prepare":
            sub.add_argument("--refresh-source", action="store_true", help="입력을 보존하며 runId·문서 목록 갱신")
        if command == "bundle":
            sub.add_argument("--confirm-host", action="store_true")
            sub.add_argument("--model-file")
        if command == "connect":
            sub.add_argument("--confirm-remote", action="store_true", help="실제 canary LLM 요청 승인")
    args = parser.parse_args(argv)
    api = None
    try:
        config, path = None, None
        if args.command in {"prepare", "bundle", "connect"}:
            path = cache_path(args.config)
            require(path.suffix.lower() == ".json", "PROD 입력 파일은 .cache 아래 JSON이어야 합니다.")
            config = read_config(path) if path.exists() else None
            require(config is not None or args.command == "prepare", "먼저 prepare로 PROD 입력 파일을 생성하세요.")
        api = LocalAPI(getattr(args, "port", 4457))
        if args.command == "prepare":
            prepare(api, args, path, config)
        elif args.command == "bundle":
            bundle(api, args, config)
        elif args.command == "connect":
            connect(api, args, config)
        else:
            status(api)
        return 0
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError, EOFError, getpass.GetPassWarning, KeyboardInterrupt):
        print("PROD 준비를 중단했습니다. 입력 파일·터미널·LTM 상태를 확인하세요.", file=sys.stderr)
    finally:
        if api:
            api.client.close()
    if args.command == "prepare":
        print("Knowledge 준비 실패: 압축 해제를 진행하지 마세요. 위의 첫 오류를 해결한 뒤 prepare를 다시 실행하세요. "
              "기존 ZIP이 있어도 이번 준비가 성공한 것은 아닙니다.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
