"""The PROD setup CLI only uses bounded local APIs; never real MAXIS services."""
from __future__ import annotations

import copy
import io
import json
import zipfile
from types import SimpleNamespace

import httpx
import pytest

from tools import maxis_setup as cli


RUN = "a" * 32
MODEL = {"plugin_unique_identifier": "approved/model:1", "provider": "approved/model/provider", "name": "model"}
UUID = "11111111-1111-4111-8111-111111111111"


class FakeAPI:
    def __init__(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("README.txt", "synthetic")
        self.zip = stream.getvalue()
        self.calls = []
        self.source = {"sourceDigest": "b" * 64, "documents": [{"id": "guide"}]}
        self.run = None
        self.hosts = []
        self.profile = {"name": "prod", "verified": False, "lastProbe": {}}
        self.can_manage = True
        self.fail = ""
        self.overrides = {}

    def __call__(self, request):
        path = request.url.path
        data = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, data))
        assert request.url.host == "127.0.0.1"
        assert request.url.port in {4457, 4458}
        if path == self.fail:
            return httpx.Response(200, json={"ok": False, "error": "app-" + "SECRET" * 10})
        if path in self.overrides:
            return self.overrides[path](request)
        if path == cli.SETUP:
            result = {"source": self.source, "currentRun": self.run, "registeredHosts": self.hosts}
        elif path == cli.STATUS:
            result = {"canManage": self.can_manage, "profiles": [self.profile], "environmentOverrides": {}}
        elif path == cli.RUNTIME:
            result = {"conversation": {"ready": False}, "editor": {"ready": False}}
        elif path == cli.SETUP + "/knowledge":
            if not self.run:
                self.run = {**copy.deepcopy(self.source), "runId": RUN, "sourceCurrent": True,
                            "bundleReady": False, "candidateSaved": False,
                            "workflowBundleVersion": "bundle", "knowledgeVersion": "knowledge",
                            "knowledgeSentinelSha256": "c" * 64}
            result = {"ok": True, **self.run, "downloadUrl": cli.SETUP + f"/download/{RUN}/knowledge"}
        elif "/download/" in path:
            return httpx.Response(200, content=self.zip)
        elif path == cli.SETUP + "/model":
            result = {"ok": True, "model": MODEL}
        elif path == cli.SETUP + "/host":
            self.hosts = ["prod.example"]
            result = {"ok": True, "baseUrl": data["baseUrl"], "host": "prod.example"}
        elif path == cli.SETUP + "/bundle":
            self.run.update(bundleReady=True, baseUrl=data["baseUrl"], assetInputs=data)
            result = {"ok": True, **self.run, "downloadUrl": cli.SETUP + f"/download/{RUN}/workflows"}
        elif path == cli.SETUP + "/candidate":
            self.run["candidateSaved"] = True
            self.profile = {
                "name": "prod", "baseUrl": self.run["baseUrl"], "effectiveBaseUrl": self.run["baseUrl"],
                "workflowBundleVersion": "bundle", "knowledgeVersion": "knowledge",
                "knowledgeDatasetId": UUID, "effectiveKnowledgeDatasetId": UUID,
                "knowledgeSentinelSha256": "c" * 64, "effectiveKnowledgeSentinelSha256": "c" * 64,
                "workflows": {key: {"version": data["workflowVersions"].get(key, "")} for key in cli.WORKFLOWS},
                **{key: value for key, value in data.items() if key.startswith("remoteLog")},
                **{"effective" + key[0].upper() + key[1:]: value
                   for key, value in data.items() if key.startswith("remoteLog")},
            }
            result = {"ok": True, **self.run}
        elif path == cli.CREDENTIALS:
            result = {"ok": True}
        elif path == cli.PROBE:
            self.profile.update(verified=True, lastProbe={"ok": True})
            result = {"ok": True}
        else:
            pytest.fail("Unexpected endpoint: " + path)
        return httpx.Response(200, json=result)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_: "app-" + "SECRET" * 10)
    api = FakeAPI()
    client = httpx.Client

    def local_client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        assert kwargs["timeout"].connect <= 5
        return client(**kwargs, transport=httpx.MockTransport(api))

    monkeypatch.setattr(cli.httpx, "Client", local_client)
    return api


def configured(fake):
    assert cli.main(["prepare"]) == 0
    path = cli.ROOT / ".cache/maxis/prod-setup.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config.update(baseUrl="https://prod.example/v1", datasetId=UUID, sentinelDatasetId=UUID,
                  roleMetadataFieldId=UUID, sentinelDocumentId=UUID, documentIds={"guide": UUID},
                  model=MODEL, workflowVersions={"agent_core": UUID, "editor_author": UUID, "pmo_brief": ""},
                  remoteLogPolicyId="approved-policy", remoteLogMode="redacted", remoteLogRetentionDays=0)
    path.write_text(json.dumps(config, indent=3), encoding="utf-8")
    return path, config


def test_full_lifecycle_preserves_input_and_skips_optional_pmo(fake, capsys):
    path, _ = configured(fake)
    original = path.read_bytes()
    assert cli.main(["prepare"]) == 0
    assert cli.main(["bundle", "--confirm-host"]) == 0
    assert cli.main(["connect", "--confirm-remote"]) == 0
    assert cli.main([]) == 0
    assert path.read_bytes() == original
    assert (cli.ROOT / ".cache/maxis/knowledge.zip").read_bytes() == fake.zip
    assert (cli.ROOT / ".cache/maxis/workflows.zip").read_bytes() == fake.zip
    candidate = next(body for _, path, body in fake.calls if path.endswith("/candidate"))
    assert set(candidate["workflowVersions"]) == {"agent_core", "editor_author"}
    credentials = next(body for _, path, body in fake.calls if path == cli.CREDENTIALS)
    assert set(credentials) == {"agentCoreApiKey", "editorAuthorApiKey"}
    assert all(not any(word in path for word in ("activate", "apply", "qualification", "console")) for _, path, _ in fake.calls)
    output = capsys.readouterr()
    assert "SECRET" not in output.out + output.err


@pytest.mark.parametrize("change", ["source", "run"])
def test_prepare_refuses_stale_input_without_mutation(fake, change):
    path, _ = configured(fake)
    original = path.read_bytes()
    if change == "source":
        fake.run["sourceCurrent"] = False
    else:
        fake.run["runId"] = "d" * 32
    fake.calls.clear()
    assert cli.main(["prepare", "--replace-files"]) == 1
    assert path.read_bytes() == original
    assert all(method == "GET" for method, _, _ in fake.calls)


def test_zip_conflict_requires_explicit_replace_and_keeps_json(fake):
    path, _ = configured(fake)
    original = path.read_bytes()
    target = cli.ROOT / ".cache/maxis/knowledge.zip"
    target.write_bytes(b"older download")
    assert cli.main(["prepare"]) == 1
    assert target.read_bytes() == b"older download"
    assert cli.main(["prepare", "--replace-files"]) == 0
    assert target.read_bytes() == fake.zip
    assert path.read_bytes() == original


@pytest.mark.parametrize("endpoint", ["/candidate", "/credentials/prod", "/probe/prod"])
def test_ok_false_is_failure_and_never_echoes_response(fake, capsys, endpoint):
    configured(fake)
    assert cli.main(["bundle", "--confirm-host"]) == 0
    fake.fail = cli.SETUP + endpoint if endpoint == "/candidate" else "/api/agent/maxis" + endpoint
    fake.calls.clear()
    assert cli.main(["connect", "--confirm-remote"]) == 1
    assert fake.calls[-1][1] == fake.fail
    output = capsys.readouterr()
    assert "SECRET" not in output.out + output.err


def test_manager_confirmation_and_tty_are_required_before_writes(fake, monkeypatch):
    configured(fake)
    fake.calls.clear()
    assert cli.main(["bundle"]) == 1
    assert cli.main(["connect"]) == 1
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert cli.main(["connect", "--confirm-remote"]) == 1
    assert all(method == "GET" for method, _, _ in fake.calls)
    fake.can_manage = False
    assert cli.main(["prepare"]) == 1
    assert all(method == "GET" for method, _, _ in fake.calls)


def test_redirect_is_not_followed_or_printed(fake, capsys):
    fake.overrides[cli.STATUS] = lambda _: httpx.Response(
        307, headers={"Location": "https://other.example/?token=SECRET"})
    assert cli.main(["prepare"]) == 1
    assert len(fake.calls) == 1
    assert "SECRET" not in capsys.readouterr().err


def test_config_extra_fields_and_paths_are_rejected(fake):
    path, config = configured(fake)
    config["token"] = "SECRET"
    path.write_text(json.dumps(config), encoding="utf-8")
    fake.calls.clear()
    assert cli.main(["prepare"]) == 1
    assert cli.main(["prepare", "--config", ".cache/../outside.json"]) == 1
    assert cli.main(["prepare", "--config", "outside.json"]) == 1
    assert not fake.calls


def test_model_export_is_only_sent_to_local_extractor_and_not_saved(fake):
    path, config = configured(fake)
    config["model"] = dict.fromkeys(MODEL, "")
    path.write_text(json.dumps(config), encoding="utf-8")
    export = cli.ROOT / ".cache/maxis/prod-model.yml"
    export.write_text("unrelated-secret: SECRET\n", encoding="utf-8")
    original = path.read_bytes()
    assert cli.main(["bundle", "--confirm-host", "--model-file", ".cache/maxis/prod-model.yml"]) == 0
    assert fake.run["assetInputs"]["model"] == MODEL
    assert path.read_bytes() == original
    assert cli.main(["connect", "--confirm-remote"]) == 0


@pytest.mark.parametrize("params", [{}, {"max_completion_tokens": 2048, "reasoning_effort": "low"}])
def test_explicit_model_options_survive_cli_without_becoming_required_identity(fake, params):
    path, config = configured(fake)
    config["model"] = {**MODEL, "completion_params": params}
    path.write_text(json.dumps(config), encoding="utf-8")
    assert cli.main(["bundle", "--confirm-host"]) == 0
    assert fake.run["assetInputs"]["model"]["completion_params"] == params
    assert cli.main(["connect", "--confirm-remote"]) == 0


def test_prepare_starts_with_server_model_defaults(fake):
    assert cli.main(["prepare"]) == 0
    config = json.loads((cli.ROOT / ".cache/maxis/prod-setup.json").read_text(encoding="utf-8"))
    assert config["model"] == {**dict.fromkeys(cli.MODEL_KEYS, ""), "completion_params": {}}

def test_empty_getpass_preserves_existing_keys(fake, monkeypatch):
    configured(fake)
    assert cli.main(["bundle", "--confirm-host"]) == 0
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_: "")
    fake.calls.clear()
    assert cli.main(["connect", "--confirm-remote"]) == 0
    assert not any(path == cli.CREDENTIALS for _, path, _ in fake.calls)
    assert any(path == cli.PROBE for _, path, _ in fake.calls)


@pytest.mark.parametrize("change", ["host", "base", "bundle", "candidate"])
def test_mismatch_prevents_secret_transmission(fake, change):
    configured(fake)
    assert cli.main(["bundle", "--confirm-host"]) == 0
    if change == "host":
        fake.hosts = ["different.example"]
    elif change == "base":
        fake.run["baseUrl"] = "https://different.example/v1"
    elif change == "bundle":
        fake.run["assetInputs"]["datasetId"] = "22222222-2222-4222-8222-222222222222"
    else:
        fake.overrides[cli.STATUS] = lambda _: httpx.Response(
            200, json={"canManage": True, "profiles": [{"name": "prod", "baseUrl": "https://different.example/v1"}]})
    fake.calls.clear()
    assert cli.main(["connect", "--confirm-remote"]) == 1
    assert not any(path in {cli.CREDENTIALS, cli.PROBE} for _, path, _ in fake.calls)


def test_refresh_preserves_edits_and_updates_only_run_and_document_keys(fake):
    path, config = configured(fake)
    fake.run = None
    fake.source = {"sourceDigest": "d" * 64, "documents": [{"id": "guide"}, {"id": "new_guide"}]}
    config["documentIds"]["removed"] = UUID
    config["runId"] = "f" * 32
    path.write_text(json.dumps(config), encoding="utf-8")
    assert cli.main(["prepare", "--refresh-source", "--replace-files"]) == 0
    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated == {**config, "runId": RUN, "documentIds": {"guide": UUID, "new_guide": ""}}


@pytest.mark.parametrize("mode", ["external", "traversal", "oversize"])
def test_untrusted_responses_stop_before_download_or_persistence(fake, mode):
    if mode == "oversize":
        fake.overrides[cli.STATUS] = lambda _: httpx.Response(200, content=b" " * (cli.MAX_JSON + 1))
    else:
        location = ("https://evil.example/SECRET" if mode == "external"
                    else cli.SETUP + "/download/../../SECRET/knowledge")
        fake.overrides[cli.SETUP + "/knowledge"] = lambda _: httpx.Response(
            200, json={"ok": True, "runId": RUN, "documents": [{"id": "guide"}], "downloadUrl": location})
    assert cli.main(["prepare"]) == 1
    assert not (cli.ROOT / ".cache/maxis/prod-setup.json").exists()
    assert not any("/download/" in path for _, path, _ in fake.calls)


def test_symlink_component_rejected_before_network(fake, monkeypatch):
    from pathlib import Path
    linked = cli.ROOT / ".cache/maxis"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == linked or original(self))
    assert cli.main(["prepare"]) == 1
    assert not fake.calls


def test_getpass_echo_fallback_cannot_store_or_probe(fake, monkeypatch):
    import warnings
    configured(fake)
    assert cli.main(["bundle", "--confirm-host"]) == 0

    def unsafe_prompt(*_):
        warnings.warn("SECRET", cli.getpass.GetPassWarning)
        return "SECRET"

    monkeypatch.setattr(cli.getpass, "getpass", unsafe_prompt)
    fake.calls.clear()
    assert cli.main(["connect", "--confirm-remote"]) == 1
    assert not any(path in {cli.CREDENTIALS, cli.PROBE} for _, path, _ in fake.calls)


def test_explicit_port_and_configured_pmo(fake):
    path, config = configured(fake)
    config["workflowVersions"]["pmo_brief"] = UUID
    path.write_text(json.dumps(config), encoding="utf-8")
    assert cli.main(["bundle", "--confirm-host", "--port", "4458"]) == 0
    assert cli.main(["connect", "--confirm-remote", "--port", "4458"]) == 0
    body = next(body for _, path, body in fake.calls if path == cli.CREDENTIALS)
    assert "pmoBriefApiKey" in body


@pytest.mark.parametrize("engine,profile", [("local", ""), ("maxis", "dev"), ("maxis", "prod")])
def test_status_reports_prod_readiness_only(fake, capsys, engine, profile):
    fake.overrides[cli.RUNTIME] = lambda _: httpx.Response(
        200, json={key: {"ready": True, "engine": engine, "profile": profile} for key in ("conversation", "editor")})
    assert cli.main(["status"]) == 0
    output = capsys.readouterr().out
    expected = "완료" if profile == "prod" else "미완료"
    assert f"PROD 대화 준비: {expected}" in output
    assert f"PROD 편집기 준비: {expected}" in output


def test_unknown_token_option_is_not_echoed(capsys):
    with pytest.raises(SystemExit):
        cli.main(["connect", "--token", "SECRET"])
    assert "SECRET" not in capsys.readouterr().err


def test_bundle_rerun_reuses_existing_bytes_and_candidate(fake):
    configured(fake)
    assert cli.main(["bundle", "--confirm-host"]) == 0
    assert cli.main(["connect", "--confirm-remote"]) == 0
    fake.calls.clear()
    assert cli.main(["bundle", "--confirm-host"]) == 0
    assert fake.run["candidateSaved"]
    assert not any(path == cli.SETUP + "/bundle" for _, path, _ in fake.calls)
    assert cli.main(["bundle", "--confirm-host", "--replace-files"]) == 0
    assert any(path == cli.SETUP + "/bundle" for _, path, _ in fake.calls)


def test_bundle_version_override_stops_token_transmission(fake):
    configured(fake)
    assert cli.main(["bundle", "--confirm-host"]) == 0
    fake.overrides[cli.STATUS] = lambda _: httpx.Response(200, json={
        "canManage": True, "profiles": [fake.profile],
        "environmentOverrides": {"workflowBundleVersion": "LAKE_MAXIS_WORKFLOW_BUNDLE_VERSION"}})
    fake.calls.clear()
    assert cli.main(["connect", "--confirm-remote"]) == 1
    assert not any(path in {cli.CREDENTIALS, cli.PROBE} for _, path, _ in fake.calls)
