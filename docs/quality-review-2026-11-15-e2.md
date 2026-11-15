# ShopHub E2 验收记录 · 2026-11-15

## 版本与范围

基线为`improve-test-design / dbe9c2c`，本机验证的是未提交工作区，精确Python文件指纹保存在[证据JSON](verification/2026-11-15-e2.json)。保留接手前两份修改及两份未跟踪文档，不将其整份差异归为本轮。用户确认每测试进程独立数据库和单进程商城服务、默认双进程、全量迁移数据准备/隔离/清理。课件按本次指令延期，未修改。

## 完整回归

Windows本机、Python 3.11.9、项目`.venv`、MySQL、Chromium。原76条单元增加为110条，原74接口、5网页完整保留；逐项nodeid（测试的完整编号）比较无增删。新增单元不用于抵消业务覆盖减少。

以下命令将`N`替换为1或2，`LABEL`替换为表中对应编号：

```powershell
.venv/Scripts/python.exe -m tools.run_tests --suite all --workers N --run-id LABEL -- -q --browser chromium
```

各轮均189通过、失败/错误/跳过均0、退出0。以下时间单位为秒；连接峰值按50毫秒间隔采样本轮数据库连接，包含测试直连，不含管理连接；采样峰值并非绝对瞬时上限。

| 编号 | 通过 | 准备 | 执行 | 清理 | 总耗时 | 采样连接峰值 |
|---|---:|---:|---:|---:|---:|---:|
| e2-final-serial | 189 | 2.016 | 51.078 | 0.156 | 53.250 | 3 |
| e2-final-parallel-1 | 189 | 4.078 | 29.688 | 0.250 | 34.016 | 6 |
| e2-final-parallel-2 | 189 | 4.047 | 28.797 | 0.219 | 33.063 | 6 |
| e2-final-parallel-3 | 189 | 4.015 | 29.344 | 0.234 | 33.593 | 6 |

双进程总耗时约33–34秒，单进程53.25秒，本机约1.59倍；不能外推为所有机器固定收益。三个双进程轮次都从cases.jsonl确认两个环境的业务call阶段在时间上重叠。每服务池预建0、上限10；本机采样峰值6低于两服务池理论上限20加直连预算。未做压测。

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

初始化在第二环境故意失败，已建资源回收；真实占用端口触发新端口重试；真实worker异常退出无重启，停止本轮；测试进入执行后注入KeyboardInterrupt，退出130；模拟一次删库失败使退出非0，保留清单后由recover_run恢复。探针查询精确库名和进程身份，不能只凭summary宣称已清理。最终故障结果以证据JSON为准。

恢复入口：`python -m tools.recover_run --manifest reports/engineering/LABEL/resources.json`。只恢复指定清单资源，先按PID、创建时间及命令摘要核对并停止测试进程，再核对商城进程并删库；不扫描测试库前缀。强杀时来不及登记的进程不能保证自动恢复，保留此操作系统边界。

## 中间失败与修复

首轮完整串行167通过2失败，因验收工具测试先于实现落地；逆序发现短超时客户端被closing当作可close对象，已改为关闭其session并真实重验；重复探针直接重复pytest条目造成fixture缺失，改为独立参数化收集后10次通过。均保留首次失败目录，未用最终绿色结果覆盖。

独立审查还修复支付排队漏计数、UI先关浏览器再清理数据、端口快速退出绕过重试、旧尝试覆盖清理失败、pytest主控退出后遗留worker回收等问题；新增针对测试先观察失败再修复。

## Linux CI与未验证范围

Linux CI配置单元、单进程与双进程完整集成矩阵，远程最终结果待补；不以E1历史CI替代本轮。Compose、真实Celery消息投递及定时调度、真实支付、性能容量未验证。课件本轮延期。

## 证据可获得性与清理

仓库可审阅[证据JSON](verification/2026-11-15-e2.json)，含命令、版本、指纹、计数、时间、环境映射和清理结果。原始JUnit、逐进程日志、诊断、截图及轨迹位于本机`D:/my-test-framework/reports/engineering/`对应编号目录，被Git忽略，不随克隆获得。保留其作为验收证据，其余一次性脚本及时清理；不包含.env或真实凭据。远程产物按CI配置保留14天。
