"""购物车页对象：结算。"""
from .base_page import BasePage


class CartPage(BasePage):
    URL_PATH = "/cart.html"

    LIST = "#cartList"
    TOTAL = "#cartTotal"
    CHECKOUT = "#checkoutBtn"

    def checkout(self) -> "CartPage":
        """点击「去结算」下单，成功后前端跳转到订单页。"""
        self.click(self.CHECKOUT)
        return self
