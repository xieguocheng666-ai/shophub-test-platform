# 08 · Allure 进阶（step / severity / attach）

> 阶段1 接口测试深化 · Task 3
> 日期：2026-08-28

在 feature/story（见 [06-allure.md](06-allure.md)）基础上，讲 Allure 报告的三个进阶能力，让报告从"能分类"升级到"能看到过程 + 排优先级 + 附证据"。

| 子概念 | 作用 | 状态 |
|--------|------|------|
| step /step/ | 给用例内部拆步骤，报告能看到执行过程 | ✅ 2026-08-28 |
| severity /sɪˈverəti/ | 标注用例优先级 | ✅ 2026-08-28 |
| attach /əˈtætʃ/ | 附加请求/响应等证据 | ✅ 2026-08-28 |

---

## step：给报告"过程"

### 动机

feature/story 只解决了**纵向分类**（哪个模块、哪个场景），但一个用例内部做了什么（加购→下单→支付→发货→收货），报告里只有一行 `passed`，看不到过程。

### 本质

`allure.step` 把测试函数内部的一段代码标记成带标题的**步骤节点**，报告展开用例后能看到按执行顺序排列的步骤。

一句话：**feature/story 给报告"分类"，step 给报告"过程"。**

```
feature "状态机"
 └── story "全流程"
      └── test_order_full_flow
           ├── 步骤1：加购商品到购物车   ✅
           ├── 步骤2：创建订单          ✅
           ├── 步骤3：支付订单          ✅
           ├── 步骤4：发货             ✅
           └── 步骤5：确认收货          ✅
```

### 两种用法

| 用法 | 语法 | 场景 |
|------|------|------|
| 上下文管理器 | `with allure.step("标题"):` 包裹代码块 | 测试函数**内部**就地拆步骤（最常用） |
| 装饰器 | `@allure.step("标题")` 放在函数上一行 | 抽**可复用**步骤（如给 `ApiClient` 方法加） |

```python
# 上下文管理器版：测试内部就地拆步骤
with allure.step("创建订单"):
    order_id = api_client.order_create().json()["order_id"]

# 装饰器版：给 ApiClient 方法加，处处调用都自动记录步骤
@allure.step("支付订单")
def order_pay(self, order_id: int):
    return self._request("POST", f"/order/{order_id}/pay", headers=self._headers())
```

### 关键点

1. `with` 块正常跑完 → 步骤记 `passed`；抛异常 → 步骤记 `failed`/`broken`。失败时能定位到"哪一步挂的"。
2. 步骤可嵌套（步骤里还能再开子步骤）。
3. 变量跨步骤可见（Python 没有块级作用域，`order_id` 在 `with` 块里赋值，后面步骤照样读）。
4. **步骤标题要和动作一一对应**——"下单"步骤里别偷偷做"支付"，否则报告误导看报告的人。
5. **不是所有测试都要加 step**：只给有 2 个以上动作的"流程型"测试加（状态机/支付异常/并发），单请求单断言的简单测试（`test_health`/`test_login`/鉴权各例）加了反而是噪音。

### 用户踩过的坑

- **编辑器缓存覆盖**：Claude 外部改文件后，VS Code 旧缓冲保存会覆盖外部改动，需"重新加载"（同 [06-allure.md](06-allure.md) 的坑）。
- **`import allure` 别漏**：装饰器要用到 `allure`，漏 import 会 `NameError`。

---

## severity /sɪˈverəti/：给用例打优先级

### 动机

feature/story 解决"分类"，step 解决"过程"，但报告里几十个用例**权重是一样的**——`test_health` 和 `test_order_full_flow` 在报告里都只是绿勾，看的人不知道哪个挂了该第一时间处理。

### 本质

`severity` 标注的是"**这个测试失败时，暴露出来的缺陷有多严重**"，**不是**"这个测试本身写起来简不简单"。

一句话：**feature/story 给报告"分类"，step 给报告"过程"，severity 给报告"优先级"。**

### 五个级别

| 级别 | 含义 | 例子 |
|------|------|------|
| `BLOCKER` | 阻断：系统不可用，后续全卡住 | 服务挂了、登录 500 |
| `CRITICAL` | 核心功能坏，但系统还活着 | 下单、支付链路失败 |
| `NORMAL` | 一般功能问题（**默认值**，不写就是这个） | 常规功能用例 |
| `MINOR` | 次要问题 | 非核心字段格式不对 |
| `TRIVIAL` | 轻微（纯外观/文案） | 提示语措辞 |

**关键点：不写 `@allure.severity` 的用例，默认就是 `NORMAL`。**

### 用法

```python
import allure

@allure.feature("状态机")
@allure.story("全流程")
@allure.severity(allure.severity_level.CRITICAL)   # ← 和 feature/story 并列，各管各的
def test_order_full_flow(api_client, login_user):
    ...
```

### 本项目的 severity 映射

| 测试 | severity | 理由 |
|------|---------|------|
| `test_health` | **BLOCKER** | 服务死了 = 阻断一切 |
| `test_login` | **CRITICAL** | 登录入口坏，所有人登不进 |
| 下单全流程 / 支付异常 4 个 / 并发防超卖 2 个 | **CRITICAL** | 核心链路 + 资损 |
| 鉴权 5 个 / 越权 / 非法状态流转 | **NORMAL** | 安全边界，重要但非阻断 |

### 关键教训（2026-08-28 用户质疑纠正）

**用户问："冒烟测试挂了不是说明出大问题了吗？为什么标 MINOR？"——用户对了，我上一轮标错了。**

- **错误根源**：把"冒烟测试内容简单"误当成了"优先级低"。恰恰相反，冒烟测试覆盖的是**系统最基础的可用性**，一旦失败后果最重。
- **更深一点**：不是每个级别都非得有测试去填。severity 是"如实标注缺陷严重度"，项目里没有 MINOR/TRIVIAL 级别的功能点（如"字段格式""提示语措辞"），就不该硬塞。强行凑齐五级反而是错的。

---

## attach /əˈtætʃ/：给失败现场留证据

### 动机

step 告诉你"哪一步挂了"，但**没告诉你失败时请求发了什么、响应返回了什么**。想看现场，得自己加 print 再复现。

### 本质

`allure.attach(数据, name=标题, attachment_type=类型, extension=扩展名)` 把一段内容存进报告，作为某个用例/步骤的附件。

一句话：**step 给报告"过程"，attach 给报告"证据"。**

### 用法

```python
import allure

# 拿到响应之后 attach 状态码 + 响应体
pay_resp = api_client.order_pay(order_id)
assert pay_resp.json()["status"] == "paid"
allure.attach(
    str(pay_resp.status_code),           # 状态码是 int，必须 str()（原因见下）
    name="状态码",
    attachment_type=allure.attachment_type.TEXT,
)
allure.attach(
    pay_resp.text,                       # 响应体，str 类型，直接贴
    name="响应体",
    attachment_type=allure.attachment_type.JSON,
)
```

### 最常用的三种类型

| 类型 | 用途 |
|------|------|
| `TEXT` | 普通文本（URL、状态码、日志） |
| `JSON` | 响应体（会美化 + 语法高亮） |
| `PNG` | 截图（**阶段2 UI 测试大量用**） |

### 用户问过的三个问题（2026-08-28 沉淀）

**Q1：attach 放在哪个位置？**

紧跟"拿到响应那一行"之后，且放在对应的 `with allure.step(...)` 块**内部**。原因：① `attach` 要读 `resp.text`，必须先有 `resp` 变量；② 放 step 块内，附件才挂在对应步骤下，定位精准。

**Q2：`str(resp.status_code)` 能不能省掉 str？**

**不能，省了会直接报错。** 源码依据（`allure_commons/logger.py:42`）：

```python
with open(destination, "wb") as attached_file:   # "wb" = 二进制写
    if isinstance(body, str):
        attached_file.write(body.encode("utf-8"))  # str → encode 成 bytes
    else:
        attached_file.write(body)                  # int 走到这里 → 报错
```

`resp.status_code` 是 `int`（200），走到 `else` 分支 `write(200)`，抛 `TypeError: a bytes-like object is required, not 'int'`。所以 `str()` 不是规范，是**必须**。

**Q3：`resp.text` 是什么？**

`resp` 是 requests 的 `Response` 对象，`.text` 是它的属性，含义是**响应体（服务器返回的数据）的字符串形式**。

| 写法 | 类型 | 含义 |
|------|------|------|
| `resp.status_code` | `int` | 状态码 200/400/500 |
| `resp.text` | `str` | 响应体字符串，如 `'{"status":"paid"}'` |
| `resp.content` | `bytes` | 响应体原始字节（未解码） |
| `resp.json()` | `dict`/`list` | 把响应体解析成 Python 对象（方法，要加括号） |

**为什么 attach 用 `.text` 而不是 `.json()`？** attach 要"贴一段文本"（str），`.text` 正好是 str；`.json()` 返回 dict，直接 attach 一个 dict 又会踩 Q2 的坑（不是 str/bytes）。

### 用户踩过的坑

- **attach 放错步骤**：任务要求放"支付订单"，实际放到了"创建订单"——两处都能学 attach 用法，但支付是资金关键动作，失败最该留证据。
- **缩进乱**：attach 参数写成 14 空格（应为 12），括号内 Python 不报错但难看，面试观感差。
- **抄了示例里的三元表达式**：`attachment_type=JSON if content-type 是 json else TEXT` 是"防御性"写法，但 FastAPI 接口一定返回 JSON，直接写 `allure.attachment_type.JSON` 就够。把简单事搞复杂了。

---

## 跨领域意外收获：DEBUG_MODE 405

跑测试时 18 个测试全在 `reset_data` fixture 报 `405`，根因：

- `/debug/reset`、`/debug/payment-mode` 被 `if os.getenv("DEBUG_MODE") == "1":` 包着（`server/main.py:303`），启动 server 漏设该环境变量 → 路由未注册
- 根路径 `app.mount("/", StaticFiles(...))` 接住了 POST，StaticFiles 只认 GET → 回 `405`（而非 `404`）

**工程意义（面试可深挖）**：故障注入接口（能清空订单/购物车、改支付行为）是"危险"接口，生产环境必须隔离。所以用 `DEBUG_MODE=1` 做闸门——默认（生产）根本不注册这些路由，测试环境才显式开启。启动命令已修正为 `DEBUG_MODE=1 ... uvicorn ...`。

> 面试官追问："你的 reset 接口在生产会不会有安全问题？" → 生产不设 `DEBUG_MODE`，路由不存在，不存在攻击面。
