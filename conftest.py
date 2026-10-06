"""全局 fixture：conftest.py 会被 pytest 自动加载，所有测试用例共用。

fixture 生命周期：config/base_url 用 session 级（只读、可复用）；
api_client/login_user/payment_mode 用 function 级（每个测试独立，避免状态互相污染）。
"""
import os
from pathlib import Path

import pytest
import yaml
from dotenv import load_dotenv

from api.api_client import ApiClient

ROOT_DIR = Path(__file__).resolve().parent

load_dotenv(ROOT_DIR / ".env")


@pytest.fixture(scope="session")
def config():
    env = os.getenv("TEST_ENV", "dev")
    with open(ROOT_DIR / "config" / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # 环境覆盖文件：config.{env}.yaml（默认 dev 无需覆盖）
    env_path = ROOT_DIR / "config" / f"config.{env}.yaml"
    if env != "dev" and env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            cfg.update(yaml.safe_load(f))
    cfg["secret_key"] = os.getenv("SECRET_KEY")  # 敏感信息从 .env 读，不进 yaml
    cfg["env"] = env
    return cfg


@pytest.fixture(scope="session")
def base_url(config):
    # 容器内跑测试时用环境变量 BASE_URL 指向被测系统（如 http://sut:8000），
    # 本地跑则回退到 config.yaml 的 base_url。
    return os.getenv("BASE_URL", config["base_url"])
 

@pytest.fixture()
def api_client(base_url):
    """每个测试独立的客户端，token 互不串扰。"""
    return ApiClient(base_url)


@pytest.fixture(autouse=True)
def reset_data(api_client):
    """每个测试前重置被测系统数据，保证测试隔离（库存/购物车/订单/支付模式）。"""
    resp = api_client.reset()
    assert resp.status_code == 200, resp.text


@pytest.fixture()
def login_user(api_client, config):
    """登录默认账号 alice，返回 token 和用户名。"""
    user = config["users"]["alice"]
    resp = api_client.login(user["username"], user["password"])
    assert resp.status_code == 200, resp.text
    return {"token": resp.json()["token"], "username": user["username"]}


@pytest.fixture()
def payment_mode(api_client):
    """切换支付网关模式，测试结束后恢复为 success。"""
    def _set(mode):
        resp = api_client.set_payment_mode(mode)
        assert resp.status_code == 200, resp.text
        return resp

    _set("success")
    yield _set
    _set("success")
