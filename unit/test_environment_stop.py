"""污染标记必须在下一例fixture准备前停止，不能依赖主控轮询。"""
from types import SimpleNamespace

import pytest

from test_support import environment_plugin


def test_existing_fatal_marker_stops_before_next_case_setup(tmp_path, monkeypatch):
    marker = tmp_path / 'fatal.txt'
    marker.write_text('scenario_cleanup_failed', encoding='utf8')
    monkeypatch.setenv('TEST_FATAL_FILE', str(marker))
    events = []
    with pytest.raises(pytest.exit.Exception) as captured:
        environment_plugin.pytest_runtest_setup(SimpleNamespace())
        events.append('next_case_fixture_started')
    assert captured.value.returncode == 2
    assert events == []
    assert marker.read_text(encoding='utf8') == 'scenario_cleanup_failed'


def test_clean_environment_allows_next_case_setup(tmp_path, monkeypatch):
    marker = tmp_path / 'not_created.txt'
    monkeypatch.setenv('TEST_FATAL_FILE', str(marker))
    environment_plugin.pytest_runtest_setup(SimpleNamespace())
    assert not marker.exists()
