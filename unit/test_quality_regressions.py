"""网络响应丢失、输入边界与 MySQL 无变化更新的回归。"""
from unittest.mock import MagicMock

import pytest
import requests
from fastapi import HTTPException
from pydantic import ValidationError

from api.api_client import ApiClient
from server import db
from server.main import ProductCreate, StockUpdate


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_write_response_loss_is_not_replayed(method, monkeypatch):
    client = ApiClient("http://isolated.invalid", retry=2)
    writes = []

    def response_lost(*args, **kwargs):
        writes.append("applied")
        raise requests.ReadTimeout("server applied write; response lost")

    monkeypatch.setattr(client.session, "request", response_lost)
    monkeypatch.setattr("api.api_client.time.sleep", lambda _: None)
    with pytest.raises(requests.ReadTimeout):
        client._request(method, "/write")
    assert writes == ["applied"]
    client.session.close()


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("error", [requests.ReadTimeout, requests.ConnectionError])
def test_read_network_failure_retries_are_bounded(method, error, monkeypatch):
    client = ApiClient("http://isolated.invalid", retry=2)
    send = MagicMock(side_effect=error("network unavailable"))
    monkeypatch.setattr(client.session, "request", send)
    monkeypatch.setattr("api.api_client.time.sleep", lambda _: None)
    with pytest.raises(error):
        client._request(method, "/read")
    assert send.call_count == 3
    client.session.close()


@pytest.mark.parametrize("status", [400, 500])
def test_http_error_response_is_not_retried(status, monkeypatch):
    client = ApiClient("http://isolated.invalid")
    response = requests.Response()
    response.status_code = status
    send = MagicMock(return_value=response)
    monkeypatch.setattr(client.session, "request", send)
    assert client._request("GET", "/read") is response
    assert send.call_count == 1
    client.session.close()


def test_non_network_request_error_is_not_retried(monkeypatch):
    client = ApiClient("http://isolated.invalid")
    send = MagicMock(side_effect=requests.exceptions.InvalidURL("invalid"))
    monkeypatch.setattr(client.session, "request", send)
    monkeypatch.setattr("api.api_client.time.sleep", lambda _: None)
    with pytest.raises(requests.exceptions.InvalidURL):
        client._request("GET", "/read")
    assert send.call_count == 1
    client.session.close()


@pytest.mark.parametrize("price,stock", [(-1, 1), (1, -1), (float("inf"), 1), (float("nan"), 1)])
def test_product_rejects_negative_or_nonfinite_values(price, stock):
    with pytest.raises(ValidationError):
        ProductCreate(name="probe", price=price, stock=stock)


def test_stock_update_rejects_negative():
    with pytest.raises(ValidationError):
        StockUpdate(stock=-1)


def test_zero_price_and_stock_remain_valid():
    assert ProductCreate(name="probe", price=0, stock=0).stock == 0
    assert StockUpdate(stock=0).stock == 0


@pytest.mark.parametrize("exists", [True, False])
def test_unchanged_stock_is_not_confused_with_missing_product(exists, monkeypatch):
    # MySQL 未开启 FOUND_ROWS 时，写入原值的 affected rows 也是零。
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.rowcount = 0
    cursor.fetchone.return_value = {"present": 1} if exists else None
    monkeypatch.setattr(db, "get_conn", lambda: conn)
    if exists:
        db.update_stock(1, 2, 100)
        conn.commit.assert_called_once()
    else:
        with pytest.raises(HTTPException) as raised:
            db.update_stock(1, 999, 100)
        assert raised.value.status_code == 404
        conn.commit.assert_not_called()
    conn.close.assert_called_once()
