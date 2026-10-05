"""基础冒烟 + JWT 鉴权测试：健康检查、登录、商品搜索、token 校验。"""
from datetime import datetime, timedelta
from pathlib import Path

import allure
import jwt as pyjwt
import pytest
import requests
import yaml

from api.common.assert_util import assert_ok, assert_status

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
@pytest.mark.parametrize("username,password,expect_code", _load_login_cases())
def test_login(username, password, expect_code, api_client):
    resp = api_client.login(username, password)
    assert_status(resp, expect_code)


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
    assert_status(resp, 401)


@allure.feature("鉴权")
@allure.story("伪造 token")
@allure.severity(allure.severity_level.NORMAL)
def test_me_with_forged_token(base_url):
    resp = requests.get(f"{base_url}/me", headers={"Authorization": "Bearer forged.token"})
    assert_status(resp, 401)


@allure.feature("鉴权")
@allure.story("错误签名")
@allure.severity(allure.severity_level.NORMAL)
def test_me_with_wrong_signature(base_url):
    token = pyjwt.encode({"username": "alice"}, "wrong-secret-key-for-testing-only", algorithm="HS256")
    resp = requests.get(f"{base_url}/me", headers={"Authorization": f"Bearer {token}"})
    assert_status(resp, 401)


@allure.feature("鉴权")
@allure.story("过期 token")
@allure.severity(allure.severity_level.NORMAL)
def test_me_with_expired_token(base_url, config):
    token = pyjwt.encode(
        {"username": "alice", "exp": datetime.utcnow() - timedelta(seconds=10)},
        config["secret_key"], algorithm="HS256",
    )
    resp = requests.get(f"{base_url}/me", headers={"Authorization": f"Bearer {token}"})
    assert_status(resp, 401)


@allure.feature("鉴权")
@allure.story("合法 token")
@allure.severity(allure.severity_level.NORMAL)
def test_me_with_valid_token(api_client, login_user):
    resp = api_client.me()
    assert_ok(resp)
    assert resp.json()["username"] == "alice"
    assert resp.json()["nickname"] == "爱丽丝"
