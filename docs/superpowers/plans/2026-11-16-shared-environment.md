# 共享环境与分阶段执行实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本轮只交付计划，不开始实现，不自动提交或推送。

**Goal:** 一轮只创建一个专用商城服务和数据库，普通用例并行，全局用例独占，保持业务断言、失败诊断和可靠回收。

**Architecture:** 请求局部支付模式与按用例登记的请求收尾消除服务端串扰；执行器以互不重叠的pytest子进程运行普通与独占阶段。环境只登记一次，测试进程与产物按阶段/worker分开。

**Tech Stack:** Python、pytest、pytest-xdist、FastAPI、requests、Playwright、PyMySQL、现有进程管理工具；不新增基础设施依赖。

**设计依据：**[完整设计及验收规则](../specs/2026-11-16-shared-environment-design.md)。2026-11-16，待实施；下列新接口、新文件、测试名均是拟定内容，不能当作当前能力。

---

## 使用规则与检查点

所有命令从仓库根目录D:/my-test-framework运行。每项按“先补能失败的行为测试→确认失败原因→最小实现→验证”的顺序推进。命令中的新测试文件必须先按对应步骤创建。基础设施替身测试只证明控制逻辑，不能代替真实共享服务/库验收。

本轮不因计划写作创建worktree；实施时优先复用当前合适工作区并隔离本轮改动。当前E2大量改动尚未提交，任何提交前均审阅逐块差异，不git add整个仓库，不回滚既有修改。是否提交/推送另依用户授权；不把计划中的提交检查点解释成发布授权。

## 任务1：冻结基线并完成逐例分类表

**涉及：** docs/项目状态.md、docs/verification/2026-11-15-e2.json、api/tests/*.py、ui/test_ui.py、data/search.yaml；新建docs/verification/2026-11-16-shared-classification.json。

- [ ] 保存git status、HEAD、Python及依赖版本、E2源码指纹；保留当前文件的可恢复快照，不覆盖reports内历史运行。
- [ ] 用当前入口执行一次E2单元和完整双进程基线，保存数量、退出码和回收结果。先确认数据库/浏览器可用；不可用如实记录，不继续宣称拥有新基线。

```powershell
python -m tools.run_tests --suite unit --run-id shared-before-unit
python -m tools.run_tests --suite all --workers 2 --run-id shared-before-all
```

- [ ] 逐条审计SQL、固定账号写入、直接requests、支付模式、线程与浏览器活动；分类表记录nodeid、parallel_safe/exclusive及理由。必须覆盖所有参数行。
- [ ] 初始独占5个函数按设计登记；搜索四类参数仍保留原数量检查。对发现的其他全局依赖更新设计与分类表，不能默认放行。
- [ ] 审阅并保存检查点；此时业务代码不变，分类表是计划证据而非已实现标签。

## 任务2：支付模式改为请求局部

**修改：** server/main.py、api/clients/order.py、api/clients/debug.py、conftest.py、api/tests/test_order.py、api/tests/test_order_integrity.py、api/tests/test_store.py。
**新增验证：** unit/test_payment_policy.py；真实验收用例放tools/verify_shared_cases.py。同步迁移unit/test_parallel_service.py中的全局变量替身测试，保留原业务断言。

- [ ] 写参数解析测试：未传success、显式三个合法值、非法/空/重复头422、非调试注入403；拒绝路径不进入支付数据库写入。
- [ ] 运行`python -m tools.run_tests --suite unit --run-id shared-policy-red -- -q unit/test_payment_policy.py`，确认失败来自尚未实现的解析器，而非导入/环境错误。
- [ ] 在server/main.py保留pay_order业务路径；局部解析请求头，按原顺序执行鉴权、订单归属/状态和支付分支。移除PAYMENT_MODE及设置接口。
- [ ] OrderMixin.order_pay签名改为`order_pay(self, order_id: int, *, payment_mode: str | None = None)`，每次复制鉴权头，仅本次传X-Test-Payment-Mode。

```python
# 支付失败测试的新写法：前置仍经真实下单接口获得。
response = api_client.order_pay(order_id, payment_mode='fail')
assert response.json()['status'] == 'failed'
# 已取消订单再支付：省略参数走默认路径，仍应被状态机拒绝。
assert api_client.order_pay(order_id).status_code == 400
```

- [ ] 迁移所有payment_mode fixture调用及额外创建的短超时客户端；删除旧fixture和DebugMixin.set_payment_mode，不保留静默兼容全局设置。
- [ ] 运行同一单元文件确认通过；真实并发正反例在任务9验证。变更期间暂不启用共享环境。

## 任务3：服务端用例登记与请求计数

**新增：** server/test_runtime.py、unit/test_case_runtime.py。
**修改：** server/main.py。

- [ ] 为状态机编写行为测试：open只接受新ID；close幂等；closing/closed拒绝新请求；每次业务请求只增减一次；异常路径释放；不同case互不影响；阶段切换需要零在途、零活动case。
- [ ] 先运行`python -m tools.run_tests --suite unit --run-id shared-runtime-red -- -q unit/test_case_runtime.py`确认预期失败。
- [ ] 实现CaseRuntime对象：以(run_id, case_id)索引，保存状态和request_id集合，使用线程锁原子保护短操作。具体公开方法固定为`open_case`、`close_case`、`enter_request`、`leave_request`、`case_state`、`run_state`、`set_phase`，禁止在锁中等待业务请求完成。
- [ ] 按设计注册5个debug控制接口；托管请求校验run/case，错误归属返回409，非调试控制路径不注册。服务单进程，不让模块全局登记误扩展到多服务worker。
- [ ] 以ASGI入口覆盖排队时间；处理中断且无法证明同步线程停止时标记污染。补同步支付超时、客户端断连的实际服务验证，不仅测字典增减。
- [ ] 为/debug/cancel-expired与/debug/reset增加exclusive阶段守卫；parallel请求409且不调用原数据库函数。
- [ ] 运行对应单元文件通过，核对正常非托管开发运行不要求测试头。

## 任务4：客户端身份传播和按例清理

**新增：** test_support/case_lifecycle.py、unit/test_case_lifecycle.py。
**修改：** conftest.py、unit/conftest.py、api/api_client.py、test_support/scenarios.py、test_support/pytest_plugin.py、ui/conftest.py、api/tests/test_auth.py。

- [ ] 写顺序测试：open发生在任何业务请求之前；关闭浏览器/线程在close之前；本例排空在SQL删除之前；超时及部分准备失败停止整轮；嵌套TestData不会关闭外层case。
- [ ] 运行`python -m tools.run_tests --suite unit --run-id shared-lifecycle-red -- -q unit/test_case_lifecycle.py`确认失败原因。
- [ ] 实现CaseLifecycle，属性为run_id、case_id、phase；方法为`open()`、`wait_idle(timeout=15)`、`close_and_wait(timeout=15)`、`headers()`、`register_cleanup(callback)`。控制客户端禁用自动写请求重试；等待使用单调时钟和有截止时间的轮询。
- [ ] 内部提供`wait_requests_idle(timeout=15)`，对外wait_idle本轮只委托HTTP排空；明确不覆盖Celery任务，调用者统一通过生命周期接口，不分散读取计数器。沿用普通run/case标识，未来可通过消息元数据传递；本轮不编写未使用的Celery适配器。
- [ ] 根目录新增case_lifecycle fixture；api_client、scenario显式依赖它；unit目录覆写为不连接服务。注册finalizer须在可能失败的准备之前建立，open响应丢失不能当作从未注册。
- [ ] ApiClient构造时捕获生命周期身份，支持显式`case_lifecycle=`；所有手动构造客户端与TestData传同一对象，工作线程复用已捕获身份，不在发送时读取可变_active。
- [ ] 仅本次请求头合并`X-Test-Run-ID`、`X-Test-Case-ID`和现有`X-Request-ID`；鉴权头和支付头各自保留。直接requests鉴权反例走统一本例请求封装，仍准确验证缺失/伪造Authorization。
- [ ] TestData.cleanup调用所属生命周期wait_idle后再归属删除；scenario只登记清理回调，不在自己的fixture finalizer立即删数据；最外层fixture执行close_and_wait，再逆序调用已登记清理。手动TestData上下文仍可提前清理，需先结束其工作线程；清理幂等。删除全局_quiesce和_restore_success。
- [ ] UI通过browser_context_args传本例头，首次导航前生效；上下文关闭先于生命周期关闭和数据删除。生命周期不能依赖api_client而形成循环。
- [ ] 运行新生命周期测试及现有unit/test_fixture_lifecycle.py、unit/test_diagnostics.py，确认事件顺序和异常证据完整。

## 任务5：分类插件与数据假设修复

**新增：** tools/test_phases.py、unit/test_phase_selection.py。
**修改：** pytest.ini、test_support/environment_plugin.py、conftest.py、api/tests/*.py、ui/test_ui.py。

- [ ] 写收集测试：api/ui每函数必须恰好一个分类；参数行继承；未分类/双分类失败；unit豁免；用户-k筛选后两阶段并集与应执行集合一致。
- [ ] 运行`python -m tools.run_tests --suite unit --run-id shared-selection-red -- -q unit/test_phase_selection.py`确认失败。
- [ ] pytest.ini注册parallel_safe和exclusive；逐个审计后加函数标签，初始独占清单见设计，不使用模块级parallel_safe默认。
- [ ] tools/test_phases.py实现收集清单解析、验证与按类别拆分；JSON字段固定为nodeid、classification、source。environment_plugin在collect-only时也校验并输出，不启动业务fixture。
- [ ] catalog.missing_product取本例另一店铺商品ID，并显式确认目标店铺无关联；删掉MAX(id)+1及相应断言。归属清理保留原事务及依赖顺序。
- [ ] 审计种子搜索、公共账号登录无业务写入的事实；全库明文检查继续独占，不改为只查一条以减少范围。
- [ ] 运行分类测试并核对预收集清单，任何遗漏作为错误处理。

## 任务6：一套环境、两个执行阶段

**修改：** tools/run_tests.py、tools/environments.py、test_support/environment_plugin.py。
**新增验证：** unit/test_shared_runner.py。

- [ ] 写运行器事件测试：只创建一个库/服务；普通阶段workers=N；独占阶段无xdist；屏障通过才启动独占；普通失败阻止后续；workers=1仍分阶段；空阶段不启动pytest；整体空选非零。
- [ ] 运行`python -m tools.run_tests --suite unit --run-id shared-runner-red -- -q unit/test_shared_runner.py`确认预期失败。
- [ ] 抽出阶段命令构造到tools/test_phases.py。普通阶段固定-n N --dist load --max-worker-restart=0（N=1不启用xdist），独占不传-n，均显式加载环境插件。
- [ ] all先跑无环境单元，再创建一套环境供api/ui；api只创建一套环境。单元失败时不创建商城。保存统一预收集清单和各阶段的实际nodeid报告。
- [ ] 参数解析禁止覆盖分类、进程、环境、产物和插件的透传参数；-k在预收集统一应用。阶段命令只传清单中的nodeid，检查Windows命令行长度，超过限制用本轮JSON清单由插件筛选，不拼接无限长命令行。
- [ ] schema_version=2清单只登记一个worker=shared的资源；环境在全部阶段复用。环境绑定插件将共享DB/URL与phase/worker产物目录分开，主控不做业务操作。
- [ ] 保留服务身份检查、高位端口重试和连接预算；最初保持DB_POOL_MIN=0、DB_POOL_MAX=10，压力不足时必须有连接峰值/等待证据再调整，不无限扩池。
- [ ] 运行新测试和现有unit/test_runner.py、unit/test_parallel_runner.py，更新旧“每worker建库”的预期，同时保留归属保护反例。

## 任务7：基线屏障、报告与恢复

**修改：** tools/run_tests.py、tools/recover_run.py、test_support/diagnostics.py、test_support/environment_plugin.py、unit/test_environment_stop.py、unit/test_diagnostics.py、unit/test_e2_acceptance.py。
**新增验证：** unit/test_shared_recovery.py、unit/test_phase_barrier.py。

- [ ] 编写屏障反例：仍有进程、仍有请求、closing用例、残留订单、种子字段变化、fatal标记，任何一种均阻止独占阶段。
- [ ] 保存初始化后的规范化种子基线；按稳定业务字段比较用户/店铺/商品/店铺商品，其余业务表验证无残留；忽略自增游标，不清库修复差异。
- [ ] 单元/普通/独占各保存JUnit和进程清单；产物路径固定run/phase/worker，服务日志run/service.log。报告记录phase、worker、case_id、request_id，不覆盖pytest的setup/call/teardown字段。
- [ ] 汇总核对nodeid无重复/遗漏，保留各阶段退出码、not_run和not_applicable；非空阶段exit=5作为错误。任一失败和回收错误保持非零。
- [ ] recover_run同时读旧E2和v2清单；v2逐个验证本轮进程清单路径，先停全部测试进程，再停唯一服务、删唯一库；重复恢复幂等，PID身份不符拒绝删除。
- [ ] 运行`python -m tools.run_tests --suite unit --run-id shared-barrier-green -- -q unit/test_phase_barrier.py unit/test_shared_recovery.py unit/test_environment_stop.py unit/test_diagnostics.py`，确认保护反例实际覆盖而非只检查字段存在。

## 任务8：定向真实链路验证

**新增：** tools/verify_shared.py、tools/verify_shared_cases.py、unit/test_shared_acceptance.py。

- [ ] 接受`python -m tools.verify_shared --group payment|lifecycle|phases|cleanup|faults|all`，每次创建自己的临时环境并finally回收，报告保存在reports/engineering对应唯一运行目录；工具不得连接已有开发库。
- [ ] payment：同一服务/库两个case用事件或屏障协调同时发起success/fail/timeout；验证各自订单、库存、明细及请求时间，不以“最终全绿”替代确实重叠的证据。
- [ ] lifecycle：1秒客户端超时对照6秒服务处理，证明等待只针对本例；close后迟到请求409且无写入；不能排空时停止全部worker并禁止删库直到服务停止。
- [ ] phases：普通阶段保留一个在途请求，独占阶段不得启动；制造归属泄漏必须阻止屏障；调用全局debug接口在普通阶段409；独占阶段关单保留三种订单筛选与重复执行检查。
- [ ] cleanup：两例并行创建，甲清理时乙状态完整；手动嵌套上下文、响应丢失后的已创建资源仍正确回收；业务竞争用例仍对同一商品发起两请求。
- [ ] faults：worker强制退出、服务启动失败、服务早退、阶段失败、中断、进程身份不符、删库失败、恢复重复执行。每个场景记录预期非零及最终资源状态，不把故障注入当成业务回归失败。
- [ ] tools/verify_e2.py、tools/verify_e2_cases.py保留为有日期的旧实现证据入口，启动时明确提示适用E2快照并拒绝用于新共享实现；不要把旧双环境支付隔离脚本悄悄当新方案验收。unit/test_e2_acceptance.py中仍适用的进程/归属保护迁入新验收测试，历史验收JSON不改写。
- [ ] 运行`python -m tools.verify_shared --group all`。合格输出必须包含真实服务和SQL检查证据；单元替身通过不算本任务完成。

## 任务9：完整回归与成本对照

**修改：** .github/workflows/ci.yml；新增docs/quality-review-2026-11-16-shared.md、docs/verification/2026-11-16-shared.json。

- [ ] 运行全部单元一次，修复后再进行必要的完整回归；新增单元数量按实收集记录，不硬编码原113/192。

```powershell
python -m tools.run_tests --suite unit --run-id shared-final-unit
python -m tools.run_tests --suite all --workers 1 --run-id shared-final-one
python -m tools.run_tests --suite all --workers 2 --run-id shared-final-two-a
python -m tools.run_tests --suite all --workers 2 --run-id shared-final-two-b
python -m tools.run_tests --suite all --workers 2 --run-id shared-final-two-c
```

- [ ] 确认原业务用例全覆盖、分类清单无遗漏/重复、请求归属正确、阶段不重叠、本轮库和服务无残留。4进程仅跑定向冒烟。
- [ ] 核实本轮入口未启动Celery/beat，任何HTTP排空证据没有被描述成后台任务排空；后续异步准入条件按设计第12节保留，避免直接复用带--beat的Compose入口污染普通阶段。
- [ ] CI保持单元前置和workers=[1,2]矩阵，每个矩阵任务各有自己的整轮独立环境；上传各阶段报告、归属清单、屏障差异与脱敏日志。远程未运行时明确未验证，不把配置修改当CI通过。
- [ ] 成本比较使用任务1可追溯E2快照与新实现，在同机相近负载交替各3次，记录准备/执行/清理/总耗时、服务数、库数、连接峰值、测试数量与负载。代码版本不同以及新增测试数量变化必须说明，不把未校正耗时当严格提速结论。
- [ ] 保存可随克隆获得的脱敏验收摘要，说明reports原始产物实际可获得位置。未测项目明确列出，不预填任何结果。

## 任务10：文档、交接与切换默认方案

**修改：** README.md、AGENTS.md、docs/架构说明.md、docs/决策记录.md、docs/长期路线.md、docs/项目状态.md、docs/test-cases.md；学习目录相关课件在实现稳定后同步。

- [ ] 全部验收满足后才把共享方案写成当前实现，标记ENG-002被新方案替代但保留历史事实。更新“禁止共享库/服务”等现行规则，说明只允许本轮经过分类控制的共享。
- [ ] README说明一个入口自动执行两阶段、筛选/空阶段语义、单元无环境、恢复命令；不用要求用户手动记两条pytest命令来保证互斥。
- [ ] 更新课件数据流、请求局部支付和阶段屏障；不把项目实现记录成用户已掌握。项目进度只写项目状态，求职表不重复记录开发任务。
- [ ] 检查链接、命令与当前实现一致，核对本轮产物脱敏和临时资源清理。审阅本轮diff，保留其他人的未提交修改。
- [ ] 交接列出测试证据、未验证边界、回退快照。验收失败继续维持E2现状，不宣称共享方案已完成；不自动merge/push。

## 方案自审

- 支付局部化对应任务2；在途归属/迟到请求对应3—4；分类和搜索全库假设对应1、5；阶段和单环境对应6；基线/恢复/报告对应7；实际证据对应8—9；正式切换对应10。
- 设计中的模式头、run/case头、状态字段和方法名保持一致；未声称新模块或命令已经存在。
- 当前只完成方案写作及文档核查；所有实施复选框保持未勾选，任何通过数据均待实际运行。
