# ShopHub 商城测试平台

ShopHub 是个人测试练习项目：围绕模拟商城建立接口、网页和性能测试。被测系统（SUT，System Under Test）是测试所针对的 FastAPI + MySQL 后端，业务包括登录、搜索、加购、下单、模拟支付、按店铺发货和确认收货。

后端与部分测试由 AI 协作完成；仓库内容不证明本人独立编写、运行或掌握全部实现。本项目没有真实支付和企业上线经历。

## 新对话与长期维护入口

接手项目按 [AGENTS](AGENTS.md) → [项目状态](docs/项目状态.md) → [长期路线](docs/长期路线.md) 阅读，再核对实际代码与Git工作区。当前进度、最近验证和未定事项只在项目状态维护；路线图中的候选技术不代表已经采用。

例如，新对话可以这样开始：“继续ShopHub项目，请先读项目AGENTS、项目状态和长期路线，再从状态页的下一步接着讨论。”

## 项目组成

- `server/`：FastAPI + MySQL模拟商城，Celery + Redis支持周期关单。
- `api/`、`unit/`：接口测试与不依赖外部服务的单元回归，使用pytest组织执行。
- `ui/`：Playwright网页测试，页面对象封装页面操作，用例判断结果。
- `perf/`：Locust性能脚本、历史数据及核对报告。
- `docs/`：当前状态、路线、设计、业务参考与验收记录。
- `test_support/`：生成测试参数、准备业务状态、清理本例数据和记录失败诊断。
- `tools/run_tests.py`：每轮创建专用库与服务，执行并保存结果，再释放环境。

模块关系、关键机制和当前结构限制统一见 [架构说明](docs/架构说明.md)。

## 本地运行（Windows PowerShell）

推荐使用下面的执行入口。它读取`.env`中的MySQL连接信息，为每个测试进程创建唯一数据库和本机商城服务，执行结束后清理；账号需要建库、删库权限。接口及完整回归默认两个测试进程，各进程内部顺序执行。

```powershell
# 项目根目录：创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium

# 仅首次复制，随后在 .env 中填写专用环境的连接信息
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

```powershell
# 无需MySQL的单元测试
.\.venv\Scripts\python.exe -m tools.run_tests --suite unit -- -q

# 真实接口回归：MySQL需已启动，不用手动启动商城
.\.venv\Scripts\python.exe -m tools.run_tests --suite api -- -q

# 完整单元、接口与网页回归
.\.venv\Scripts\python.exe -m tools.run_tests --suite all -- -q --browser chromium

# 单进程对照：同一套环境管理和业务用例
.\.venv\Scripts\python.exe -m tools.run_tests --suite all --workers 1 -- -q --browser chromium
```

`--`之后是传给pytest的参数，`-k`按名称筛选、`-m`按标记筛选。执行器始终加入所选套件目录，额外传文件路径不会取代整个套件。`--workers`指定正整数并行数；纯单元默认1且不创建商城环境。`--run-id`是可读标签，资源所有权使用额外生成的内部随机标识；已有同名结果目录会被拒绝覆盖。运行器拒绝覆盖并行数、服务地址及报告位置的透传参数。

结果位于`reports/engineering/<运行编号>/`：`summary.json`记录版本、退出码和准备/执行/清理耗时，`junit.xml`是供工具读取的测试结果，`resources.json`是无密钥的资源清单；各`gw0/`、`gw1/`（测试进程编号）目录下的`service.log`记录对应服务日志，`cases.jsonl`记录实际分配及起止时间，`diagnostics/`保存失败用例的脱敏请求及阶段。网页失败另存截图与操作轨迹。失败诊断中的请求标识`X-Request-ID`可用于查找服务日志中的同一次请求。

例如接口返回错误时，先看summary的失败阶段，再从diagnostics找请求标识，在service.log中搜索该标识；不要直接把含真实密码的`.env`附进报告。

强杀或断电后，可用指定清单恢复：`.venv/Scripts/python.exe -m tools.recover_run --manifest reports/engineering/运行编号/resources.json`。恢复先核对并停止已登记的测试进程和商城服务，再删本轮库；身份不符时拒绝清理。不按数据库名前缀批量删除。原始日志保留用于诊断；课件本轮延期，完整实测见[E2验收](docs/quality-review-2026-11-15-e2.md)。

### 手动启动（需要自己管理环境时）

普通业务用例使用scenario（本例资源及业务状态准备器），不再自动清空全库；登录和搜索保留只读种子。手动方式只支持单进程，须自行确保专用库与服务匹配；并行使用正式运行器。支付收尾最多等待15秒确认服务端处理归零，再恢复开关和清理；清理失败会停止本轮。

```powershell
# 终端1：先确认该库可丢弃；初始化已支持DB_NAME，不再硬编码shop
$env:DB_NAME = 'shophub_manual_test'
$env:DEBUG_MODE = '1'
.\.venv\Scripts\python.exe -c "from server import main; main.db.init_db()"
.\.venv\Scripts\python.exe -m uvicorn server.main:app --port 8000 --no-access-log

# 终端2：同一数据库配置，测试结束后自行停止服务和管理库
$env:DB_NAME = 'shophub_manual_test'
$env:BASE_URL = 'http://127.0.0.1:8000'
.\.venv\Scripts\python.exe -m pytest

# 可选：安装 Allure 命令行工具后查看报告
.\.venv\Scripts\python.exe -m pytest --alluredir=reports
allure serve reports
```

只检查测试清单、不执行用例：`.\.venv\Scripts\python.exe -m pytest --collect-only -q -o addopts=''`。只运行单元回归：`.\.venv\Scripts\python.exe -m pytest unit -q -o addopts=''`。

## Docker 与流水线配置

```powershell
docker compose up --build --abort-on-container-exit --exit-code-from test
```

该命令按Compose配置启动5个服务，以test的退出码作为结果；使用本地演示凭据。Compose共享商城、数据库和定时任务，不支持E2的每进程隔离入口；本轮未运行Compose。

GitHub Actions的持续集成（CI /ˌsiː ˈaɪ/，代码变更后自动检查）先跑单元，再使用MySQL服务运行单进程对照及默认双进程完整套件，始终上传执行目录，保留14天。触发包括main/master/improve-test-design推送、拉取请求和手动执行。流水线复用上述执行器；配置存在不代表远程运行通过。MySQL 8认证依赖按[PyMySQL官方说明](https://pymysql.readthedocs.io/en/latest/user/installation.html)声明`PyMySQL[rsa]`。

## 测试数量与证据

默认收集范围为 `unit api ui`。参数化是同一用例按多组输入分别执行，例如数量1与100分别计一条。最近有记录的数量、执行环境、结果和未验证项统一见 [项目状态](docs/项目状态.md#最近验证与证据)，用例依据见 [用例设计](docs/test-cases.md)。

历史通过只证明对应版本和环境；配置存在不能证明容器或远程流水线运行成功。`reports/`和`test-results/`默认不随Git克隆获得，证据摘要与可获得性以阶段验收说明为准。

## 阅读入口

- [统一协作入口](AGENTS.md)：接手顺序、文档分工和维护规则。
- [项目状态](docs/项目状态.md)：当前目标、最近证据、限制和下一步。
- [长期路线](docs/长期路线.md)：阶段方向、依赖与尚未决定的选项。
- [架构说明](docs/架构说明.md)：模块关系、机制和限制。
- [决策记录](docs/决策记录.md)：重要选择及取舍。
- [用例设计与断言](docs/test-cases.md)：业务风险、具体输入、预期和用例对应关系。
- [数据库结构](docs/database-structure.md)：8张表、父子订单和数据变化示例。
- [需求草案](docs/requirements.md)：实现后补写、尚待本人逐条确认，不是企业需求评审记录。
- [质量验收](docs/quality-review-2026-11-09.md)：修复和运行证据、个人参与边界。
- [E1工程升级验收](docs/quality-review-2026-11-12.md)：本轮数据、诊断、环境及回归证据。
- [历史压测核对](perf/report.md)：原始数据、错误原因边界和同负载比较；请求速率与成功订单吞吐分开解释。
- [性能复验方案](PERF_TEST_PLAN.md)：如何保留版本、环境、错误与同负载对照。
