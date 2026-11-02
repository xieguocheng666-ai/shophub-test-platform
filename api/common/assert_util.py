"""统一断言工具：让失败信息带上响应体，不用每个测试手写报错信息。"""


def assert_status(resp, expected_code: int):
    """断言响应状态码，失败时带出响应体，方便定位问题。"""
    assert resp.status_code == expected_code, (
        f"期望状态码 {expected_code}，实际 {resp.status_code}\n响应体: {resp.text}"
    )


def assert_ok(resp):
    """断言状态码为 200。"""
    assert_status(resp, 200)


def assert_error(resp, expected_code: int, detail_contains=None):
    """负向断言：状态码 + 错误详情。

    只断状态码时，实现因为错误的原因返回同一个码也会通过；
    断言 detail 能锁住“这次失败到底是因为什么”。
    """
    assert_status(resp, expected_code)
    try:
        body = resp.json()
    except ValueError:
        raise AssertionError(f"期望 JSON 错误响应，实际：{resp.text}")
    detail = body.get("detail", "")
    assert detail, f"期望错误响应包含 detail，实际：{resp.text}"
    if detail_contains:
        assert detail_contains in detail, (
            f"期望 detail 包含 {detail_contains!r}，实际 {detail!r}"
        )
