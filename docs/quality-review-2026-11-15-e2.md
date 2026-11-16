# ShopHub E2 验收记录 · 2026-11-15

## 版本与范围

基线为`improve-test-design / dbe9c2c`，本机验证的是未提交工作区，精确Python文件指纹保存在[证据JSON](verification/2026-11-15-e2.json)。保留接手前两份修改及两份未跟踪文档，不将其整份差异归为本轮。用户确认每测试进程独立数据库和单进程商城服务、默认双进程、全量迁移数据准备/隔离/清理。课件按本次指令延期，未修改。

## 完整回归

Windows本机、Python 3.11.9、项目`.venv`、MySQL、Chromium。原76条单元增加为113条，原74接口、5网页完整保留；逐项nodeid（测试的完整编号）比较无增删。新增单元不用于抵消业务覆盖减少。

以下命令将`N`替换为1或2，`LABEL`替换为表中对应编号：

```powershell
.venv/Scripts/python.exe -m tools.run_tests --suite all --workers N --run-id LABEL -- -q --browser chromium
```

最后一版各轮均192通过、失败/错误/跳过均0、退出0。以下时间单位为秒；连接峰值按50毫秒间隔采样本轮数据库连接，包含测试直连，不含管理连接；采样峰值并非绝对瞬时上限。

| 编号 | 通过 | 准备 | 执行 | 清理 | 总耗时 | 采样连接峰值 |
|---|---:|---:|---:|---:|---:|---:|
| e2-delivery-serial | 192 | 2.062 | 50.656 | 0.141 | 52.859 | 3 |
| e2-delivery-parallel-1 | 192 | 4.031 | 28.734 | 0.297 | 33.062 | 6 |
| e2-delivery-parallel-2 | 192 | 4.094 | 28.859 | 0.219 | 33.172 | 6 |
| e2-delivery-parallel-3 | 192 | 3.969 | 29.656 | 0.203 | 33.828 | 6 |

双进程总耗时平均33.354秒，单进程52.859秒，本机约1.58倍；不能外推为所有机器固定收益。三个双进程轮次都从cases.jsonl确认两个环境的业务call阶段在时间上重叠。每服务池预建0、上限10；本机采样峰值6低于两服务池理论上限20加直连预算。未做压测。

## 隔离与边界证据

```powershell
.venv/Scripts/python.exe -m tools.verify_e2 --run-id e2-isolation-final
.venv/Scripts/python.exe -m tools.verify_e2_cases --mode reverse --run-id e2-cases-final-reverse
.venv/Scripts/python.exe -m tools.verify_e2_cases --mode repeat --run-id e2-cases-verified-repeat
.venv/Scripts/python.exe -m tools.verify_e2_cases --mode api-failure --run-id e2-cases-verified-api-failure
.venv/Scripts/python.exe -m tools.verify_e2_cases --mode ui-failure --run-id e2-cases-verified-ui-failure
.venv/Scripts/python.exe -m tools.run_tests --suite api --workers 4 --run-id e2-four-smoke -- -q -k 'test_cart_isolation_with_owned_resources or test_cross_store_paid_order_has_independent_expected_amount or test_cleanup_preserves_another_context or test_partial_preparation_failure_still_cleans_created_resources'
```

- 双环境真实验证A支付失败时B支付成功；A扫描关单B仍pending；A清理后B账号、订单及商品仍存在。
- HTTP200业务支付失败时，准备器明确拒绝paid前置，数据库实际为canceled，随后资源清理；状态等待超时只读、不重发写请求。
- 客户端1秒超时后观察到在途1，约6.016秒后归零，之后才恢复和清理。ASGI（接收HTTP请求的服务接口）入口计数覆盖线程池排队，单元同时覆盖异常finally归零。
- 逆序74接口通过；同环境重复10次通过，每次生成新资源。四进程4条冒烟通过，只证明冒烟，未声称四进程完整回归。
- 故意API/UI失败各1条，测试退出1，验证器退出0；核对对应worker诊断请求ID能命中同目录service.log，UI截图及trace.zip（浏览器操作轨迹）存在。不是产品缺陷。
- 准备中断、重复清理、不误删共享/参考数据由原工程接口用例真实验证；清理失败停止标记、15秒等待上限及业务恢复结果由新增单元覆盖。

## 异常回收

对init、port、crash、interrupt、cleanup分别执行：

```powershell
.venv/Scripts/python.exe -m tools.verify_e2 --fault FAULT --run-id e2-final-fault-FAULT
```

初始化在第二环境故意失败，已建资源回收；真实占用端口触发新端口重试；真实worker异常退出无重启，停止本轮；测试进入执行后注入KeyboardInterrupt，退出130；模拟一次删库失败使退出非0，保留清单后由recover_run恢复。探针查询精确库名和进程身份，不能只凭summary宣称已清理。最终五类探针全部验收通过，精确退出码与资源核查见证据JSON。

恢复入口：`python -m tools.recover_run --manifest reports/engineering/LABEL/resources.json`。只恢复指定清单资源，先按PID、创建时间及命令摘要核对并停止测试进程，再核对商城进程并删库；不扫描测试库前缀。强杀时来不及登记的进程不能保证自动恢复，保留此操作系统边界。

## 中间失败与修复

以下是本轮实际运行碰到的问题，以及代码审查发现并修复的风险；不是企业生产事故。故意制造的接口断言失败、进程崩溃和删库失败等另见“异常回收”，不计作自然发生的产品缺陷。记录日期为2026-11-15，不补造工时或发生次数。

### 实际运行失败

| 问题与现象 | 原因及处理 | 验证与证据 |
|---|---|---|
| 首次收集清单提示缺少`dbutils` | 调用了系统Python，没有使用项目已安装依赖的`.venv`；改用项目虚拟环境，不通过删测试绕过依赖 | 虚拟环境下重新收集原155条，原76条单元通过；最初错误终端输出未单独留存，基线收集文件已被有效清单替换 |
| 首轮完整串行167通过、2失败 | 验收工具的测试已落地，工具实现尚未写入；属于分工整合时序问题，不能据此判断商城业务失败。工具就绪后重新验收 | [首次结果](../reports/engineering/e2-serial/summary.json)；最终完整结果见上表 |
| 逆序运行73通过、1失败，报`ApiClient`没有`close()` | `closing`用于退出时调用对象的`close()`；本次误把客户端本身当作可关闭对象。应关闭它持有的`session`（HTTP连接会话） | [逆序失败结果](../reports/engineering/e2-cases-reverse/summary.json)；`e2-timeout-fix`定向重验1通过，随后逆序74通过 |
| 同环境重复探针1通过、9失败，报`KeyError: scenario` | 重复放入同一个pytest测试条目，没有得到独立的准备/清理生命周期。改为参数化生成10个独立条目，每次创建新场景资源 | [重复探针失败结果](../reports/engineering/e2-cases-final-repeat/summary.json)；`e2-cases-verified-repeat`最终10通过。该问题属于验收工具错误，不是商城功能错误 |
| 191条复验中3条网页失败，接口和服务健康检查正常 | 随机分到了5061端口；Chromium限制此端口，请求报`ERR_UNSAFE_PORT`。查阅[官方限制列表](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/net/base/port_util.cc)，分配改为49152–65535高位随机端口，并保留占用重试；不关闭浏览器保护绕过问题 | [失败结果](../reports/engineering/e2-accepted-parallel-1/summary.json)、[端口分配回归测试](../unit/test_browser_port.py)；修复后本机四轮完整回归及Linux CI均通过 |
| Git HTTPS推送两次遇到TLS连接中断，无法直接推送验证快照 | Windows默认TLS后端与单次切换OpenSSL均失败，具体网络原因未确认。GitHub API当时可用，改用Git数据接口创建独立验证分支，校验远程内容后触发CI；没有改动当前分支或原索引 | 当时错误仅在终端输出，未单独保存网络日志；[最终CI](https://github.com/xieguocheng666-ai/shophub-test-platform/actions/runs/43727627175)及证据JSON保存快照和源码一致性结果。不能将此记录解释为已定位网络根因 |

表内`reports/`链接仅在本机证据目录保留时可打开，不随Git克隆获得；对应失败计数已在本表保留。证据JSON主要保存验收轮次及探针结果，并未收录全部过程失败。环境选错和网络问题只保留了上述记录，不宣称存在独立原始日志。

### 审查发现并修复的风险

这些问题由审查或针对测试暴露，不表示完整回归时都已自然发生。验证强度分别列出，避免把单元替身通过写成真实故障已经出现。

| 风险与具体例子 | 处理方式 | 验证依据 |
|---|---|---|
| 只检查HTTP200会误认业务前置成立：模拟支付失败也返回200，发货测试可能拿着未支付订单继续执行 | 准备器检查支付业务字段，并查询本例父子订单的实际状态；条件等待只读取，不再次提交支付 | [准备契约测试](../unit/test_scenario_contract.py)及真实`e2-isolation-final`探针，确认失败支付订单实际为canceled，准备器拒绝paid前置 |
| 客户端超时不等于服务端结束；只在业务处理函数开始时计数，还会漏掉正在等线程的支付请求 | 将在途计数放到接收HTTP请求的入口，在完整请求结束的`finally`中减少；收尾有截止时间地观察归零后才恢复和清理 | [服务契约测试](../unit/test_parallel_service.py)覆盖排队及异常归零；真实探针观察客户端1秒超时后仍在途，约6秒后结束 |
| 网页数据可能先被清理，浏览器上下文仍存活；上下文是一次测试使用的Cookie、页面及连接等浏览器资源 | 用准备函数的依赖顺序确保先关闭浏览器上下文，再清理scenario资源 | [网页收尾实现](../ui/conftest.py)、完整网页回归及故意网页失败截图/轨迹验收；本轮没有单独注入浏览器关闭失败 |
| 服务在端口冲突后快速退出，读取进程创建时间就可能报错，绕开端口重试；另一次旧尝试的成功收尾可能覆盖新尝试的停止失败状态 | 快速退出纳入有上限的启动重试，同一环境的清理失败状态不能被旧尝试改写成成功 | [运行器回归测试](../unit/test_parallel_runner.py)两项针对覆盖；真实`e2-final-fault-port`验证占用后换端口启动 |
| pytest主控先退出时，直接返回可能留下仍在运行的测试子进程；仅凭PID（进程编号）恢复还可能误杀后来复用该编号的进程 | 持续登记测试进程身份，记录PID、创建时间及命令摘要；主控退出也回收已登记子进程，恢复时先核对身份，再停服务和删库 | [进程登记实现](../tools/processes.py)、运行器主控退出/PID复用针对测试、真实崩溃与中断探针；最终383条登记身份核查无对应存活测试进程 |
| 手动场景清理失败后，运行器每200毫秒检查停止标记，间隙内下一例可能已经开始；写标记失败也不能让停止逻辑失效 | 手动清理失败写污染标记；每例准备前立即检查标记并退出，准备函数停止逻辑用`finally`保证执行 | [收尾契约](../unit/test_fixture_lifecycle.py)、[下一例停止测试](../unit/test_environment_stop.py)；这是针对性单元验证，不能说已经真实复现下一例污染 |

### 实施难点、取舍与保留边界

1. **隔离必须早于导入和首次连接。** 每进程独立数据库还不够，测试进程若先导入并使用旧连接池，再修改库名，仍可能连错目标。因此把环境绑定放到显式加载的轻量插件中，先完成绑定再加载根准备函数和测试模块；另用独立Python子进程确认导入时已拿到正确库名和地址。相关测试见[环境绑定回归](../unit/test_parallel_runner.py)。
2. **迁移数据时还要保留原风险。** 固定商品编号要替换成动态编号，但库存100时的99/100/101边界、独立金额预期和越权断言不能跟着改变。迁移还发现：只检查已有商品编号会漏掉非法上架时错误新增的商品，因此改为检查本例唯一商品名。原79条业务编号逐项保留；断言数量检查只作辅助，不能单独证明覆盖没有弱化。
3. **清理需要同时处理数据、请求和进程。** 用例失败不代表请求已结束，服务停止失败也不能继续删库；整轮最终删库不能掩盖每例归属清理失败。为此分别管理请求收尾、本例数据清理、测试进程和商城进程回收，并保留清单恢复入口。强杀或断电时来不及登记的资源，仍不能保证自动回收。
4. **故障验收本身也可能写错。** 例如重复使用同一pytest条目导致准备函数失效；故障工具如果只看到非零退出码，也可能把“其他原因失败”错当作目标故障已触发。因此工具还核对故障确实触发、精确资源已消失、诊断属于正确进程；支付准备拒绝还核对错误原因和实际订单状态。探针工具出错与商城出错分别记录。
5. **并行有启动和准备成本。** 为保持旧业务输入组合集中，`catalog`当前每个相关用例会创建4个账号、6个商品及1个跨店关联，并非逐项懒创建。该成本没有消除，只是纳入真实耗时；两个服务启动也使准备时间从约2秒增加至约4秒。最终整体约1.58倍，未达到或承诺2倍。两个测试进程仍共享MySQL实例，不能据此证明性能资源隔离。
6. **最终修复后必须重新收集证据。** 污染停止检查和端口分配修复都发生在已有绿色结果之后，因此保留旧结果，重新做最后一版串行/三轮双进程和Linux CI。最初远程运行取消后保留记录；最后通过的快照与本地Python源码逐文件一致。课件按用户要求延期，没有把项目实现完成记成课件同步完成。

以上问题在当前范围内均已处理并按所列方式验证；网络TLS失败根因仍未知，强杀前未登记资源、完整四进程回归及真实任务调度的边界仍保留。没有据此自动开始新一轮优化。

## Linux CI与未验证范围

Linux CI本轮[运行43727627175](https://github.com/xieguocheng666-ai/shophub-test-platform/actions/runs/43727627175)成功：Ubuntu / Python 3.11，单元113通过，单进程与双进程完整集成各192通过，无失败/错误/跳过，退出均0，资源及测试进程回收正常，产物上传成功。验证快照`8d1fc69`位于`verify/shophub-e2-20261115`；已逐文件核对全部Python源码与本地最终代码一致。CI之后本地仅移除pytest.ini的闲置owned_data标记说明，未改变运行逻辑。首次远程运行43727459641因端口修复被主动取消，保留记录。Compose、真实Celery消息投递及定时调度、真实支付、性能容量未验证。课件本轮延期。

## 证据可获得性与清理

仓库可审阅[证据JSON](verification/2026-11-15-e2.json)，含命令、版本、指纹、计数、时间、环境映射和清理结果。原始JUnit、逐进程日志、诊断、截图及轨迹位于本机`D:/my-test-framework/reports/engineering/`对应编号目录，被Git忽略，不随克隆获得。保留其作为验收证据，其余一次性脚本及时清理；不包含.env或真实凭据。远程产物按CI配置保留14天。

最终核查：32轮本机运行清单中的商城进程及临时库均不再存在；未按前缀清理或触碰其他运行资源。本地仍为improve-test-design / dbe9c2c，原索引未变，工作区修改保留；远程仅保留验证分支，未合并。
