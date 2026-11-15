"""E2运行器的新约束；不连接外部服务。"""
import json
import pytest
from tools import run_tests as r


def test_default_worker_policy_and_argument_guard():
    assert r.worker_count('unit', None) == 1
    assert r.worker_count('all', None) == 2
    assert r.worker_count('api', 4) == 4
    for extras in [['-n', '3'], ['--numprocesses=3'], ['--output=x'], ['-o', 'addopts=-n 4'], ['--base-url=x']]:
        with pytest.raises(ValueError):
            r.validate_extras(extras)


def test_binding_happens_without_importing_application(tmp_path, monkeypatch):
    from test_support.environment_plugin import bind_environment
    manifest = tmp_path / 'resources.json'
    manifest.write_text(json.dumps({'environments': [{'worker': 'gw1', 'env': {'DB_NAME': 'mine', 'BASE_URL': 'http://mine', 'TEST_ENVIRONMENT_ID': 'identity', 'TEST_ARTIFACT_DIR': str(tmp_path)}}]}))
    for key in ['DB_NAME', 'BASE_URL', 'TEST_ENVIRONMENT_ID', 'TEST_ARTIFACT_DIR', 'TEST_WORKER_ID']:
        monkeypatch.setenv(key, 'original')
    monkeypatch.setenv('TEST_ENVIRONMENT_MANIFEST', str(manifest))
    monkeypatch.setenv('PYTEST_XDIST_WORKER', 'gw1')
    bind_environment()
    import os
    assert os.environ['DB_NAME'] == 'mine'
    monkeypatch.delenv('PYTEST_XDIST_WORKER')
    monkeypatch.setenv('TEST_PARALLEL_WORKERS', '2')
    bind_environment()
    assert 'DB_NAME' not in os.environ


def test_two_workers_record_distinct_resources_and_cleanup_partial_init(tmp_path, monkeypatch):
    import tools.run_tests as r
    from types import SimpleNamespace
    created, dropped, started, commands = [], [], [], []
    monkeypatch.setattr(r, 'load_environment', lambda: {})
    monkeypatch.setattr(r, 'create_database', lambda env: created.append(env['DB_NAME']))
    monkeypatch.setattr(r, 'drop_database', lambda env: dropped.append(env['DB_NAME']))
    monkeypatch.setattr(r, 'start_service', lambda env, log: started.append(env.copy()) or SimpleNamespace(pid=None))
    monkeypatch.setattr(r, 'wait_for_service', lambda *a: None)
    monkeypatch.setattr(r, 'stop_process', lambda p: None)
    def execute(cmd, env, **kw):
        commands.append((cmd, env.copy()))
        return 0
    monkeypatch.setattr(r, 'execute', execute)
    assert r.main(['--suite', 'api', '--run-id', 'two', '--output-dir', str(tmp_path)]) == 0
    summary = json.loads((tmp_path / 'two' / 'resources.json').read_text())
    assert len(set(created)) == len(set(dropped)) == len(started) == 2
    assert created == list(reversed(dropped))
    assert started[0]['TEST_ENVIRONMENT_ID'] != started[1]['TEST_ENVIRONMENT_ID']
    cmd = commands[-1][0]
    assert cmd[cmd.index('-n') + 1] == '2'
    assert '--max-worker-restart=0' in cmd
    assert all(e['cleanup'] == {'service': 'stopped', 'database': 'removed'} for e in summary['environments'])


def test_recovery_rejects_pid_reuse_without_deleting_database(tmp_path, monkeypatch):
    from tools import recover_run as recovery
    from types import SimpleNamespace
    path = tmp_path / 'resources.json'
    path.write_text(json.dumps({'run_id': 'internal', 'environments': [{
        'worker': 'gw0', 'env': {'DB_NAME': 'shophub_test_' + 'a' * 32, 'TEST_RUN_ID': 'internal',
        'TEST_ENVIRONMENT_ID': 'one', 'BASE_URL': 'http://127.0.0.1:4321'},
        'service_pid': 123, 'service_created_at': 100,
        'cleanup': {'service': 'pending', 'database': 'pending'}}]}))
    monkeypatch.setattr(recovery.lifecycle, 'load_environment', lambda: {})
    monkeypatch.setattr(recovery.psutil, 'pid_exists', lambda pid: True)
    monkeypatch.setattr(recovery.psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 101,
        cmdline=lambda: ['python', '-m', 'uvicorn', 'server.main:app', '--port', '4321']))
    monkeypatch.setattr(recovery.lifecycle, 'database_connection', lambda env: pytest.fail('PID mismatch must block deletion'))
    assert recovery.recover(path) == 2
    assert json.loads(path.read_text())['environments'][0]['cleanup']['database'] == 'pending'


@pytest.mark.parametrize('argument', ['--tx=popen', '--tx', '--px=ssh=host', '--numprocesses=4', '-n4', '-oaddopts=-n4', '--override-ini=addopts=-n4'])
def test_override_forms_are_rejected(argument):
    with pytest.raises(ValueError):
        r.validate_extras([argument])


def test_subprocess_conftest_import_sees_binding_before_application(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    env = os.environ.copy()
    env.pop('PYTEST_XDIST_WORKER', None)
    manifest = tmp_path / 'manifest.json'
    bound = {'DB_NAME': 'binding_before_import', 'BASE_URL': 'http://127.0.0.1:43210',
             'TEST_ENVIRONMENT_ID': 'bound', 'TEST_ARTIFACT_DIR': str(tmp_path / 'diagnostics')}
    manifest.write_text(json.dumps({'environments': [{'worker': 'main', 'env': bound}]}))
    env.update(TEST_ENVIRONMENT_MANIFEST=str(manifest), TEST_PARALLEL_WORKERS='1',
               DB_NAME='incorrect_inherited_database', BASE_URL='http://incorrect')
    (tmp_path / 'conftest.py').write_text(
        'import os, json\nfrom pathlib import Path\n'
        'assert os.environ["DB_NAME"] == "binding_before_import"\n'
        'assert os.environ["BASE_URL"] == "http://127.0.0.1:43210"\n'
        'from server import db\nassert db._POOL is None\n'
        'Path(__file__).with_name("import_evidence.json").write_text(json.dumps({k: os.environ[k] for k in ["DB_NAME", "BASE_URL"]}))\n')
    (tmp_path / 'test_import.py').write_text('def test_import():\n    assert True\n')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'test_support.environment_plugin',
        str(tmp_path / 'test_import.py'), '--rootdir', str(tmp_path), '-o', 'addopts=', '-q'],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / 'import_evidence.json').read_text()) == {k: bound[k] for k in ['DB_NAME', 'BASE_URL']}


def test_fast_service_exit_still_uses_bounded_port_retry(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import psutil
    starts = []
    monkeypatch.setattr(r, 'load_environment', lambda: {})
    monkeypatch.setattr(r, 'create_database', lambda env: None)
    monkeypatch.setattr(r, 'drop_database', lambda env: None)
    monkeypatch.setattr(r, 'execute', lambda *a, **k: 0)
    monkeypatch.setattr(r, 'stop_process', lambda p: None)
    def start(env, log):
        process = SimpleNamespace(pid=100 + len(starts), poll=lambda: 1 if len(starts) == 1 else None)
        starts.append(process)
        return process
    def identity(pid):
        if pid == 100:
            raise psutil.NoSuchProcess(pid)
        return SimpleNamespace(create_time=lambda: 1)
    def wait(process, env):
        if process.pid == 100:
            raise r.RunnerError('Service exited before readiness')
    monkeypatch.setattr(psutil, 'Process', identity)
    monkeypatch.setattr(r, 'start_service', start)
    monkeypatch.setattr(r, 'wait_for_service', wait)
    assert r.main(['--suite', 'api', '--workers', '1', '--run-id', 'fast', '--output-dir', str(tmp_path)]) == 0
    assert len(starts) == 2


def test_old_port_attempt_cannot_hide_final_service_cleanup_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace
    starts, drops = [], []
    monkeypatch.setattr(r, 'load_environment', lambda: {})
    monkeypatch.setattr(r, 'create_database', lambda env: None)
    monkeypatch.setattr(r, 'drop_database', lambda env: drops.append(env))
    monkeypatch.setattr(r, 'execute', lambda *a, **k: 0)
    def start(env, log):
        process = SimpleNamespace(pid=None, attempt=len(starts), poll=lambda: 1)
        starts.append(process)
        return process
    def wait(process, env):
        if process.attempt == 0:
            raise r.RunnerError('Service exited before readiness')
    def stop(process):
        if process.attempt == 1:
            raise RuntimeError('cannot stop final service')
    monkeypatch.setattr(r, 'start_service', start)
    monkeypatch.setattr(r, 'wait_for_service', wait)
    monkeypatch.setattr(r, 'stop_process', stop)
    assert r.main(['--suite', 'api', '--workers', '1', '--run-id', 'stop', '--output-dir', str(tmp_path)]) != 0
    summary = json.loads((tmp_path / 'stop' / 'summary.json').read_text())
    assert summary['cleanup']['service'] == 'failed'
    assert not drops


def test_exited_controller_does_not_skip_registered_worker(monkeypatch):
    from types import SimpleNamespace
    stopped = []
    child = SimpleNamespace(terminate=lambda: stopped.append('worker'), wait=lambda timeout: None)
    controller = SimpleNamespace(poll=lambda: 1)
    monkeypatch.setattr(r, 'stop_registered', lambda records: stopped.extend(records) or [])
    r.stop_tree(controller, registered=['worker'])
    assert stopped == ['worker']


def test_recovery_stops_test_processes_before_database_and_rejects_reuse(tmp_path, monkeypatch):
    from tools import recover_run as recovery
    path = tmp_path / 'resources.json'
    processes = tmp_path / 'test-processes.json'
    processes.write_text(json.dumps([{'pid': 123, 'created_at': 1, 'fingerprint': 'owned', 'role': 'pytest'}]))
    path.write_text(json.dumps({'run_id': 'r', 'test_process_manifest': str(processes), 'environments': [{
        'worker': 'main', 'env': {'DB_NAME': 'shophub_test_' + 'a' * 32, 'TEST_RUN_ID': 'r', 'TEST_ENVIRONMENT_ID': 'e'},
        'cleanup': {'service': 'not_started', 'database': 'pending'}}]}))
    monkeypatch.setattr(recovery.lifecycle, 'load_environment', lambda: {})
    monkeypatch.setattr(recovery, 'stop_registered', lambda records: [{'pid': 123, 'type': 'IdentityMismatch'}])
    monkeypatch.setattr(recovery.lifecycle, 'database_connection', lambda env: pytest.fail('must not delete with active unknown test process'))
    assert recovery.recover(path) == 2
