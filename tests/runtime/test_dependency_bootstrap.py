"""Dependency repair handles absent modules, broken installations and false success."""

import json
import os
import subprocess
import sys

import pytest

from support.paths import REPO_ROOT


@pytest.mark.parametrize("scenario,expected_calls", [
    ("healthy", 0), ("missing", 1), ("damaged", 2),
    ("install_failure", 1), ("false_success", 2),
])
def test_dotenv_bootstrap(tmp_path, scenario, expected_calls):
    # Extract the real function without importing run/settings: importing those
    # modules can migrate caches from the user's application roots.
    script = r'''
import ast
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from types import ModuleType, SimpleNamespace

root, cache, scenario = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
settings = ModuleType("app.infra.settings")
settings.APP_ROOT, settings.CACHE_DIR = root, cache
for name in ("app", "app.infra"):
    sys.modules[name] = ModuleType(name)
sys.modules[settings.__name__] = settings
tree = ast.parse((root / "run.py").read_text(encoding="utf-8"))
function = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "_ensure_deps")
namespace = {"Path": Path, "sys": sys, "__file__": str(root / "run.py")}
exec(compile(ast.Module(body=[function], type_ignores=[]), "run.py", "exec"), namespace)

original_finders = list(sys.meta_path)
class WithoutDotenv:
    find_distributions = importlib.machinery.PathFinder.find_distributions

    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        if fullname == "dotenv" or fullname.startswith("dotenv."):
            return None
        return importlib.machinery.PathFinder.find_spec(fullname, path, target)

if scenario != "healthy":
    sys.meta_path = [importlib.machinery.BuiltinImporter,
                     importlib.machinery.FrozenImporter, WithoutDotenv]
    assert importlib.util.find_spec("dotenv") is None
    try:
        import pydantic_settings
    except ModuleNotFoundError as exc:
        assert exc.name == "dotenv", exc
    else:
        raise AssertionError("missing dotenv did not reach the upstream settings dependency")

if scenario == "missing":
    distribution = metadata.distribution
    def without_metadata(name):
        if name == "python-dotenv":
            raise metadata.PackageNotFoundError(name)
        return distribution(name)
    metadata.distribution = without_metadata
else:
    assert metadata.distribution("python-dotenv")  # broken files, intact dist-info

calls = []
original_run = subprocess.run
def install(command, *args, **kwargs):
    if command[:3] != [sys.executable, "-m", "pip"]:
        return original_run(command, *args, **kwargs)
    calls.append(command)
    assert kwargs == {"timeout": 1800}
    assert command[:5] == [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if "--force-reinstall" in command:
        assert command[5:] == ["--force-reinstall", "--no-deps", "python-dotenv==1.2.2"]
    else:
        assert command[-2:] == ["-r", str(root / "requirements.txt")]
    if scenario in {"missing", "damaged"}:
        sys.meta_path = original_finders
    return SimpleNamespace(returncode=int(scenario == "install_failure"))

subprocess.run = install
namespace["_ensure_deps"]()
namespace["_ensure_deps"]()
failed = scenario in {"install_failure", "false_success"}
assert (cache / ".deps-attempt").exists() == failed
if not failed:
    import pydantic_settings
    assert pydantic_settings.BaseSettings
if scenario in {"damaged", "false_success"}:
    assert "--force-reinstall" in calls[0]
    assert calls[1][-2] == "-r"
assert "run" not in sys.modules
assert sys.modules["app.infra.settings"] is settings
print(json.dumps({"installs": len(calls)}))
'''
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", script,
         str(REPO_ROOT), str(tmp_path), scenario],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"installs": expected_calls}
    if scenario in {"install_failure", "false_success"}:
        assert "누락 패키지 설치 완료" not in result.stdout
