"""鉴权接口：健康检查、登录、当前用户。"""


class AuthMixin:
    def login(self, username: str, password: str):
        resp = self._request(
            "POST", "/login", json={"username": username, "password": password}
        )
        if resp.status_code == 200:
            self.token = resp.json()["token"]
        return resp

    def health(self):
        return self._request("GET", "/health")

    def me(self):
        return self._request("GET", "/me", headers=self._headers())
