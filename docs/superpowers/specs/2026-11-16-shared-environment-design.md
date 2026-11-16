# 共享商城与数据库、分阶段执行改造设计

日期：2026-11-16。状态：用户已选择总体方向并要求详细方案；本文具体接口与实施细节为助手拟定，尚未实施、尚未运行验收。当前仍执行E2每进程独立环境方案，不把本文写成已替代事实。

## 1. 目标、边界与术语

一轮接口/完整测试只创建一个专用临时数据库和一个单进程商城服务；普通用例由两个测试工作进程并行执行；影响或依赖全局状态的用例在其他测试全部退出后单进程执行。不同运行轮次仍各有独立库和服务，绝不连接日常开发库或压测库。

- worker /ˈwɜːrkər/：实际执行测试的工作进程，例如gw0、gw1。
- case /keɪs/：一次用例执行；同一用例重跑必须分配新标识。
- namespace /ˈneɪmspeɪs/：用唯一前缀区分本例创建的数据，沿用现有工厂。
- fixture /ˈfɪkstʃər/：pytest负责调用的准备和清理函数。
- barrier /ˈbæriər/：阶段屏障；上一阶段进程、请求和清理全部结束，下一阶段才能启动。
- 独占：该阶段内只有当前一条业务用例操作本轮库和服务。并非只把几条用例放进同一个worker。

不接入真实Celery/共享队列，不增加账号资源池、分布式锁或容器依赖，不改变订单业务状态机。不承诺提速比例，也不把减少环境数量等同于减少总实现复杂度。

## 2. 当前代码依据与冲突清单

已核查工作区：分支improve-test-design，HEAD dbe9c2c；存在大量E2未提交修改和未跟踪文件，详见当前git status。未来提交或回退必须按本轮文件/代码块选择，不能git reset或整文件覆盖既有工作。

| 现状/风险 | 实际位置 | 拟处理 |
|---|---|---|
| PAYMENT_MODE为进程全局变量 | server/main.py支付与debug接口 | 删除全局可变支付模式，改为本次请求参数 |
| payment_mode准备函数调用全局设置接口 | conftest.py、api/clients/debug.py、3份业务测试文件 | 支付调用处显式传payment_mode；删除旧设置接口与准备函数 |
| 超时支付客户端1秒退出，服务仍睡眠6秒 | api/tests/test_order.py::test_pay_timeout_preserves_order_and_stock | 本例请求排空后才能清理数据 |
| 支付在途计数统计整个服务 | server/main.py、conftest.py::_quiesce | 改为按case_id追踪业务HTTP请求 |
| 超时关单扫描全库 | server/db.py::cancel_expired_orders | 保留扫描规则，相关用例独占执行 |
| 搜索条数依赖全库快照 | test_auth.py::test_search_products、data/search.yaml | 全部参数行独占执行，保留精确数量与匹配检查 |
| 密码明文检查遍历全库 | test_auth.py::test_password_stored_hashed | 独占执行，保留原检查范围 |
| MAX(id)+1不能稳定代表不存在的编号 | conftest.py::catalog | 改用本例已创建、明确未在目标店铺上架的商品编号 |
| 每worker绑定独立库、地址、目录 | test_support/environment_plugin.py | 共享环境身份，仍分开phase/worker产物目录 |
| 清理按买家、店主、商品归属删除 | test_support/scenarios.py | 保留并发核查，不改成全库重置 |
| UI每例独立浏览器上下文 | ui/conftest.py、ui/test_ui.py | 保留，额外为同源业务请求传递case_id |

先做全量静态审计；以上不是声称穷尽所有风险。全库计数、宽泛快照、固定账号写入、隐式共享商品、全表修改均需逐项给出分类或改法。

## 3. 总体流程与运行入口

```text
python -m tools.run_tests --suite all --workers 2
  → 单元阶段：无业务环境，独立pytest进程
  → 单元通过后创建一套本轮库/商城，核对/health归属
  → 收集接口/UI用例，验证分类，保存应执行清单
  → 普通阶段：gw0、gw1 → 同一个商城 → 同一个库
  → 屏障：全部测试进程退出、本例清理成功、服务在途归零、基线核对
  → 独占阶段：main逐条运行全局用例，使用同一商城与库
  → 归并报告 → 停止测试及商城进程 → 删除本轮库
```

--suite api只运行接口两阶段；--suite unit仍无商城环境；--workers 1仍使用两阶段分类，只把普通阶段改为单进程。--workers 2仅影响普通阶段，不增加环境数量；默认仍为2。先验收1和2，4仅做冒烟，不声明完整支持。

单元、普通、独占任一阶段失败即停止后续阶段，保留已完成报告并回收资源；没有执行的用例记为not_run，不伪记通过或pytest跳过。共享环境一旦污染，停止整轮，禁止只继续其他worker。

## 4. 支付行为：只对本次请求有效

公开业务路径仍为POST /order/{order_id}/pay。拟定测试请求头X-Test-Payment-Mode；允许success、fail、timeout。未提供时使用success；空值、未知值、重复冲突值返回422且没有业务写入。非DEBUG_MODE=1收到该测试头时返回403；正常不带测试头的业务行为不变。原鉴权、归属与订单状态检查保持有效。

测试客户端接口拟定为：

```python
def order_pay(self, order_id: int, *, payment_mode: str | None = None):
    headers = self._headers()
    if payment_mode is not None:
        headers['X-Test-Payment-Mode'] = payment_mode
    return self._request('POST', f'/order/{order_id}/pay', headers=headers)
```

headers是本次调用新建的字典；不修改session.headers或客户端共享默认值。服务端读取后保存在函数局部变量中，不写模块全局变量。服务端校验仍必需，客户端不能替代它。

```python
# 改造后的用例调用示例；不是当前已实现API。
failed = api_client.order_pay(order_id, payment_mode='fail')
again = api_client.order_pay(order_id)  # 未指定，正常支付路径
```

第二次请求仍须经过订单状态检查；已取消订单不能因为默认成功而重新支付。超时分支保留现有业务预期和等待时间，不自动重放支付写请求。删除PAYMENT_MODE、/debug/payment-mode、set_payment_mode及payment_mode fixture，避免两套控制方式并存。旧/debug/payment-state不再暴露全局模式；其全局在途能力迁入下节接口。

## 5. 请求归属、超时收尾与关闭入口

仅删除全局开关不能解决客户端超时后服务继续处理的问题。本轮增加轻量内存登记，不增加数据库表：键为(run_id, case_id)，值为open/closing/closed状态、在途请求数及请求标识；用服务内短临界区锁保护登记，不锁住整段业务执行。

拟定接口（仅DEBUG_MODE=1可用）：

| 接口 | 行为 |
|---|---|
| POST /debug/cases/{case_id}/open | 注册新用例；重复注册拒绝409；记录本轮身份 |
| POST /debug/cases/{case_id}/close | 原子切换为closing，拒绝该case后续新业务请求，重复关闭幂等 |
| GET /debug/cases/{case_id}/state | 返回状态、in_flight和本例未完成请求标识，不返回密码/token |
| GET /debug/request-state | 运行器读取全服务在途数与未关闭用例数，供阶段屏障检查 |
| POST /debug/execution-phase | 运行器切换parallel/exclusive；存在活动用例或请求时拒绝切换409 |

业务请求头使用X-Test-Run-ID和X-Test-Case-ID；它们用于本轮归属校验，不宣传为公网身份认证。运行器创建服务时开启TEST_MANAGED_RUN=1：业务请求必须提供已注册且属于本轮的case_id；/health、静态资源和控制接口不参加业务请求计数，控制接口另校验本轮身份。非托管开发运行不要求case_id，不改变正常浏览器用法。

登记覆盖准备、执行中的业务HTTP请求，不能只登记支付。计数在进入鉴权/线程池排队前增加，在实际处理完成后减少。实现时必须验证同步处理函数在客户端断开后仍运行的情形；不能仅因协程取消或连接断开就宣布后端写入完成。若检测到取消而无法证明底层处理已结束，登记为污染并停止本轮，而不是提前删数据。

每例收尾顺序：

```text
结束并join本例工作线程/关闭浏览器上下文
 → close本例，阻止迟到请求
 → 最多15秒等待本例in_flight归零
 → scenario按归属清理
 → 标记closed并保留拒绝迟到请求所需记录，直到整轮服务退出
```

case_id在生命周期fixture中生成随机UUID，不使用可能重复的nodeid充当身份。所有ApiClient在创建时捕获该身份并固定保存；工作线程不能在发送时读取已经切换到下一例的全局活动用例。显式requests调用也须通过本例请求封装附加标识；缺失/伪造token测试仅改变Authorization，保留归属头。

生命周期fixture由api_client、scenario及UI边界显式依赖；它本身使用控制客户端，不能依赖api_client造成循环。普通scenario把清理回调登记到生命周期对象，其fixture结束时不立即删数据；生命周期最外层先关闭入口并排空，再按登记逆序执行这些回调，避免依赖fixture后释放反而导致先删数据。每个TestData.cleanup（包括手动嵌套上下文）在删除自己资源前等待所属case当前请求归零；手动上下文退出前须结束其工作线程，只有最外层用例生命周期关闭case，内层清理不能关闭外层仍在使用的case。scenario和UI具体finalizer顺序以事件顺序测试验证，不只靠注释。

UI在首次导航前通过browser_context_args设置本例请求头；上下文逐例创建和关闭，覆盖页面脚本发起的同源请求。Web页面生产代码不新增测试逻辑。单元阶段覆写生命周期fixture，不访问控制接口。

## 6. 分类规则及初始清单

注册parallel_safe /ˈpærəlel seɪf/（可安全并行）与exclusive /ɪkˈskluːsɪv/（独占）两个标签。api/ui每个测试函数必须明确二选一；不允许目录默认parallel_safe掩盖新增全局用例，也不允许缺标签默认并行。参数化各行继承函数标签。unit独立阶段免此分类。

parallel_safe要求：所有可变资源归本例；查询/断言限定本例数据或只读稳定基线；不触发全局操作。读取操作若依赖全库数量、排序或快照稳定，同样需要exclusive。

初始exclusive清单：

| 文件/函数 | 原因 |
|---|---|
| api/tests/test_order.py::test_cancel_expired_idempotent | 全库关单及全库取消数量 |
| api/tests/test_e2e.py::test_cancel_expired_orders | 全库关单 |
| api/tests/test_quality_regressions.py::test_cancel_expired_only_cancels_old_pending | 全库扫描和筛选规则 |
| api/tests/test_auth.py::test_search_products | 所有参数行精确依赖公共商品总量 |
| api/tests/test_auth.py::test_password_stored_hashed | 全库明文密码检查 |

此清单为5个函数，参数化后用例数由实际收集确定。其他用例逐条审计再加parallel_safe，不直接宣布剩余全部安全。全库扫描不改为按指定订单关单，不削弱精确数量断言以迁就并行。

运行器先收集用户过滤后选中的全部测试，保存nodeid、分类、源文件及阶段。两阶段使用保存的nodeid分发，检查集合互斥且并集等于应执行集合。禁止透传-m、-n、--dist、-p等覆盖自身分类/执行控制的参数；保留-k、明确节点选择等受控筛选，需在预收集时统一生效。

空阶段由预收集识别为not_applicable而不启动pytest；非空预期阶段返回pytest退出码5（未收集到测试）属于异常，不能统一当成功。整个筛选结果为空时明确非零退出。

托管服务的/debug/cancel-expired和/debug/reset仅在exclusive阶段且请求case已注册时允许，parallel阶段返回409且无副作用。ApiClient同样做早期提示；服务端检查防止绕过客户端。对直连SQL不能靠此接口守卫保证安全，仍需要归属审计和测试覆盖。

## 7. 数据清理与阶段屏障

保留唯一账号/商品名、本例资源登记和外键反序事务清理；种子账号只读。catalog.missing_product改为本例另一店铺已上架、但目标店铺没有关联的商品，仍验证“店铺中没有该商品”的404，不依赖全库MAX编号。

普通阶段全部通过后：

1. 确认pytest主控与全部worker退出，任何未确认停止的进程阻止后续阶段。
2. 服务全部在途请求数为0；无open/closing用例；无fatal标记。
3. 对创建环境后的种子基线进行规范化对照：用户、店铺、商品、店铺商品的稳定业务字段一致；购物车/订单/子单/明细没有本轮遗留。基线不比较AUTO_INCREMENT计数，不重置自增序列。
4. 不一致则失败并留差异摘要，禁止清库掩盖泄漏，也不启动独占阶段。
5. 切服务阶段至exclusive，再启动单进程pytest。

独占阶段每条用例仍归属清理，整个阶段结束再做一次同样的基线检查。用户主动筛选少量测试时仍执行屏障，不依赖“刚好后面没用例”。

## 8. 清单、报告、异常与兼容

resources.json升级为schema_version=2，environment_mode=shared_phased；environments列表只保留一个worker='shared'的资源条目以复用恢复形状，不为gw0/gw1重复登记同一个库。单元运行无环境。运行器依旧核对运行UUID、环境UUID、库名、服务PID及创建时间。

每阶段有独立测试进程清单，最终清单登记所有子清单；恢复工具逐个检查路径归属、先停止所有测试进程，再停止商城，最后只删除这一个本轮库。保留读取E2旧清单的能力。回收失败必须非零，不输出真实凭据。

共享环境配置与产物配置分离：各worker的DB_NAME、BASE_URL、TEST_ENVIRONMENT_ID相同；TEST_WORKER_ID和TEST_ARTIFACT_DIR不同。目录为run/phase/worker/，含cases.jsonl、诊断与UI轨迹；服务日志只有一份，记录run/case/request关联。JUnit按阶段独立保存，汇总同时报告预期数、已运行、通过、失败、错误、跳过、未运行及原始退出码；诊断phase与pytest的setup/call/teardown字段分开命名。

普通失败、收尾失败、worker崩溃、SIGINT/超时、服务早退、删库失败分别留证。worker崩溃不自动重启，停止其他worker；服务未确认停止时不删库。任何阶段失败不能被后续成功或回收成功覆盖。

## 9. 文件边界与实施顺序

详细步骤见[实施计划](../plans/2026-11-16-shared-environment.md)。新增模块限制在真正独立职责：server/test_runtime.py管理测试请求登记与阶段；test_support/case_lifecycle.py管理测试端收尾；tools/test_phases.py管理分类清单与阶段命令。复用environments.py、processes.py、factories.py、diagnostics.py现有能力。

先冻结当前E2基线和审计用例，再实现请求局部支付；接着实现请求归属和清理，再启用共享库和阶段运行器，最后调整恢复、诊断、CI和文档。禁止只合并数据库而暂时保留全局支付开关或全局_quiesce。

## 10. 验收与回退

所有验证必须记录代码状态、环境、完整命令、数量、退出码、产物路径与清理结果；历史E2的192条只是参考，不冒充新版本固定数量。

- 请求级支付：默认成功、显式失败、显式超时、无效参数、非调试环境拒绝注入；同一服务同时成功/失败/超时互不串扰，订单/库存/明细结果正确。
- 超时收尾：客户端退出后服务仍在途，先排空再删除；另一例正常请求不被等待拖住；本例关闭后迟到请求拒绝且不写库；无法排空使整轮失败。
- 分类：缺标签/双标签报错；独占不在普通阶段运行；未知全局调用在普通阶段被拒绝；空阶段合法但非空阶段意外漏收集失败。
- 阶段：用时间区间及进程退出记录证明无重叠，基线泄漏时独占不启动；筛选后无漏项/重复项。
- 归属：两个独立上下文并行创建和清理，甲清理不影响乙；一条抢最后一件用例内部仍共享同一商品，不把业务竞争拆开。
- 完整回归：Windows单进程1轮、双进程连续3轮；Linux CI单元及完整workers=1/2。失败/错误/意外跳过必须解释，不以收集成功代替执行。
- 故障回收：worker崩溃、支付不收尾、阶段失败、服务启动失败、中断及恢复重入；无本轮服务/库遗留，恢复不影响其他运行。
- 成本：同机相近负载对比E2与新方案的创建/执行/回收/总耗时、服务数、库数、数据库连接峰值；每种至少3次。明确预期结构从2套变1套，总时间可能变长，不设虚构提速门槛。

实施前保存可追溯E2代码快照与证据，不自动推送。验收不通过时保留E2为当前方案，修复后复验；使用选择性补丁/恢复指定版本代码，不回退用户其他修改。最终只维护一套默认实现，不长期增加两套生产运行器；旧清单恢复兼容单独保留。

## 11. 官方依据（2026-11-16核查）

- [pytest自定义标签](https://docs.pytest.org/en/stable/example/markers.html)：标签用于分类与筛选，不自动提供互斥。
- [xdist分发策略](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)：load分发，分组不能保证其他worker暂停。
- [xdist进程身份](https://pytest-xdist.readthedocs.io/en/stable/how-to.html)：按进程标识拆分产物；不要求数据库必须逐进程创建。
- [FastAPI请求头](https://fastapi.tiangolo.com/tutorial/header-params/)：从请求读取局部配置；本文头名和调试控制协议属于本项目设计。
- [pytest退出码](https://docs.pytest.org/en/stable/reference/exit-codes.html)：5代表没有收集到测试，不应一律视为成功。
- [pytest准备与清理](https://docs.pytest.org/en/stable/how-to/fixtures.html)：通过显式依赖安排释放顺序，异常准备也应释放已取得资源。

这些来源支持工具行为，不证明所有公司都采用本文架构。

## 12. 后续Celery深化的兼容边界（11-16补充）

用户明确后续会深化Celery异步。本轮共享环境方案不禁止扩展异步，但HTTP请求结束不能等同于所有业务活动结束；不能承诺现方案无需调整即可直接启动真实调度。

### 12.1 当前真实情况

server/tasks.py中的cancel_expired_orders只薄封装server/db.py的全库扫描；server/celery_app.py已配置Redis消息传输/结果存储和周期关单；docker-compose.yml的Celery入口带--beat。本轮独立测试入口不启动这些进程。以后直接把该Compose入口加到普通回归旁，会有后台全库扫描绕过HTTP阶段守卫，破坏隔离；这不是增加case_id请求头就能解决的。

Celery /ˈseləri/负责异步任务执行；队列是待执行消息的传递通道；Celery worker是消费消息的任务进程，与pytest worker不同；beat /biːt/是定时投递任务的调度器。它们各自是独立进程，不能共享FastAPI进程里的Python字典和计数器。

### 12.2 本轮应保留的扩展位置

1. CaseLifecycle.wait_idle与close_and_wait在注释和文档中明确本轮仅覆盖受跟踪HTTP请求；在内部拆分wait_requests_idle(timeout)与对外wait_idle(timeout)，为以后增加后台任务检查留一个调用点。本轮不实现空壳Celery适配器，不返回虚构的“任务已排空”。scenario、阶段屏障不得在各处直接读取HTTP计数并将其命名为all_work_finished。
2. run_id、case_id使用与HTTP无关的普通标识；HTTP请求头只是传递方式之一。未来发布任务时须显式传递关联标识，按实际任务行为通过消息头/业务标识关联；Web进程环境变量、局部变量和内存登记不会自动传到任务进程。
3. 一轮一套环境是当前HTTP套件的默认，不是未来所有测试永久只能一套环境的约束。执行器保持“创建资源→运行阶段→屏障→释放资源”的边界，后续可以增加独占异步阶段，必要时给该阶段单独环境，不恢复为所有pytest worker各建一整套环境。
4. 保留任务薄封装与共享业务逻辑；本轮不为了分类把全库扫描缩窄成仅扫本例订单。不能用只调用debug接口通过，声称已验证真实队列消费与定时投递。

### 12.3 未来接入真实Celery时的准入检查（不在本轮实现）

- 为每轮分配独立队列名，生产端显式路由，Celery worker只消费该轮队列；worker通过受控环境配置连接该轮专用库。独立队列只隔离消息流，不能限制一个全库SQL扫描到哪些行，也不能替代结果存储、调度状态的隔离。
- 普通HTTP并行阶段不运行全库关单调度。先将真实Celery/beat验证放在独占异步阶段；先验证消息投递消费，再验证周期投递。周期扫描任务按run/阶段归属管理，不强行伪造一个case_id代表其扫描的全部数据。
- 明确跟踪本例提交的task_id、链式子任务、重试/延迟任务，以及数据库业务终态。一个task成功、某次队列长度为0或HTTP在途为0，均不能单独证明没有后续写入。
- 收尾须先停止继续发布任务及周期投递，再确认相关已投递/已预取/执行中/重试和延迟任务不会继续写入，最后清理数据；确认不了就使该阶段失败并停止本轮拥有的任务进程，再考虑删库。不能以清空公共队列或“发了撤销指令”当完成证据。
- Web与Celery分别回收连接池和进程；资源清单增加任务进程、调度器、队列和必要的调度/结果产物。只能回收本轮拥有的资源，不FLUSHALL、不清空其他工作的默认队列。
- 若异步支付需要模拟失败，故障参数必须显式进入任务执行上下文且仍仅测试环境可用；不能让Celery任务回读Web函数局部变量，也不重新引入进程全局支付模式。

### 12.4 判断与验证

当前方向可以继续；它减少HTTP回归的环境数量，并为全局任务保留独占执行位置。实际代价是未来必须把“等待HTTP结束”扩展为“确认相关异步工作收尾”，还可能为异步专项增加资源，不能保证零返工。

本轮验收增加两项：普通运行未启动Celery/beat；HTTP等待接口和文档没有宣称覆盖任务。未来异步验收必须启动真实消息传输与消费者，包含重试、延迟、重复投递和清理竞态，不能以本地同步模拟替代。

依据（2026-11-16核查）：[Celery调用与消息发布](https://docs.celeryq.dev/en/stable/userguide/calling.html)、[指定队列及消费者](https://docs.celeryq.dev/en/stable/userguide/routing.html)、[周期任务及重叠风险](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html)、[真实worker与同步模拟的差异](https://docs.celeryq.dev/en/stable/userguide/testing.html)。具体队列命名、任务跟踪及异步阶段方案在异步深化时另行细化，不把未来准入条件当成本轮已实现。
