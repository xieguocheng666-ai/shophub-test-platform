"""执行入口的资源所有权与失败路径，不使用真实数据库或服务。"""
import importlib
import json
from types import SimpleNamespace

import pytest


def runner():
    try:
        return importlib.import_module('tools.run_tests')
    except ModuleNotFoundError:
        pytest.fail('缺少独立执行入口 tools.run_tests')


class Process:
    def __init__(self, code=None):
        self.code = code
        self.stopped = False

    def poll(self):
        return self.code

    def terminate(self):
        self.stopped = True
        self.code = 0

    def wait(self, timeout=None):
        return self.code


@pytest.mark.parametrize('failure', ['none', 'init', 'test', 'interrupt', 'drop', 'collision'])
def test_run_owns_only_created_resources_and_records_failures(tmp_path, monkeypatch, failure):
    r = runner()
    events = []
    service = Process()
    envs = []

    def create(env):
        events.append('create')
        if failure == 'collision':
            raise RuntimeError('existing database')

    def execute(cmd, env, **kwargs):
        envs.append(env.copy())
        if 'pytest' not in cmd:
            events.append('init')
            return 1 if failure == 'init' else 0
        events.append('test')
        if failure == 'interrupt':
            raise KeyboardInterrupt()
        return 1 if failure == 'test' else 0

    def drop(env):
        events.append('drop')
        assert service.stopped or 'test' not in events
        if failure == 'drop':
            raise RuntimeError('sensitive-password')

    monkeypatch.setattr(r, 'create_database', create)
    monkeypatch.setattr(r, 'drop_database', drop)
    monkeypatch.setattr(r, 'execute', execute)
    monkeypatch.setattr(r, 'start_service', lambda env, log: service)
    monkeypatch.setattr(r, 'wait_for_service', lambda *a, **k: None)
    monkeypatch.setattr(r, 'load_environment', lambda: {'DB_PASSWORD': 'sensitive-password'})
    code = r.main(['--suite', 'api', '--workers', '1', '--run-id', 'example', '--output-dir', str(tmp_path)])
    summary = json.loads((tmp_path / 'example' / 'summary.json').read_text())
    assert code == summary['exitcode']
    assert (code == 0) == (failure == 'none')
    assert ('drop' in events) == (failure != 'collision')
    assert summary['cleanup']['database'] == ('failed' if failure == 'drop' else 'removed' if failure != 'collision' else 'not_created')
    assert 'sensitive-password' not in json.dumps(summary)
    assert set(summary['timings']) == {'prepare', 'execute', 'cleanup'}
    assert all(e['DB_NAME'].startswith('shophub_test_') for e in envs if 'DB_NAME' in e)
    if len(envs) == 2:
        assert envs[1]['TEST_ENVIRONMENT_MANIFEST']
        assert envs[0]['DB_NAME'] == summary['environments'][0]['env']['DB_NAME']


def test_unit_never_creates_database_and_preserves_pytest_exit(tmp_path, monkeypatch):
    r = runner()
    def forbidden(*args):
        pytest.fail('unit不应连接数据库或启动服务')
    monkeypatch.setattr(r, 'create_database', forbidden)
    monkeypatch.setattr(r, 'start_service', forbidden)
    commands = []
    monkeypatch.setattr(r, 'execute', lambda cmd, **kw: commands.append(cmd) or 5)
    assert r.main(['--suite', 'unit', '--output-dir', str(tmp_path), '--', '-k', 'nothing']) == 5
    assert commands[0][-2:] == ['-k', 'nothing']


def test_health_requires_this_run_and_reports_early_exit(monkeypatch):
    r = runner()
    monkeypatch.setattr(__import__('tools.environments', fromlist=['requests']).requests, 'get', lambda *a, **kw: SimpleNamespace(status_code=200, headers={'X-Test-Run-ID': 'other'}))
    with pytest.raises(RuntimeError, match='deadline'):
        r.wait_for_service(Process(), {'BASE_URL': 'http://localhost', 'TEST_RUN_ID': 'mine'}, timeout=0.01)
    with pytest.raises(RuntimeError, match='exited'):
        r.wait_for_service(Process(7), {'BASE_URL': 'http://localhost', 'TEST_RUN_ID': 'mine'})


def test_summary_explains_owned_service_early_exit(tmp_path, monkeypatch):
    r = runner()
    monkeypatch.setattr(r, 'load_environment', lambda: {})
    monkeypatch.setattr(r, 'create_database', lambda env: None)
    monkeypatch.setattr(r, 'drop_database', lambda env: None)
    monkeypatch.setattr(r, 'execute', lambda *a, **kw: 0)
    monkeypatch.setattr(r, 'start_service', lambda *a: Process(7))
    assert r.main(['--suite', 'api', '--workers', '1', '--run-id', 'early', '--output-dir', str(tmp_path)]) != 0
    summary = json.loads((tmp_path / 'early' / 'summary.json').read_text())
    assert 'exited' in summary['errors'][0]['detail']
    assert '7' in summary['errors'][0]['detail']


def test_database_collision_is_not_silently_adopted(monkeypatch):
    r = runner()
    statements = []
    existing = set()
    name = 'shophub_test_' + 'a' * 32
    class Connection:
        def cursor(self):
            return self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, sql):
            statements.append(sql)
            if sql.startswith('CREATE'):
                if name in existing:
                    raise RuntimeError('database exists')
                existing.add(name)
            else:
                existing.remove(name)
        def close(self):
            pass
    monkeypatch.setattr(__import__('tools.environments', fromlist=['database_connection']), 'database_connection', lambda env: Connection())
    env = {'DB_NAME': name}
    r.create_database(env)
    with pytest.raises(RuntimeError):
        r.create_database(env)
    assert existing == {name}
    assert all('IF NOT EXISTS' not in sql for sql in statements)
    r.drop_database(env)
    assert not existing
    for invalid in ['shop', 'shophub_test_unsafe`', 'shophub_test_other']:
        with pytest.raises(ValueError):
            r.drop_database({'DB_NAME': invalid})


def test_execute_terminates_owned_child_on_interrupt(monkeypatch):
    r = runner()
    process = Process()
    def wait(timeout=None):
        if not process.stopped:
            raise KeyboardInterrupt()
        return 0
    process.wait = wait
    monkeypatch.setattr(r.subprocess, 'Popen', lambda *a, **kw: process)
    with pytest.raises(KeyboardInterrupt):
        r.execute(['example'], {})
    assert process.stopped


def test_health_accepts_only_success_with_identity(monkeypatch):
    r = runner()
    responses = iter([SimpleNamespace(status_code=503, headers={'X-Test-Run-ID': 'mine'}),
                      SimpleNamespace(status_code=200, headers={'X-Test-Run-ID': 'mine'})])
    monkeypatch.setattr(__import__('tools.environments', fromlist=['requests']).requests, 'get', lambda *a, **kw: next(responses))
    r.wait_for_service(Process(), {'BASE_URL': 'http://localhost', 'TEST_RUN_ID': 'mine'})


def test_database_created_before_close_error_is_still_cleaned(tmp_path, monkeypatch):
    r = runner()
    resources = set()
    class Connection:
        def cursor(self):
            return self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, sql):
            if sql.startswith('CREATE'):
                resources.add('database')
            else:
                resources.remove('database')
        def close(self):
            if resources:
                raise RuntimeError('close failed after successful DDL')
    monkeypatch.setattr(__import__('tools.environments', fromlist=['database_connection']), 'database_connection', lambda env: Connection())
    monkeypatch.setattr(r, 'load_environment', lambda: {})
    assert r.main(['--suite', 'api', '--workers', '1', '--run-id', 'close-failure', '--output-dir', str(tmp_path)]) != 0
    assert not resources
