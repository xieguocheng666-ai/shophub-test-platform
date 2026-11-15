# E1 测试工程升级实施计划

> 授权与边界见 [设计](../specs/2026-11-12-engineering-e1.md)。基线0952776已先推送GitHub；复用improve-test-design工作区，使用子任务实现及独立审查，不创建新工作树。

## 任务与验证顺序

- [x] 推送当前文档及已核对的既有变更，核对远端与本地提交号；运行原18条单元回归。
- [x] 数据与场景：先写`unit/test_data_factories.py`工厂行为测试并确认缺失实现导致失败；实现`test_support/factories.py`和`scenarios.py`；在`api/tests/test_engineering_data.py`以真实接口/SQL验证准备、清理与共存；迁移代表用例并比较顺序结果。
- [x] 请求诊断：先写`unit/test_diagnostics.py`验证脱敏、失败阶段、输出隔离与异常保护；实现诊断模块、客户端关联与服务中间件；在根conftest接入失败钩子，验证三阶段失败输出。
- [x] 执行环境：先写`unit/test_runner.py`及数据库标识测试，确认缺失行为；实现`tools/run_tests.py`与init_db修复；验证唯一库、健康检查归属、退出码及清理，记录准备/执行/清理耗时。
- [x] 集成根conftest的数据标记/fixture与诊断钩子；更新pytest标记及自动执行配置；完整隔离运行单元、接口、网页，故意失败再恢复验证诊断及清理。
- [x] 独立审查规格及代码质量，修复重要发现；更新README、架构、状态、路线与决策；检查文档链接、差异及资源清理。

## 共享接口和文件归属

数据实现由主任务负责：test_support/__init__.py、factories.py、scenarios.py、代表接口用例及根conftest整合。

诊断子任务负责：test_support/diagnostics.py、unit/test_diagnostics.py、api/api_client.py、server/main.py。导出`begin_case(nodeid)`、`record_request(record)`、`finish_case(phase, outcome, error=None)`、`write_failure(output_dir, nodeid)`，并提供可供根conftest调用的上下文管理函数；详细签名与主任务同步。输出目录通过`TEST_ARTIFACT_DIR`传入，默认reports/diagnostics。

环境子任务负责：tools/__init__.py、tools/run_tests.py、unit/test_runner.py、server/db.py的初始化部分及相关单元验证。套件unit/api/all；使用`TEST_RUN_ID`、`TEST_ARTIFACT_DIR`共享环境字段；pytest输出JUnit到运行目录，服务stdout/stderr写入该目录。

子任务不得修改根conftest、文档、流水线或对方负责文件，不提交/推送；由主任务集成。测试按先失败后实现执行，记录实际结果。评审子任务只读。

## 运行方式

新增工具后使用`.venv/Scripts/python.exe -m tools.run_tests --suite all`完成本机隔离验收，使用`--suite api`验证接口重复与数据共存；原始命令和真实结果保留于本轮验收记录。故意失败验证放到本轮独立测试环境，不修改业务规则或已有数据。

## 验收记录

2026-11-12完成本机E1实施：最终76单元、74接口、5网页，共155条通过；逆序10条、同库逆序重复20次通过；真实接口故意失败正确退出并清理。独立审查修复共享商品归属、中断异常保留、MySQL认证依赖和查询参数脱敏。原始命令、三阶段耗时、证据可获得性及未验证项见[本轮验收](../../quality-review-2026-11-12.md)。远程CI及Compose尚未实跑，E1改动保留工作区待提交。
