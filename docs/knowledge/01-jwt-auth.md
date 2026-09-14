# 01 · JWT 鉴权与登录

> 阶段0 · Task 1 ｜ 讲解 + 提问沉淀 ｜ 2026-08-15
> 核心术语：JWT /ˈdʒeɪ ˈdʌbəljuː ˈtiː/（JSON Web Token，念 J-W-T）、token /ˈtoʊkən/

## 1. 为什么需要 JWT

旧版登录用的是**假 token**（拼字符串 + 存字典）：

```python
token = f"token-{req.username}-{len(TOKENS) + 1}"
TOKENS[token] = req.username
```

三个坑：

| 坑 | 表现 | 后果 |
|----|------|------|
| 有状态 | token 存服务端内存字典 | 服务重启全失效 |
| 可伪造 | `token-alice-1` 格式一眼看穿 | 攻击者直接冒充 |
| 不过期 | 发出去永远有效 | 被偷了无解 |

**JWT 一句话**：一段"防篡改"的字符串，服务端用密钥签名，之后凭签名验证真伪，所以**不用存它**（无状态）。

## 2. JWT 的三段结构

```
eyJhbGciOi...  .  eyJ1c2VybmFtZSI6...  .  QPYxo18d...
     header     .       payload        .    signature
```

| 段 | 装什么 | 可读性 |
|----|-------|--------|
| header | 算法（HS256）+ 类型（JWT） | 明文（base64） |
| payload | 业务数据：`username`、`exp` | 明文（base64） |
| signature | header+payload 用密钥算的哈希 | 只有持密钥能算 |

**防篡改原理**：谁改了 payload，服务端用密钥重算 signature 就对不上 → 拒绝。

## 3. 过期时间 exp

- **为什么需要**：token 发出后收不回，设有效期让"被偷"的损失可控（临时通行证）。
- **exp 是什么**：payload 里的字段，值 = Unix 时间戳（有效期截止时刻）。
- **签发**：`exp = 现在 + 30分钟`，写进 payload 随签名锁死。
- **校验**：`jwt.decode` 内部自动拿 exp 和当前时间比，过期抛 `ExpiredSignatureError`。

⚠️ **过期 ≠ 篡改**：过期 token 的签名是对的，只是"时间到了"。签名管真伪，exp 管时效，两个独立检查。

## 4. 两个函数：签发 vs 校验

### _create_token（签发，登录时调一次）

```python
def _create_token(username: str) -> str:
    payload = {"username": username,
               "exp": datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
```

- `jwt.encode(payload, 密钥, algorithm=算法)`：把字典编码成防篡改字符串（PyJWT 库）。

### _require_user（校验，每次请求自动跑）

```python
def _require_user(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 token")
    token = authorization[len("Bearer "):]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="token 无效")
    username = payload.get("username")
    if not username or username not in USERS:
        raise HTTPException(status_code=401, detail="token 无效")
    return username
```

- `authorization[len("Bearer "):]`：切片切掉前缀，拿纯 token。
- `except` 顺序：`ExpiredSignatureError` 在前（它是 `InvalidTokenError` 的子类），否则过期会被"无效"抢先接住。

**签一次、验无数次、中间不存** —— 这就是 JWT 无状态的全部。

## 5. FastAPI 机制：Header() 与 Depends()

| 参数写法 | FastAPI 从哪取 |
|---------|--------------|
| `keyword: str = ""` | 查询参数（`?keyword=鼠标`） |
| `req: LoginRequest` | 请求体（body JSON） |
| `authorization: str = Header(None)` | **请求头**（headers） |

- **`Header()`**：按参数名去 headers 里取**那一个键的值**（不是整个字典）。参数名 `authorization` = header 键 `Authorization`。`None` 是默认值（没这个头就用 None）。
- **`Depends(_require_user)`**：这个参数不直接从请求取，先调 `_require_user`，用它的返回值。

## 6. HTTP 请求报文结构

```
POST /login HTTP/1.1                          ← ① 请求行（方法 + 路径 + 版本）
Host: 127.0.0.1:8000                         ← ② 请求头（Key: Value）
Content-Type: application/json               ←
                                             ← ③ 空行
{"username":"alice","password":"alice123"}   ← ④ 请求体（body）
```

- "请求行"和"请求头"是两码事：请求行只有第一行，请求头是下面那串 `键: 值`。
- 请求头种类很多：`Host`、`Content-Type`、`Authorization`（放 token）、`Cookie`… 格式统一 `Key: Value`。

## 7. pytest fixture 依赖注入

```python
@pytest.fixture()
def api_client(base_url):
    return ApiClient(base_url)

@pytest.fixture()
def login_user(api_client, config):   # ← 参数名 = 依赖声明
    resp = api_client.login(...)       # ← 用同一个 api_client 登录
    return {"token": ..., "username": ...}
```

- **发现依赖**：看 fixture 函数的**参数名**（参数名就是要注入的 fixture 名）。
- **同一对象**：同一个测试里，同名 fixture 只创建一次，多处引用拿同一个对象。所以 `login_user` 登录的那个 client，和测试函数里的 `api_client` 是**同一个**。
- **执行顺序**：拓扑排序，被依赖的先执行（config → base_url → api_client → login_user → 测试函数）。
- **本质**：跟普通函数调用一样——`login_user(api_client, config)` 想被调用，`api_client`、`config` 这两个实参必须先准备好。

## 8. 你问过的问题速查

| 你的问题 | 一句话答案 |
|---------|-----------|
| 为什么 me() 不用传参？ | login_user 已把 token 存进同一个 client 对象，me() 自动带 header |
| authorization 是字典吗？ | 不是，是 `Authorization` 单个头的**值**（字符串），所以用切片不是 `["Authorization"]` |
| 所有请求头都长一样吗？ | 格式都是 `Key: Value`，但种类很多，Authorization 只是其中一种 |
| 为什么不是先执行 login_user？ | 它依赖 api_client，参数没准备好就没法调用，只能先建被依赖的 |
