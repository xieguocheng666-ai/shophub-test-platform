"""统一断言工具：让失败信息带上响应体，不用每个测试手写报错信息。"""


def assert_status(resp, expected_code: int):
    """断言响应状态码，失败时带出响应体，方便定位问题。"""
    assert resp.status_code == expected_code, (
        f"期望状态码 {expected_code}，实际 {resp.status_code}\n响应体: {resp.text}"
    )


def assert_ok(resp):
    """断言状态码为 200。"""
    assert_status(resp, 200)
