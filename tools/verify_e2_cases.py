"""使用正式运行器验证乱序、重复及故意失败的诊断，不改变业务测试。"""
import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import run_tests


def pytest_collection_modifyitems(items):
    mode = os.getenv('E2_CASE_PROBE')
    if mode == 'reverse':
        items.reverse()


def pytest_generate_tests(metafunc):
    if os.getenv('E2_CASE_PROBE') == 'repeat':
        metafunc.fixturenames.append('e2_iteration')
        metafunc.parametrize('e2_iteration', range(10))


@pytest.hookimpl(trylast=True)
def pytest_runtest_call(item):
    if os.getenv('E2_CASE_PROBE') in ('api-failure', 'ui-failure'):
        raise AssertionError('Intentional E2 evidence probe after business action')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['reverse', 'repeat', 'api-failure', 'ui-failure'], required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    suite = 'all' if args.mode == 'ui-failure' else 'api'
    selector = {'repeat': 'test_cart_isolation_with_owned_resources',
                'api-failure': 'test_cross_store_paid_order_has_independent_expected_amount',
                'ui-failure': 'test_add_to_cart_and_checkout'}.get(args.mode)
    original = run_tests.execute
    def execute(command, env, **kwargs):
        if 'pytest' in command:
            command = command + ['-p', 'tools.verify_e2_cases']
            env = {**env, 'E2_CASE_PROBE': args.mode}
        return original(command, env, **kwargs)
    command = ['--suite', suite, '--workers', '1' if args.mode in ('reverse', 'repeat') else '2',
               '--run-id', args.run_id, '--', '-q']
    if selector:
        command += ['-k', selector]
    if suite == 'all':
        command += ['--browser', 'chromium']
    with patch.object(run_tests, 'execute', execute):
        code = run_tests.main(command)
    output = run_tests.ROOT / 'reports' / 'engineering' / args.run_id
    summary = json.loads((output / 'summary.json').read_text(encoding='utf8'))
    expected_failure = args.mode.endswith('failure')
    assert code == (1 if expected_failure else 0), 'Unexpected probe exit code'
    if expected_failure:
        diagnostics = list(output.glob('gw*/diagnostics/case-*.json'))
        assert len(diagnostics) == 1
        record = json.loads(diagnostics[0].read_text(encoding='utf8'))
        assert record['requests'], 'Missing request evidence'
        worker = diagnostics[0].parent.parent
        log = (worker / 'service.log').read_text(encoding='utf8')
        assert any(r['request_id'] in log for r in record['requests'])
        if args.mode == 'ui-failure':
            assert list(worker.glob('ui/**/*.png')), 'Missing screenshot'
            assert list(worker.glob('ui/**/trace.zip')), 'Missing browser trace'
    assert summary['cleanup'] == {'database': 'removed', 'service': 'stopped'}
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
