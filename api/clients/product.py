"""商品接口：商品搜索、店铺商品详情。"""


class ProductMixin:
    def products(self, keyword: str = ""):
        return self._request("GET", "/products", params={"keyword": keyword})

    def store_product(self, store_id: int, product_id: int):
        return self._request("GET", f"/stores/{store_id}/products/{product_id}")
