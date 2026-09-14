# 06 · Allure 报告

> 阶段1 接口测试深化 · Task 1
> 日期：2026-08-26

## 为什么需要 Allure

`pytest -v` 的控制台输出只能自己看"过了没"，两个场景搞不定：

1. **给别人看**（面试官/同事）：看不出测试结构、覆盖了什么业务
2. **失败排查**：只有报错堆栈，看不到请求/响应、中间步骤

Allure（/əˈljʊər/）是**业界测试报告的事实标准**，把 pytest 结果渲染成网页版可视化报告。

## 核心概念：分类层级

```
feature /ˈfiːtʃər/  功能模块（粗）      "鉴权"
   └── story /ˈstɔːri/  具体场景（细）   "缺失 token"
          └── 测试用例                    test_me_without_token
```

| 装饰器 | 作用 |
|--------|------|
| `@allure.feature("鉴权")` | 哪个大功能模块 |
| `@allure.story("缺失 token")` | 模块下的哪个具体场景 |

**一句话：feature 是"哪个模块"，story 是"模块里的哪件事"。**

## 运行命令

```bash
# 跑测试，结果写入 reports/
pytest --alluredir=reports --clean-alluredir

# 渲染成 HTML 报告
allure generate reports -o reports/allure-report --clean

# 启动本地服务看报告（报告需 HTTP 服务渲染，file:// 打开会白屏）
python -m http.server 8080 -d reports/allure-report
# 或：allure serve reports
```

## 踩坑记录

- **编辑器缓存**：文件被外部（Claude）修改后，VS Code 不会自动刷新，若用旧缓冲保存会覆盖外部改动。务必"关闭重开"或点"重新加载"。
- **报告需 HTTP 服务**：allure 生成的 HTML 用 `fetch` 加载数据 json，直接双击 `index.html` 用 `file://` 打开会因 CORS 白屏。

## 用户问过的问题

- "我没看到鉴权组模板" → 实际是编辑器缓存问题，文件磁盘上已正确，需刷新编辑器。
