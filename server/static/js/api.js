/* ==========================================================================
   My Shop — 前端 API 封装
   统一管理 JWT token（localStorage）与鉴权 header。
   后端要求 Authorization: Bearer <token>（不是 query 参数），全部接口走这里。
   ========================================================================== */

const API = {
  get token() {
    return localStorage.getItem("shop_token") || "";
  },
  set token(v) {
    v ? localStorage.setItem("shop_token", v) : localStorage.removeItem("shop_token");
  },

  get user() {
    try { return JSON.parse(localStorage.getItem("shop_user") || "null"); }
    catch { return null; }
  },
  set user(v) {
    v ? localStorage.setItem("shop_user", JSON.stringify(v)) : localStorage.removeItem("shop_user");
  },

  loggedIn() { return !!this.token; },

  // 统一请求：自动带鉴权 header，非 2xx 抛错（错误信息取后端 detail 字段）
  async request(method, path, body) {
    const headers = {};
    if (this.token) headers["Authorization"] = "Bearer " + this.token;
    if (body !== undefined) headers["Content-Type"] = "application/json";

    let resp;
    try {
      resp = await fetch(path, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      throw new Error("网络请求失败，请确认服务已启动");
    }

    let data = null;
    try { data = await resp.json(); } catch { data = {}; }

    if (!resp.ok) {
      const detail = (data && data.detail) ? data.detail : "请求失败（" + resp.status + "）";
      const err = new Error(detail);
      err.status = resp.status;
      throw err;
    }
    return data;
  },

  // ---------- 认证 / 用户 ----------
  login(username, password) { return this.request("POST", "/login", { username, password }); },
  me() { return this.request("GET", "/me"); },

  // ---------- 商品（买家） ----------
  products(keyword = "", page = 1, pageSize = 20) {
    const q = new URLSearchParams({ keyword, page, page_size: pageSize });
    return this.request("GET", "/products?" + q.toString());
  },

  // ---------- 购物车 ----------
  cartGet() { return this.request("GET", "/cart"); },
  cartAdd(storeId, productId, quantity) {
    return this.request("POST", "/cart", { store_id: storeId, product_id: productId, quantity });
  },
  cartRemove(storeId, productId) {
    return this.request("DELETE", `/cart/${storeId}/${productId}`);
  },

  // ---------- 订单（买家） ----------
  createOrder() { return this.request("POST", "/order"); },
  orders(page = 1, pageSize = 20) {
    const q = new URLSearchParams({ page, page_size: pageSize });
    return this.request("GET", "/orders?" + q.toString());
  },
  orderDetail(orderId) { return this.request("GET", `/order/${orderId}`); },
  payOrder(orderId) { return this.request("POST", `/order/${orderId}/pay`); },
  confirmSubOrder(subOrderId) { return this.request("POST", `/sub-orders/${subOrderId}/confirm`); },

  // ---------- 卖家 ----------
  myStores() { return this.request("GET", "/stores"); },
  createStore(storeName) { return this.request("POST", "/stores", { store_name: storeName }); },
  storeProducts(storeId) { return this.request("GET", `/stores/${storeId}/products`); },
  addProduct(storeId, name, price, stock) {
    return this.request("POST", `/stores/${storeId}/products`, { name, price, stock });
  },
  updateStock(storeId, productId, stock) {
    return this.request("PATCH", `/stores/${storeId}/products/${productId}/stock`, { stock });
  },
  shipSubOrder(subOrderId) { return this.request("POST", `/sub-orders/${subOrderId}/ship`); },
};

/* --------------------------------------------------------------------------
   通用 UI 工具
   -------------------------------------------------------------------------- */

// Toast 提示：toast("保存成功") / toast("失败", "error")
function toast(msg, type = "success") {
  let wrap = document.querySelector(".toast-wrap");
  if (!wrap) {
    wrap = document.createElement("div");
    wrap.className = "toast-wrap";
    document.body.appendChild(wrap);
  }
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => {
    el.classList.add("hide");
    setTimeout(() => el.remove(), 260);
  }, 2600);
}

// 金额格式化：5499 -> ¥5,499.00
function fmtMoney(n) {
  return "¥" + Number(n).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// 中文状态映射
const STATUS_CN = {
  pending: "待支付",
  paid: "已支付",
  shipped: "已发货",
  completed: "已完成",
  canceled: "已取消",
};

// 登录守卫：未登录跳回登录页
function requireLogin() {
  if (!API.loggedIn()) {
    location.href = "/login.html";
    return false;
  }
  return true;
}

// 角色守卫：非指定角色跳回首页
function requireRole(role) {
  if (!requireLogin()) return false;
  const u = API.user;
  if (!u || u.role !== role) {
    toast("该页面需要 " + (role === "seller" ? "卖家" : "买家") + " 权限", "error");
    setTimeout(() => (location.href = "/"), 900);
    return false;
  }
  return true;
}

// 退出登录
function logout() {
  API.token = "";
  API.user = null;
  location.href = "/login.html";
}

// HTML 转义：插入任何后端返回的文本前先转义，防 XSS
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// 顶部导航：所有页面共用，active 标记当前页，卖家才显示「卖家后台」
function renderTopnav(active) {
  const u = API.user || {};
  const isSeller = u.role === "seller";
  const nav = [
    { key: "index", href: "/index.html", label: "首页", show: true },
    { key: "cart", href: "/cart.html", label: "购物车", show: true },
    { key: "orders", href: "/orders.html", label: "订单", show: true },
    { key: "seller", href: "/seller.html", label: "卖家后台", show: isSeller },
  ];
  const links = nav
    .filter((n) => n.show)
    .map((n) => `<a class="nav-link ${n.key === active ? "active" : ""}" href="${n.href}">${n.label}</a>`)
    .join("");
  return `
    <nav class="topnav">
      <div class="container topnav__inner">
        <a class="brand" href="/index.html">
          <span class="brand__mark">S</span> My Shop
          <span class="brand__sub">平台电商</span>
        </a>
        <div class="topnav__actions">
          ${links}
          <div class="nav-user">
            <span class="nav-user__name">${escapeHtml(u.nickname || u.username || "未登录")}</span>
            <span class="nav-user__role ${isSeller ? "seller" : ""}">${isSeller ? "卖家" : "买家"}</span>
          </div>
          <button class="nav-link" onclick="logout()">退出</button>
        </div>
      </div>
    </nav>`;
}
