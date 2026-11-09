"""纯单元测试覆盖根目录的数据重置，不连接被测服务或数据库。"""
import pytest


@pytest.fixture(autouse=True)
def reset_data():
    yield
