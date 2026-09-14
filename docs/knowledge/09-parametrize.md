# 09 · 数据驱动深化（parametrize / pytest.param / 断言设计）

> 阶段1 接口测试深化 · Task 4
> 日期：2026-08-30

数据驱动（data-driven）的核心思想一句话：**一个测试函数 + 多组参数 = 自动变出 N 条用例**。本 task 讲三个层层递进的点：① parametrize 到底在干什么；② 数据为什么要外置到 yaml；③ 用 `pytest.param(id=)` 给每条用例起业务名。最后落到一个更本质的问题——**断言该精确匹配还是只抓关键点**。

---

## 一、parametrize /pəˈræmɪtəraɪz/ 的本质

`@pytest.mark.parametrize` 干的事就一句话：**一个测试函数，配一组参数，自动生成 N 条用例**。

```python
@pytest.mark.parametrize("a,b", [(1,2), (3,4)])   # 两组参数 → 生成 2 条用例
def test_add(a, b):
    assert a + b > 0
```

它要两个东西：

| 参数位置 | 内容 | 例子 |
|---------|------|------|
| 第一个 | 形参名字符串（逗号分隔） | `"username,password,expect_code"` |
| 第二个 | 每组实参的列表，一行一组 | `[("alice","alice123",200), ...]` |

第二个参数（那个列表）就是 `cases`。`_load_login_cases()` / `_load_search_cases()` 做的事，就是"**把这个列表造出来**"。

### 用户问：「这个 `_load_login_cases()` 能不能不要？」

**能，完全可以。** 最直接的写法是把参数列表写死在装饰器里：

```python
@pytest.mark.parametrize("username,password,expect_code", [
    ("alice", "alice123", 200),
    ("alice", "wrong-pass", 401),
    ("nobody", "whatever", 401),
])
def test_login(username, password, expect_code, api_client):
    ...
```

那为什么还要绕一圈用函数 + yaml？为了**数据外置**（把「测试数据」和「测试逻辑」分开）：

| 做法 | 加一条新登录用例时 |
|------|------------------|
| 内联（写死） | 去改 `test_api.py` 里的 Python 代码 |
| 外置（yaml + 函数） | 只在 yaml 里加 3 行，代码一行不动 |

等商品、订单、边界值数据多起来，代码里堆满数据会很乱，所以提前养成"数据放 yaml、代码只留逻辑"的习惯。

### 用户问：「`for group in ("valid_login", "invalid_password", "unknown_user")` 这写法不好吧？」

**用户对。** 硬编码三个组名，恰恰是"数据外置没做彻底"——yaml 里加一组 `locked_user`，还得回代码补一个字符串，和外置的初衷自相矛盾。

正确写法是遍历 `data.items()`，让 yaml 的 key/value 自己驱动循环：

```python
for group, items in data.items():   # group = 组名(key)，items = 该组用例列表(value)
    for item in items:
        cases.append(pytest.param(..., id=item["id"]))
```

以后 yaml 加任意一组，代码一行不动。**数据外置要彻底：连"组名"都不该写死在代码里。**

---

## 二、pytest.param(id=)：给用例起业务名

### 本质

在 parametrize 的参数列表里，每个元素最朴素的样子是一个**元组**：

```python
("alice", "alice123", 200)     # 一个普通条目 = 三个参数值
```

但有时你想给这个元组**额外附加信息**（比如"这条用例叫什么名字"）。普通元组装不下，pytest 给了 `pytest.param(...)` 来包装：

```python
pytest.param("alice", "alice123", 200, id="合法登录")
#            └────── 三个参数值，和元组一样 ──────┘  └── 附加信息：名字 ──┘
```

**`pytest.param` 的本质：把「参数值 + 附加信息（id/标记等）」打包成一个对象**，塞进 parametrize 的列表。除 `id` 外还能装 `marks=pytest.mark.skip`（跳过某条）等。

### 三种起名方式 + 优先级

| 方式 | 写法 | 效果 |
|------|------|------|
| 默认 | `parametrize("a,b", [(1,2)])` | 自动拼 `test_x[1-2]` |
| `ids=` 列表 | `parametrize("a,b", cases, ids=["第一","第二"])` | 名字和值分两处、靠下标对应 |
| **`pytest.param(id=)`** | `[pytest.param(1,2,id="正常")]` | 名字紧挨着值，改数据不漏 |

**业界主流推荐 `pytest.param(id=)` 而非 `ids=`**：`ids` 靠「下标」和参数组一一对应，中间插一条用例就会全错位；`pytest.param` 把名字直接绑在数据上，最稳。ID 解析优先级：`pytest.param(id=)` > `ids` 列表 > `ids` 函数 > 自动拼参数值。

### 用户问：「id 显示在哪？」

`id` 决定参数化用例「**中括号里那一截**」，完整用例名 = `函数名 + [中括号]`：

```
不给 id：  test_login[alice-alice123-200]   ← pytest 拿参数值自动拼
给了 id：  test_login[合法登录]             ← 用你指定的名字
```

出现在三个地方：① 终端 `pytest -v` 的 `PASSED ... test_login[合法登录]`；② Allure 报告用例树；③ **失败摘要**（哪条挂了名字直接标出来）。价值：把「读不懂的参数值」换成「能读懂的用例名」。

---

## 三、断言设计：精确匹配 vs 只抓关键点

### 用户踩的坑

想写搜索测试时，第一反应是让 `expect` 等于搜索接口的**完整返回** `{"items": [8个商品的完整 dict...]}`，然后 `assert resp == expect` 精确对比。这有两个问题：

**问题 1：`resp == expect` 永远不成立。** `resp` 是 `requests.Response` 对象，`expect` 是 yaml 读出的 dict，类型都不同。真要精确比得写 `resp.json() == expect`。

**问题 2（更关键）：精确匹配完整返回是又脆又长的坏断言。** 每个商品有 `id/name/price/stock` 四字段，yaml 里要完整抄一遍；而且 `stock` 下单就变、`price` 改一分钱就红。这样的测试不是"发现 bug"，是"数据一风吹草动就误报"。

### 正确做法：只断言业务关键点

```python
@pytest.mark.parametrize("keyword,expect_count", _load_search_cases())
def test_search_products(keyword, expect_count, api_client):
    resp = api_client.products(keyword)
    items = resp.json()["items"]
    assert len(items) == expect_count                          # 关键点①：数量
    if keyword:
        assert all(keyword.lower() in item["name"].lower() for item in items)  # 关键点②：含关键词
```

这样 yaml 是**扁平的**（`expect_count: 3`），不用嵌套；断言只锁"数量"和"名字含关键词"两个业务关键点，price/stock 怎么变都不误报。

**面试可讲**：断言要抓「业务不变式」（搜索出来的东西必须含关键词、数量正确），而不是锁死「易变细节」（价格、库存），否则测试又脆又难维护。

---

## 四、附：yaml 嵌套语法（dict → list → dict）

如果以后真要写「字典 → 列表 → 字典」这种嵌套，规则就两条——**`-` 表示列表项，缩进表示层级**：

```yaml
match_many:
  - id: 匹配多个            # 列表项（是个字典）
    keyword: 罗技
    expect:                  # 值是另一个字典，换行缩进
      items:                 # 字典里的 key，值是列表
        - name: 罗技 K845 机械键盘   # 列表项（又是个字典）
          price: 299.0
        - name: 罗技 G304 无线鼠标
          price: 149.0
```

看缩进：`items:` 后面每一项用 `- ` 开头，`- ` 里的 `name`/`price` 再往里缩一层。层级靠缩进对齐，不靠大括号。

---

## 用户问过的问题速查

| # | 问题 | 一句话答案 |
|---|------|-----------|
| Q1 | `_load_login_cases()` 能不能不要 | 能，内联写参数列表也行；用函数+yaml 是为了"数据外置" |
| Q2 | `pytest.param` 是什么 | 把「参数值 + 附加信息(id/marks)」打包的对象 |
| Q3 | `id` 显示在哪 | 用例名中括号里，终端 -v / Allure / 失败摘要三处 |
| Q4 | 硬编码组名好不好 | 不好，是外置没做彻底；应 `for k,v in data.items()` |
| Q5 | 嵌套 yaml 怎么写 | `-` 表列表项，缩进表层级 |
