"""UI 测试：登录 → 搜索 → 加购 → 下单 → 卖家开店 核心链路。

每个测试独立（function 级 page fixture，自动新 context），
后端数据由 conftest 的 reset_data autouse fixture 在每个测试前重置。
"""
from playwright.sync_api import expect

from ui.pages.cart_page import CartPage
from ui.pages.index_page import IndexPage
from ui.pages.login_page import LoginPage
from ui.pages.orders_page import OrdersPage
from ui.pages.seller_page import SellerPage


def test_login_buyer_redirects_to_index(page, base_url):
    """买家登录成功 → 跳转首页。"""
    LoginPage(page, base_url).goto().login("alice", "alice123")
    expect(page).to_have_url(f"{base_url}/index.html")


def test_search_keyboard(page, base_url):
    """登录后搜索「键盘」，结果应包含键盘商品。"""
    LoginPage(page, base_url).goto().login("alice", "alice123")
    expect(page).to_have_url(f"{base_url}/index.html")

    IndexPage(page, base_url).search("键盘")
    expect(page.locator(".product-card").first).to_contain_text("键盘")


def test_add_to_cart_and_checkout(page, base_url):
    """全链路：登录 → 加购 → 购物车 → 下单 → 跳订单页。"""
    LoginPage(page, base_url).goto().login("alice", "alice123")
    expect(page).to_have_url(f"{base_url}/index.html")

    # 加购「联想店(store 1)的机械键盘(商品 2)」2 件
    IndexPage(page, base_url).add_to_cart(store_id=1, product_id=2, quantity=2)

    # 进购物车，确认商品在，然后结算下单
    page.goto(f"{base_url}/cart.html")
    expect(page.locator(CartPage.LIST)).to_contain_text("罗技 K845 机械键盘")
    CartPage(page, base_url).checkout()

    # 下单成功 → 跳转订单页
    expect(page).to_have_url(f"{base_url}/orders.html")


def test_seller_login_redirects_to_seller(page, base_url):
    """卖家登录成功 → 跳转卖家后台。"""
    LoginPage(page, base_url).goto().login("seller1", "seller123")
    expect(page).to_have_url(f"{base_url}/seller.html")


def test_seller_create_store(page, base_url):
    """卖家开店 → 店铺列表出现新店铺。"""
    LoginPage(page, base_url).goto().login("seller1", "seller123")
    expect(page).to_have_url(f"{base_url}/seller.html")

    SellerPage(page, base_url).create_store("测试新店铺")
    expect(page.locator(SellerPage.STORE_LIST)).to_contain_text("测试新店铺")
