"""基础冒烟 + JWT 鉴权测试：健康检查、登录、商品搜索、token 校验。"""
from datetime import datetime, timedelta
from pathlib import Path

import allure
import jwt as pyjwt
import pytest
import requests
import yaml

from api.common.assert_util import assert_error, assert_ok, assert_status
from api.common.db_util import assert_db_value

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def _load_login_cases():
    with open(ROOT_DIR / "data" / "users.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cases = []
    for group, items in data.items():
        for item in items:
            cases.append(
                pytest.param(
                    item["username"], item["password"], item["expect_code"],
                    item.get("expect_detail"),
                    id=item["id"],
                )
            )
    return cases


def _load_search_cases():
    with open(ROOT_DIR / "data" / "search.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cases = []
    for group, items in data.items():
        for item in items:
            cases.append(
                pytest.param(
                    item["keyword"], item["expect_count"],
                    id=item["id"],
                )
            )
    return cases


# ---------- 基础 ----------
@allure.severity(allure.severity_level.BLOCKER)
def test_health(api_client):
    resp = api_client.health()
    assert_ok(resp)
    assert resp.json() == {"status": "ok"}


@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("username,password,expect_code,expect_detail", _load_login_cases())
def test_login(username, password, expect_code, expect_detail, api_client):
    resp = api_client.login(username, password)
    if expect_code == 200:
        assert_status(resp, 200)
        assert resp.json().get("token"), f"登录成功应返回 token，实际：{resp.text}"
    else:
        assert_error(resp, expect_code, expect_detail)


@allure.severity(allure.severity_level.NORMAL)
@pytest.mark.parametrize("keyword,expect_count", _load_search_cases())
def test_search_products(keyword, expect_count, api_client):
    resp = api_client.products(keyword)
    items = resp.json()["items"]
    assert len(items) == expect_count
    if keyword:
        assert all(keyword.lower() in item["name"].lower() for item in items)


# ---------- 鉴权 ----------
@allure.feature("鉴权")
@allure.story("缺失 token")
@allure.severity(allure.severity_level.NORMAL)
def test_me_without_token(base_url):
    resp = requests.get(f"{base_url}/me")
    assert_error(resp, 401, "缺少 token")


@allure.feature("鉴权")
@allure.story("伪造 token")
@allure.severity(allure.severity_level.NORMAL)
def test_me_with_forged_token(base_url):
    resp = requests.get(f"{base_url}/me", headers={"Authorization": "Bearer forged.token"})
    assert_error(resp, 401, "token 无效")


@allure.feature("鉴权")
@allure.story("错误签名")
@allure.severity(allure.severity_level.NORMAL)
def test_me_with_wrong_signature(base_url):
    token = pyjwt.encode({"username": "alice"}, "wrong-secret-key-for-testing-only", algorithm="HS256")
    resp = requests.get(f"{base_url}/me", headers={"Authorization": f"Bearer {token}"})
    assert_error(resp, 401, "token 无效")


@allure.feature("鉴权")
@allure.story("过期 token")
@allure.severity(allure.severity_level.NORMAL)
def test_me_with_expired_token(base_url, config):
    token = pyjwt.encode(
        {"username": "alice", "exp": datetime.utcnow() - timedelta(seconds=10)},
        config["secret_key"], algorithm="HS256",
    )
    resp = requests.get(f"{base_url}/me", headers={"Authorization": f"Bearer {token}"})
    assert_error(resp, 401, "token 已过期")


@allure.feature("鉴权")
@allure.story("合法 token")
@allure.severity(allure.severity_level.NORMAL)
def test_me_with_valid_token(api_client, login_user):
    resp = api_client.me()
    assert_ok(resp)
    assert resp.json()["username"] == "alice"
    assert resp.json()["nickname"] == "爱丽丝"


@allure.feature("鉴权")
@allure.story("密码不以明文落库")
@allure.severity(allure.severity_level.CRITICAL)
def test_password_stored_hashed(db):
    """接口完全看不到密码怎么存的，只能直连库确认。"""
    stored = db.scalar("SELECT password FROM users WHERE username=%s", ("alice",))
    assert stored != "alice123", "密码不能明文存储"
    assert ":" in stored, f"期望格式为「盐:哈希」，实际：{stored}"
    salt_hex, hash_hex = stored.split(":", 1)
    assert len(salt_hex) == 32, f"盐应为 16 字节（32 位 hex），实际长度 {len(salt_hex)}"
    assert len(hash_hex) == 64, f"SHA-256 哈希应为 32 字节（64 位 hex），实际长度 {len(hash_hex)}"
    assert_db_value(
        db,
        "SELECT COUNT(*) AS n FROM users WHERE password=%s",
        0, ("alice123",), "库中不应存在明文密码",
    )
