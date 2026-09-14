"""首页（商品列表）对象：搜索 + 加购。"""
from .base_page import BasePage


class IndexPage(BasePage):
    URL_PATH = "/index.html"

    SEARCH = "#searchInput"
    RESULT_COUNT = "#resultCount"

    def search(self, keyword: str) -> "IndexPage":
        """输入关键词回车搜索（前端监听 Enter 触发）。"""
        self.fill(self.SEARCH, keyword)
        self.page.locator(self.SEARCH).press("Enter")
        return self

    def add_to_cart(self, store_id: int, product_id: int, quantity: int = 1) -> "IndexPage":
        """定位「某店铺的某商品」卡片，设置数量后点击加购。

        商品同一 SPU 可能在多店在售，所以用 store_id + product_id 共同定位。
        """
        card = self.page.locator(
            f'.product-card:has([data-action="add"][data-store-id="{store_id}"][data-product-id="{product_id}"])'
        )
        if quantity != 1:
            card.locator(".product-card__qty").fill(str(quantity))
        card.locator('[data-action="add"]').click()
        # 加购走异步 fetch，等 toast「已加入购物车」出现，确保请求完成后再离开页面
        self.page.locator(".toast").wait_for(state="visible")
        return self
