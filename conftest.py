"""全局 fixture：conftest.py 会被 pytest 自动加载，所有测试用例共用。

fixture 生命周期：settings/base_url用session级（只读、可复用）；config按本例账号映射；
api_client/login_user/payment_mode 用 function 级（每个测试独立，避免状态互相污染）。
"""
import os
import time
import json
from types import SimpleNamespace
from pathlib import Path

import pymysql
import pytest
import yaml
from dotenv import load_dotenv

from api.api_client import ApiClient
from api.common import db_util

pytest_plugins = ["test_support.pytest_plugin"]

ROOT_DIR = Path(__file__).resolve().parent

load_dotenv(ROOT_DIR / ".env")


@pytest.fixture(scope="session")
def settings():
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
def base_url(settings):
    # 容器内跑测试时用环境变量 BASE_URL 指向被测系统（如 http://sut:8000），
    # 本地跑则回退到 config.yaml 的 base_url。
    return os.getenv("BASE_URL", settings["base_url"])
 

@pytest.fixture()
def api_client(base_url):
    """每个测试独立的客户端，token 互不串扰。"""
    client = ApiClient(base_url)
    yield client
    client.session.close()


def _fatal(reason):
    marker = os.getenv("TEST_FATAL_FILE")
    try:
        if marker:
            Path(marker).write_text(json.dumps({"environment_id": os.getenv("TEST_ENVIRONMENT_ID"), "reason": reason}), encoding="utf-8")
    finally:
        pytest.exit(reason, returncode=2)


def _quiesce(client, timeout=15):
    deadline = time.monotonic() + timeout
    original_retry = client.retry
    client.retry = 0
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("支付处理未在收尾期限内结束")
            response = client._request("GET", "/debug/payment-state", timeout=min(1, remaining))
            response.raise_for_status()
            if response.json()["in_flight"] == 0:
                return
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    finally:
        client.retry = original_retry


def _restore_success(client):
    response = client.set_payment_mode("success")
    assert response.status_code == 200
    assert response.json()["payment_mode"] == "success"


@pytest.fixture(autouse=True)
def business_lifecycle(api_client):
    """服务每次只服务本进程的一例；失败状态不带入下一例。"""
    try:
        _quiesce(api_client)
        _restore_success(api_client)
    except Exception:
        _fatal("准备时服务状态无法确认")
    yield
    try:
        _quiesce(api_client)
        _restore_success(api_client)
    except Exception:
        _fatal("收尾时支付请求未结束或开关无法恢复")


@pytest.fixture()
def scenario(base_url, db, api_client, business_lifecycle):
    """先等待本例请求结束、恢复开关，再按归属清理。"""
    from test_support.scenarios import TestData
    data = TestData(base_url, db)
    yield data
    try:
        _quiesce(api_client)
        _restore_success(api_client)
        data.cleanup()
    except Exception:
        _fatal("场景收尾或归属清理失败，停止本轮")


@pytest.fixture()
def catalog(scenario, db):
    """旧业务风险使用的本例商品组合；数字后缀表示角色，不是数据库编号。"""
    data = SimpleNamespace()
    data.alice, data.bob = scenario.buyer(nickname="爱丽丝"), scenario.buyer()
    data.seller1, data.seller2 = scenario.seller(), scenario.seller()
    specs = [(1, "笔记本", "5499.00", 50), (2, "罗技 K845 机械键盘", "299.00", 100),
             (3, "鼠标", "149.00", 200), (4, "显示器", "1099.00", 30),
             (5, "U盘", "39.90", 500), (6, "数据线", "19.90", 1000)]
    for number, name, price, stock in specs:
        seller = data.seller1 if number < 5 else data.seller2
        product = scenario.product(seller, name=f"{scenario.factory.namespace}_{name}", price=price, stock=stock)
        setattr(data, f"p{number}", product.product_id)
    data.s1, data.s2 = data.seller1.store_id, data.seller2.store_id
    # 同一scenario显式管理跨店共享商品，两家店的库存与价格独立。
    shared_name = db.scalar("SELECT name FROM products WHERE id=%s", (data.p1,))
    response = data.seller2.client.add_product(data.s2, shared_name, 5299, 30)
    assert response.status_code == 200 and response.json()["product_id"] == data.p1
    data.product_keyword = f"{scenario.factory.namespace}_罗技"
    data.zero_name = f"{scenario.factory.namespace}_zero"
    data.invalid_name = f"{scenario.factory.namespace}_invalid"
    # 原始业务客户端上架的合法商品也登记到本例归属清理。
    scenario._new_product_names.add(data.zero_name)
    data.product_ids_sql = ",".join(str(getattr(data, f"p{i}")) for i in range(1, 7))
    data.missing_product = db.scalar("SELECT COALESCE(MAX(id),0)+1 FROM products")
    assert db.scalar("SELECT COUNT(*) FROM products WHERE id=%s", (data.missing_product,)) == 0
    return data


@pytest.fixture()
def config(settings, catalog):
    cfg = {**settings, "users": {}}
    for name in ("alice", "bob", "seller1", "seller2"):
        account = getattr(catalog, name)
        cfg["users"][name] = {"username": account.username, "password": account.params.password}
    cfg["users"][f"seller{catalog.s1}"] = cfg["users"]["seller1"]
    cfg["users"][f"seller{catalog.s2}"] = cfg["users"]["seller2"]
    return cfg


@pytest.fixture()
def db():
    """数据库是完整验收的必需依赖，不可达时报告准备错误。"""
    try:
        conn = db_util.connect()
    except pymysql.err.OperationalError:
        pytest.fail("数据库不可达，无法完成数据校验", pytrace=False)
    try:
        wrapper = db_util.DB(conn)
        assert wrapper.scalar("SELECT DATABASE()") == os.getenv("DB_NAME", "shop")
        yield wrapper
    finally:
        conn.close()


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
        assert resp.json()["payment_mode"] == mode
        return resp

    return _set
