"""以独立pytest进程检验钩子，避免把内部故意失败当作主套件失败。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("stage", ["setup", "call", "teardown", "both"])
def test_failure_stage_is_preserved_and_secrets_are_redacted(tmp_path, stage):
    probe = tmp_path / "test_probe.py"
    probe.write_text(f'''
import pytest
from test_support.diagnostics import record_request
@pytest.fixture
def resource():
    record_request({{"headers": {{"Authorization": "Bearer PRIVATE_PROBE"}}, "response": {{"token": "TOKEN_PROBE"}}}})
    if {stage!r} == "setup":
        raise ValueError("setup probe")
    yield
    if {stage!r} in ("teardown", "both"):
        raise RuntimeError("teardown probe")
def test_probe(resource):
    assert {stage!r} not in ("call", "both"), "intentional probe"
''', encoding="utf-8")
    output = tmp_path / "diagnostics"
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), TEST_ARTIFACT_DIR=str(output))
    result = subprocess.run([sys.executable, "-m", "pytest", str(probe), "-p", "test_support.pytest_plugin", "-q", "-o", "addopts=", "--tb=short"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    files = list(output.glob("case-*.json"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    assert "PRIVATE_PROBE" not in raw and "TOKEN_PROBE" not in raw
    data = json.loads(raw)
    failed = [p["phase"] for p in data["phases"] if p["outcome"] == "failed"]
    assert failed == (["call", "teardown"] if stage == "both" else [stage])
