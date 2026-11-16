"""验收工具的证据必须包含真实状态，不能只信HTTP200。"""
from types import SimpleNamespace
from unittest.mock import MagicMock
import json

import pytest


def test_acceptance_check_rejects_false_evidence():
    from tools.verify_e2 import record_check
    evidence = []
    with pytest.raises(AssertionError):
        record_check(evidence, 'probe', False, {'observed': 'pending'})
    assert evidence == [{'name': 'probe', 'passed': False, 'observed': 'pending'}]


def test_connection_uses_manifest_database_not_process_default(monkeypatch):
    from tools.verify_e2 import connect_bound
    from api.common import db_util
    connect = MagicMock()
    monkeypatch.setattr(db_util, 'connect', connect)
    entry = {'env': {'DB_NAME': 'owned_env'}}
    connect_bound(entry)
    assert connect.call_args.kwargs['database'] == 'owned_env'


def test_absence_probe_reports_existing_database_and_same_process(monkeypatch):
    from tools import verify_e2
    from tools import environments
    import psutil
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (1,)
    monkeypatch.setattr(environments, 'database_connection', lambda env: connection)
    monkeypatch.setattr(verify_e2, 'load_environment', lambda: {})
    monkeypatch.setattr(psutil, 'pid_exists', lambda pid: True)
    monkeypatch.setattr(psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 100, is_running=lambda: True))
    manifest = {'environments': [{'worker': 'gw0', 'env': {'DB_NAME': 'owned'}, 'service_pid': 123, 'service_created_at': 100}]}
    assert verify_e2.verify_absent(manifest) == [{'worker': 'gw0', 'database_absent': False, 'service_absent': False}]
    assert cursor.execute.call_args.args[1] == ('owned',)
    connection.close.assert_called_once()


@pytest.mark.parametrize('absent', [True, False])
def test_fault_verdict_cannot_pass_with_residual_resources(tmp_path, monkeypatch, absent):
    from tools import verify_e2
    monkeypatch.setattr(verify_e2, 'load_environment', lambda: {})
    args = SimpleNamespace(fault='port', run_id='probe', output_dir=tmp_path)
    def fake_main(argv):
        # Initial allocation, retry, second environment allocation.
        for _ in range(3):
            verify_e2.run_tests.free_port()
        output = tmp_path / 'probe'
        output.mkdir()
        (output / 'resources.json').write_text(json.dumps({'environments': [], 'errors': []}))
        return 0
    monkeypatch.setattr(verify_e2.run_tests, 'main', fake_main)
    monkeypatch.setattr(verify_e2, 'verify_absent', lambda manifest: [
        {'worker': 'gw0', 'database_absent': absent, 'service_absent': True}])
    if absent:
        assert verify_e2.run_fault(args) == 0
        assert (tmp_path / 'probe' / 'fault-evidence.json').exists()
    else:
        with pytest.raises(AssertionError, match='owned_resources_absent'):
            verify_e2.run_fault(args)
