"""卖家后台页对象：开店。"""
from .base_page import BasePage


class SellerPage(BasePage):
    URL_PATH = "/seller.html"

    STORE_NAME = "#storeName"
    CREATE_STORE = "#createStoreBtn"
    STORE_LIST = "#storeList"

    def create_store(self, name: str) -> "SellerPage":
        """输入店铺名并创建。"""
        self.fill(self.STORE_NAME, name)
        self.click(self.CREATE_STORE)
        return self
