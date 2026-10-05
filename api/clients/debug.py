"""调试接口：故障注入、数据重置、触发超时关单（仅 DEBUG_MODE 下注册）。"""


class DebugMixin:
    def set_payment_mode(self, mode: str):
        return self._request("POST", "/debug/payment-mode", json={"mode": mode})

    def cancel_expired(self, seconds: int = 0):
        return self._request("POST", f"/debug/cancel-expired?seconds={seconds}")

    def reset(self):
        return self._request("POST", "/debug/reset")
