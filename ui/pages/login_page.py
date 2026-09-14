"""登录页对象。"""
from .base_page import BasePage


class LoginPage(BasePage):
    URL_PATH = "/login.html"

    USERNAME = "#username"
    PASSWORD = "#password"
    SUBMIT = "#loginBtn"

    def login(self, username: str, password: str) -> "LoginPage":
        """填账号密码并提交登录，成功后前端会按角色跳转（买家→首页，卖家→卖家后台）。"""
        self.fill(self.USERNAME, username)
        self.fill(self.PASSWORD, password)
        self.click(self.SUBMIT)
        return self
