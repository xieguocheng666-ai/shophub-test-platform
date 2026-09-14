"""BasePage：所有页面对象的基类，封装 Playwright 通用操作。

每个业务页面继承 BasePage，通过 URL_PATH 声明自己的路径，
元素定位用稳定选择器（id / data-* 语义属性），避免脆弱的 XPath 索引。
"""
from playwright.sync_api import Locator, Page


class BasePage:
    """页面对象基类。

    持有 page（Playwright 页面句柄）与 base_url（被测系统地址，从 config.yaml 读入）。
    """

    URL_PATH = ""  # 子类覆盖：本页面的路径，如 "/login.html"

    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url

    def goto(self) -> "BasePage":
        """跳转到本页面，返回 self 支持链式调用。"""
        self.page.goto(f"{self.base_url}{self.URL_PATH}")
        return self

    def fill(self, selector: str, value: str) -> "BasePage":
        self.page.locator(selector).fill(value)
        return self

    def click(self, selector: str) -> "BasePage":
        self.page.locator(selector).click()
        return self

    def get_text(self, selector: str) -> str:
        return self.page.locator(selector).inner_text()

    def locator(self, selector: str) -> Locator:
        return self.page.locator(selector)
