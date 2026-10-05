"""购物车接口：加购、查看、移除。"""


class CartMixin:
    def cart_add(self, store_id: int, product_id: int, quantity: int = 1):
        return self._request(
            "POST", "/cart", headers=self._headers(),
            json={"store_id": store_id, "product_id": product_id, "quantity": quantity},
        )

    def cart_get(self):
        return self._request("GET", "/cart", headers=self._headers())

    def cart_remove(self, store_id: int, product_id: int):
        return self._request("DELETE", f"/cart/{store_id}/{product_id}", headers=self._headers())
