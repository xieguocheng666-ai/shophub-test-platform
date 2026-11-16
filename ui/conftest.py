"""浏览器先结束本例活动，再释放后端资源。"""
import pytest
import os
from pathlib import Path


@pytest.fixture(autouse=True)
def browser_resource_boundary(scenario, context):
    # 依赖scenario使本fixture先结束；页面/context由Playwright逐例创建。
    yield
    try:
        context.close()
    except Exception:
        try:
            marker = os.getenv('TEST_FATAL_FILE')
            if marker:
                Path(marker).write_text('browser_cleanup_failed', encoding='utf8')
        finally:
            pytest.exit('浏览器上下文无法结束，停止本轮', returncode=2)
