"""失败报告在清理前先写入，清理报告生成后补齐所有阶段。"""
import os

import pytest

from test_support import diagnostics


def pytest_runtest_logstart(nodeid, location):
    try:
        diagnostics.begin_case(nodeid)
    except Exception:
        pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    try:
        diagnostics.finish_case(report.when, report.outcome, call.excinfo.type if call.excinfo else None)
        if report.failed:
            item._engineering_failed = True
        if getattr(item, "_engineering_failed", False):
            diagnostics.write_failure(os.getenv("TEST_ARTIFACT_DIR", "reports/diagnostics"), item.nodeid)
    except Exception:
        pass  # 留证失败不得改变业务用例结果。
    finally:
        if report.when == "teardown":
            try:
                diagnostics.end_case(item.nodeid)
            except Exception:
                pass
