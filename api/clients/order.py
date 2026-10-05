"""订单接口：创建、支付、发货、确认收货、列表。"""

import allure


class OrderMixin:
    @allure.step("创建订单")
    def order_create(self):
        return self._request("POST", "/order", headers=self._headers())

    @allure.step("支付订单")
    def order_pay(self, order_id: int):
        return self._request("POST", f"/order/{order_id}/pay", headers=self._headers())

    def sub_order_ship(self, sub_order_id: int):
        return self._request("POST", f"/sub-orders/{sub_order_id}/ship", headers=self._headers())

    def sub_order_confirm(self, sub_order_id: int):
        return self._request("POST", f"/sub-orders/{sub_order_id}/confirm", headers=self._headers())

    def orders(self):
        return self._request("GET", "/orders", headers=self._headers())
