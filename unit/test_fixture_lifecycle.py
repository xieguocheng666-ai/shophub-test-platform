"""服务收尾期限、业务恢复确认与手动场景清理失败契约。"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def fixtures():
    spec = importlib.util.spec_from_file_location('root_fixtures_contract', Path(__file__).parents[1] / 'conftest.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quiesce_uses_single_bounded_observation_and_preserves_retry():
    module = fixtures()
    states = iter([1, 0])
    class Client:
        retry = 2
        def _request(self, method, path, **kwargs):
            assert method == 'GET' and path == '/debug/payment-state'
            assert self.retry == 0
            assert 0 < kwargs['timeout'] <= 1
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'in_flight': next(states)})
    client = Client()
    module._quiesce(client)
    assert client.retry == 2


def test_restore_rejects_http_success_with_wrong_business_mode():
    module = fixtures()
    client = SimpleNamespace(set_payment_mode=lambda mode: SimpleNamespace(status_code=200, json=lambda: {'payment_mode': 'fail'}))
    with pytest.raises(AssertionError):
        module._restore_success(client)


def test_manual_scenario_cleanup_failure_marks_environment_fatal(tmp_path, monkeypatch):
    from test_support.scenarios import TestData
    fatal = tmp_path / 'fatal.txt'
    monkeypatch.setenv('TEST_FATAL_FILE', str(fatal))
    data = TestData('http://unused', None)
    data._accounts = [SimpleNamespace(username='owned', client=SimpleNamespace(session=SimpleNamespace(close=lambda: None)))]
    def fail(names):
        raise RuntimeError('cleanup probe')
    monkeypatch.setattr(data, '_delete_owned', fail)
    with pytest.raises(RuntimeError, match='cleanup probe'):
        data.cleanup()
    assert fatal.exists()
