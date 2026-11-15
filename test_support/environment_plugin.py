"""显式 -p 加载；在首个conftest导入前将worker绑定到运行器清单。"""
import json
import os
import time
from pathlib import Path
import pytest

_BOUND = None


def bind_environment():
    path = os.getenv('TEST_ENVIRONMENT_MANIFEST')
    if not path:
        return
    worker = os.getenv('PYTEST_XDIST_WORKER')
    if worker is None and int(os.getenv('TEST_PARALLEL_WORKERS', '1')) > 1:
        for key in ('DB_NAME', 'BASE_URL', 'TEST_ENVIRONMENT_ID'):
            os.environ.pop(key, None)
        return
    worker = worker or 'main'
    data = json.loads(Path(path).read_text(encoding='utf8'))
    selected = next((e for e in data['environments'] if e['worker'] == worker), None)
    if selected is None:
        raise pytest.UsageError('Worker has no owned environment')
    os.environ.update(selected['env'])
    os.environ['TEST_WORKER_ID'] = worker
    return {**selected['env'], 'TEST_WORKER_ID': worker}


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config, parser, args):
    global _BOUND
    _BOUND = bind_environment()


def pytest_configure(config):
    if _BOUND and hasattr(config.option, 'output'):
        config.option.output = str(Path(_BOUND['TEST_ARTIFACT_DIR']).parent / 'ui')


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    # 手动TestData上下文失败也会标记污染；不等运行器轮询才停止下一例。
    marker = os.getenv('TEST_FATAL_FILE')
    if marker and Path(marker).exists():
        pytest.exit('环境已标记污染，禁止开始下一例准备', returncode=2)


def pytest_runtest_logreport(report):
    if not _BOUND:
        return
    path = Path(_BOUND['TEST_ARTIFACT_DIR']).parent / 'cases.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf8') as f:
        f.write(json.dumps({'nodeid': report.nodeid, 'phase': report.when, 'outcome': report.outcome,
                            'worker': _BOUND['TEST_WORKER_ID'], 'environment': _BOUND['TEST_ENVIRONMENT_ID'],
                            'start': report.start, 'stop': report.stop, 'duration': report.duration}) + '\n')


def pytest_handlecrashitem(crashitem, report, sched):
    path = os.getenv('TEST_FATAL_FILE')
    if path:
        Path(path).write_text('worker_crash', encoding='utf8')
