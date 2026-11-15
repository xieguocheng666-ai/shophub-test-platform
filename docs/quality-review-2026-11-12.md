# 2026-11-12 · E1测试工程升级验收

## 范围与版本

本轮交付数据工厂、真实接口场景准备与归属清理、失败诊断、独立环境执行入口、代表用例迁移与持续集成配置。业务规则未修改，不增加能力记录。依据见[E1设计](superpowers/specs/2026-11-12-engineering-e1.md)、[实施计划](superpowers/plans/2026-11-12-engineering-e1.md)、[技术决策](决策记录.md#eng-001e1采用轻量数据工厂与每轮独立环境)。

按用户要求，先将既有文档和历史更正推送到GitHub的improve-test-design分支，提交为`09527761f85b7406ff6e253053dade03c4632df0`，已核对远端一致。E1在此基线上实施；验收时工作区有未提交修改，尚未再次推送。下文不代表0952776本身的测试结果。

环境：Windows、Python 3.11.9、本机MySQL 8.0.43、Chromium。每次真实回归使用执行器新建的库和单个服务。最后完整回归在补齐PyMySQL认证依赖及查询参数脱敏修复后运行。

## 最终结果

| 验证 | 实际结果 | 本机原始目录（不随克隆获得） |
|---|---|---|
| 完整单元/接口/网页 | **155通过，0失败、0错误、0跳过**；76单元、74接口、5网页；pytest 43.22秒 | reports/engineering/e1-full-final |
| 迁移用例逆序 | 10通过，64未选中；pytest 3.46秒 | reports/engineering/e1-owned-reverse |
| 同库同服务逆序重复两轮 | 20通过，128未选中；pytest 6.20秒 | reports/engineering/e1-owned-repeat |
| 真实接口故意金额断言失败 | 1条预期失败，退出1；7次请求，准备通过/执行失败/清理通过 | reports/engineering/e1-expected-failure |
| 商品归属复现与修复 | 修复前2失败；修复后2通过，72未选中 | reports/engineering/e1-ownership-red、e1-ownership-green |
| 资源复核 | information_schema确认8个本轮临时库均不存在；8个服务进程编号均不存在 | [证据JSON](verification/2026-11-12-e1.json) |

完整执行器记录准备2.344秒、执行44.016秒、清理0.140秒，退出0。执行阶段包含pytest子进程启动开销，与pytest自身计时不同。本轮不据此宣称相对旧版本提速。

中途还运行过72条接口、152条全量及执行器单元回归。`e1-runner-unit-final`这个中途目录曾有2条失败，暴露流式响应被诊断提前消费及用例外请求迟到串入下一用例的问题；均已修复并纳入最后155条通过结果。目录名中的final不证明是最终版本，以本节明确的e1-full-final为准。摘要保留中间结果，不删去或算成最终成功。

## 命令与复现

在项目根目录执行，先按README安装依赖并配置可建删库的MySQL连接：

```powershell
.venv/Scripts/python.exe -m tools.run_tests --suite all --run-id e1-full-final -- -q --browser chromium --tb=short
```

已有同名目录会被拒绝覆盖；复验使用新的run-id。

逆序验证临时增加收集钩子：收集完成后把用例列表反转；重复验证把反转后的列表重复两遍。只选择owned_data标记的10条迁移用例，不运行旧的全库重置用例。每次执行仍调用完整准备与清理；两轮共20次，在同一个库和服务中完成。临时钩子已清理。

```python
# 临时reports/engineering/probes/conftest.py的逆序重复钩子
def pytest_collection_modifyitems(items):
    items[:] = list(reversed(items)) * 2
```

```powershell
# 单轮逆序用items.reverse()，对应run-id e1-owned-reverse
.venv/Scripts/python.exe -m tools.run_tests --suite api --run-id e1-owned-repeat -- reports/engineering/probes -m owned_data -q --tb=short
```

故意失败通过临时用例创建已支付订单，再把SQL取得的正确金额故意断言为-1。这用于验证测试工程，不是产品缺陷：

```python
import pytest

@pytest.mark.owned_data
def test_e1_failure_probe(scenario, db):
    buyer = scenario.buyer()
    product = scenario.product(scenario.seller(), price="19.90", stock=5)
    order = scenario.order(buyer, [(product, 2)], state="paid")
    actual = db.scalar("SELECT total FROM orders WHERE order_id=%s", (order.order_id,))
    assert actual == -1, "intentional amount probe"
```

```powershell
.venv/Scripts/python.exe -m tools.run_tests --suite api --run-id e1-expected-failure -- reports/engineering/probes/test_e1_failure_probe.py -k test_e1_failure_probe -q --tb=short
```

已核对该次诊断的7个请求标识全部能在服务日志找到，Authorization和登录响应token均脱敏，库和服务均释放。该试验早于最后查询参数修复；三种参数回归包含在最后155条中。准备失败、清理失败及业务/清理同时失败通过真实pytest子进程探针验证，见[失败报告测试](../unit/test_failure_reporting.py)。

## 审查发现与处理

独立规格和代码审查发现并处理以下边界，修复后无剩余已确认的P1/P2问题（指应优先处理的严重或实质功能问题，不代表系统没有缺陷）：

| 发现 | 修复及证据 |
|---|---|
| 按店铺关联清理可能误认共享商品 | 创建前拒绝已有同名商品，记录创建编号及候选名称；真实库2条先失败后通过 |
| 普通ExceptionGroup无法同时保留KeyboardInterrupt与清理错误 | 改用BaseExceptionGroup，单元复现并验证两类异常均保留 |
| MySQL 8认证可能需要RSA扩展 | 声明PyMySQL[rsa]，按[官方说明](https://pymysql.readthedocs.io/en/latest/user/installation.html)核对并本机安装；不冒充远程认证已验收 |
| 列表查询参数可能泄漏令牌 | 统一处理字典、键值对列表、编码字符串，保留重复键并脱敏；3条新增回归先失败后通过 |
| 流式响应、请求迟到及默认日志行为 | 不消费流式响应；发送前捕获用例上下文；显式配置服务日志，含真实uvicorn子进程单元验证 |

创建/清理使用事务与外键保护，不关闭约束。业务状态通过真实接口准备，金额和库存用独立SQL及明确输入计算预期。旧功能断言保留；未以跳过、降低断言或自动重试失败测试获得通过。

## 证据保存与交接边界

[证据JSON](verification/2026-11-12-e1.json)随仓库保存，含各次JUnit计数、执行器摘要、原始XML哈希、资源复核及本轮23份代码/配置文件的SHA-256指纹。哈希用于对比版本，不能还原缺失报告。原始JUnit、日志、失败诊断仅保存在本机reports/engineering，该目录被Git忽略；其他电脑若缺少原文件，应明确原始文件不可获得。未上传这些原始材料。

临时失败用例、逆序钩子及一次性核查脚本在交付前删除；必要证据保留。交付检查：11份本轮Markdown的101个本地链接通过，代码/配置文件指纹一致，git diff --check通过。

尚未验证：远程GitHub Actions、Docker Compose、真实Celery调度与消息交付、多用例并行及性能实验。流水线已改并静态核对，需后续推送后观察真实执行与产物。E1本机验收不自动启动E2—E5。

## 2026-11-15提交前复核

用户授权推送本轮升级。23份代码/配置指纹与本记录验收版本一致；执行`.venv/Scripts/python.exe -m pytest unit -q -o addopts=''`，76条通过（5.91秒）；git diff --check通过。本次提交前未重跑真实接口/网页，11-12的155条结果仍标为历史验收。推送及远程流水线最新状态见[项目状态](项目状态.md)。
