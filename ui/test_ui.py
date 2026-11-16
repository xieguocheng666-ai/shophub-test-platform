"""UI 测试：登录 → 搜索 → 加购 → 下单 → 卖家开店 核心链路。

每个测试独立（function 级 page fixture，自动新 context），
后端数据由 scenario 按本例创建并清理，page每例使用独立Cookie上下文。
"""
from decimal import Decimal

from playwright.sync_api import expect

from ui.pages.cart_page import CartPage
from ui.pages.index_page import IndexPage
from ui.pages.login_page import LoginPage
from ui.pages.orders_page import OrdersPage
from ui.pages.seller_page import SellerPage


def test_login_buyer_redirects_to_index(page, base_url):
    """买家登录成功 → 跳转首页。"""
    # 只读参考账号；本例只验证登录/搜索，不修改业务数据。
    LoginPage(page, base_url).goto().login("alice", "alice123")
    expect(page).to_have_url(f"{base_url}/index.html")


def test_search_keyboard(page, base_url):
    """登录后搜索「键盘」，结果应包含键盘商品。"""
    # 只读参考账号；本例只验证登录/搜索，不修改业务数据。
    LoginPage(page, base_url).goto().login("alice", "alice123")
    expect(page).to_have_url(f"{base_url}/index.html")

    IndexPage(page, base_url).search("键盘")
    expect(page.locator(".product-card").first).to_contain_text("键盘")


def test_add_to_cart_and_checkout(page, base_url, db, scenario):
    """全链路：登录 → 加购 → 购物车 → 下单 → 跳订单页。"""
    buyer = scenario.buyer()
    product_name = f"{scenario.factory.namespace}_机械键盘"
    product = scenario.product(scenario.seller(), name=product_name, price="299.00", stock=100)
    LoginPage(page, base_url).goto().login(buyer.username, buyer.params.password)
    expect(page).to_have_url(f"{base_url}/index.html")

    # 网页搜索本例唯一商品，选择实际店铺和商品编号，加购2件
    IndexPage(page, base_url).search(scenario.factory.namespace)
    IndexPage(page, base_url).add_to_cart(store_id=product.store_id, product_id=product.product_id, quantity=2)

    # 进购物车，确认商品在，然后结算下单
    page.goto(f"{base_url}/cart.html")
    expect(page.locator(CartPage.LIST)).to_contain_text(product_name)
    expect(page.locator(".line-item__qty")).to_have_text("× 2")
    expect(page.locator(CartPage.TOTAL)).to_have_text("¥598.00")
    CartPage(page, base_url).checkout()

    # 下单成功 → 跳转订单页
    expect(page).to_have_url(f"{base_url}/orders.html")
    orders = db.all("SELECT order_id, status, total FROM orders WHERE username=%s", (buyer.username,))
    assert len(orders) == 1
    assert orders[0]["status"] == "pending"
    assert orders[0]["total"] == Decimal("598.00")
    assert db.all(
        "SELECT oi.product_id, oi.quantity, oi.price FROM order_items oi "
        "JOIN sub_orders so ON so.sub_order_id=oi.sub_order_id WHERE so.parent_order_id=%s",
        (orders[0]["order_id"],),
    ) == [{"product_id": product.product_id, "quantity": 2, "price": Decimal("299.00")}]
    assert db.scalar("SELECT COUNT(*) FROM carts WHERE username=%s", (buyer.username,)) == 0


def test_seller_login_redirects_to_seller(page, base_url):
    """卖家登录成功 → 跳转卖家后台。"""
    # 只读参考商家账号，仅验证登录跳转。
    LoginPage(page, base_url).goto().login("seller1", "seller123")
    expect(page).to_have_url(f"{base_url}/seller.html")


def test_seller_create_store(page, base_url, scenario):
    """卖家开店 → 店铺列表出现新店铺。"""
    seller = scenario.seller()
    store_name = f"{scenario.factory.namespace}_新店铺"
    LoginPage(page, base_url).goto().login(seller.username, seller.params.password)
    expect(page).to_have_url(f"{base_url}/seller.html")

    SellerPage(page, base_url).create_store(store_name)
    expect(page.locator(SellerPage.STORE_LIST)).to_contain_text(store_name)
