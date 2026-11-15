"""纯单元测试屏蔽根目录的业务服务生命周期，不连接被测服务或数据库。"""
import pytest


@pytest.fixture(autouse=True)
def business_lifecycle():
    yield
