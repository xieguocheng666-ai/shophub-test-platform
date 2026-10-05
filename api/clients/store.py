"""店铺接口：开店、上架商品、改库存、查店铺。"""


class StoreMixin:
    def create_store(self, store_name: str):
        return self._request(
            "POST", "/stores", headers=self._headers(), json={"store_name": store_name}
        )

    def list_my_stores(self):
        return self._request("GET", "/stores", headers=self._headers())

    def add_product(self, store_id: int, name: str, price: float, stock: int):
        return self._request(
            "POST", f"/stores/{store_id}/products", headers=self._headers(),
            json={"name": name, "price": price, "stock": stock},
        )

    def update_stock(self, store_id: int, product_id: int, stock: int):
        return self._request(
            "PATCH", f"/stores/{store_id}/products/{product_id}/stock",
            headers=self._headers(), json={"stock": stock},
        )
