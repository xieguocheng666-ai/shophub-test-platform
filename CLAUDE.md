# my-test-framework 开发指导

> 秋招测开核心项目：电商核心链路（登录→搜索→购物车→下单）接口 + UI 双覆盖测试框架。
> 被测系统为自写本地 FastAPI 电商，接口（requests）与 UI（Playwright/POM）双测，上 GitHub + CI 绿灯。

## 一、项目定位

- **用途**：简历项目 + 模拟面试深挖素材。面试官能追到第三四层，所以每个模块必须能讲透。
- **被测系统（SUT）**：自写 FastAPI 电商后端（`server/`），内存存储，接口/UI 都测它。
- **为什么自写**：数据可控、接口和 UI 都能测、能讲系统架构。

## 二、技术栈

| 层 | 技术 |
|----|------|
| 被测系统 | Python + FastAPI + uvicorn + MySQL 8.0（PyMySQL 数据访问层） |
| 接口测试 | pytest + requests + yaml 数据驱动 |
| UI 测试 | Playwright + POM（base_page 基类） |
| 报告 | Allure |
| CI | GitHub Actions（.github/workflows/test.yml） |

## 三、目录结构

```text
my-test-framework/
├── server/              # 被测系统：FastAPI 电商后端 + 静态页面
│   ├── main.py          # 入口：认证/商品搜索/购物车/订单/支付（完整电商闭环）
│   └── db.py            # 数据访问层：PyMySQL 原生 SQL + 原子扣库存 + 密码哈希
├── config/config.yaml   # base_url + 测试账号（密钥已移 .env）
├── .env                 # DB 连接 + SECRET_KEY（不进 git）
├── data/                # 测试数据（yaml，参数化）
├── api/
│   ├── api_client.py    # requests + Session + 重试封装
│   └── test_api.py      # 接口测试用例
├── ui/
│   ├── pages/           # POM 页面类（base_page.py + 业务页面）
│   └── test_ui.py       # UI 测试用例
├── docs/                # 文档（定位见「十、文档管理」）
│   ├── teaching-progress.md   # 教学进度真相
│   └── knowledge/       # 知识点沉淀（NN-主题.md）
├── utils/logger.py      # 日志配置
├── reports/             # Allure 报告输出
├── .github/workflows/test.yml
├── conftest.py          # 全局 fixture
├── pytest.ini
└── requirements.txt
```

## 四、里程碑（计划 vs 实际）

| 阶段 | 时间 | 内容 | 状态 |
|------|------|------|------|
| 骨架 | 8/12 | 目录 + conftest + data + requirements + pytest.ini | ✅ 8/12 |
| API 模块 | 8/13 | api_client + test_api + 数据驱动 | ✅ 8/13（6 passed） |
| SUT 完整搭建 | 8/14 | 电商 10 接口 + 3 前端页面（登录/首页/购物车） | ✅ 8/14 |
| 0 · SUT 核心改造 | 8/14-17 | JWT 鉴权 + 订单状态机 + 模拟支付网关 + 并发库存 + 越权校验 | ✅ 8/14 |
| 1 · 接口测试深化 | 8/18-24 | 全链路接口测试 + 数据驱动 + Allure + 并发竞态 | 🔜 |
| 2 · UI 模块 | 8/25-31 | base_page + 页面对象 + UI 全链路 + 失败截图/trace | 🔜 |
| 3 · CI + 收尾 | 9/1-7 | GitHub Actions + README（背景→方案→效果）+ 模拟面试 | 🔜 |
| 4 · 容器化 + 异步调度 | 投递后迭代 | Docker 容器跑测试（环境隔离）+ Celery/Redis 异步调度 | ⬜ |
| 简历 + 投递 | 9 月中 | 简历 v1（STAR 法则）+ 秋招投递 | 🔜 |

> 8/18 完工线取消，投递顺延至 9 月秋招高峰；项目作为秋招期间持续迭代的核心作品。
>
> 实际进度以 `D:\学习计划与进度\progress-tracker.md`（开发）+ `docs/teaching-progress.md`（教学）为准，本表仅作规划参考。

## 五、协作分工（ownership 留用户）

- **我来做**：搭框架、写主体代码、逐行讲解表。
- **用户亲手做**（面试深挖不露馅的关键）：
  1. 被测系统选型拍板（已定：本地 FastAPI）
  2. 加测试用例（≥3 个）
  3. 改 bug + 加小功能（我留坑给用户修）
  4. GitHub 部署 + CI 配通 + README"效果"部分
  5. 模拟面试（我当面试官追问）
- **不替用户拍板决策**；每个关键模块配逐行讲解表；明确列出预留的亲手环节。

## 六、开发规则

1. 每个关键代码模块配"代码 | 逐行解释"表，不"给了代码自己悟"。
2. 抽象概念用 ASCII 图结构化（架构、数据流、组件关系）。
3. 先讲"为什么需要"再给方案；先点旧工具局限，再引新概念。
4. 首次出现的英文术语带音标（如 fixture /ˈfɪkstʃər/）。
5. 多选项场景给"什么场景选什么"对照表（如 logger 级别、断言方式）。
6. 示例代码贴近工程：不写死值、配置分离、命名真实（不用 foo/bar）。
7. 每个新模块先交代"核心操作哪个对象、怎么拿"再教用法。
8. 概念节奏：每节只引入 1-2 个新概念，不一次性倾泻。
9. 渐进式标准：评估进度/代码用"当前阶段目标"当尺子，不用最终行业标准要求当前阶段；接受随阶段推进逐步达到行业标准（如并发边界留到阶段3 讲、测试盲区留到 Task 4/5 补）。

## 七、如何运行

```bash
# 启动被测系统（终端 1）—— 必须带 DEBUG_MODE=1，否则 /debug/* 故障注入路由不注册，测试会因 reset 失败全挂
DEBUG_MODE=1 .venv/Scripts/python -m uvicorn server.main:app --reload

# 健康检查
curl http://127.0.0.1:8000/health

# 跑测试（终端 2）
.venv/Scripts/python -m pytest

# Allure 报告
.venv/Scripts/python -m pytest --alluredir=reports
allure serve reports
```

## 八、进度

- 开发进度 → `D:\学习计划与进度\progress-tracker.md`
- 教学进度 → `docs/teaching-progress.md`

## 九、交流偏好

- 对话用简体中文；本项目代码注释用中文（用户边学边看，注释即讲解）。
- 心理健康优先于进度：不催、不施压，状态差先处理状态。

## 十、文档管理（2026-09-02 确立）

> 通用原则（文档四类 / 单一事实来源 / 更新契机）见全局 CLAUDE.md「文档管理规则」。本节只列本项目文档地图。

### 文档地图（打开项目先读这列）

| 文档 | 定位 | 什么时候读 |
|------|------|-----------|
| `docs/teaching-progress.md` | **教学进度唯一真相**（task/概念级） | 想知道"教到哪了" |
| `D:\学习计划与进度\progress-tracker.md` | **整体学习进度唯一真相**（阶段级） | 想知道"整体到哪了" |
| `docs/database-structure.md` | 数据库结构参考手册 | 讲数据结构/表/数据流 |
| `docs/knowledge/NN-*.md` | 已讲知识点的沉淀（写后只读） | 复习某概念 |
| `docs/superpowers/` | 阶段0 设计+实施计划（历史快照，已过时） | 复盘阶段0 当时怎么设计 |
| `docs/mysql-role-refactor.md` | 8-31 MySQL 化变更记录（已完成） | 复盘 MySQL 化决策 |
