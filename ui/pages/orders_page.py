"""订单页对象。"""
from .base_page import BasePage


class OrdersPage(BasePage):
    URL_PATH = "/orders.html"

    LIST = "#orderList"

    def first_order_card(self):
        """返回第一个订单卡片，供测试断言订单状态/金额。"""
        return self.page.locator("#orderList .card").first
