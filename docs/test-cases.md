# ShopHub 接口用例设计说明

这里说明真实自动化用例为什么存在、怎么准备、检查什么。面试学习稿在 `D:/同余信科面试准备/07-核心链路用例设计与口述.md`。

## 业务范围与规则

模拟商城：买家加购、下单、模拟支付，所属商家发子订单，买家确认收货。
一次购买跨店铺时按店铺拆子订单。下单扣库存，加购不扣库存；模拟支付成功不再扣库存；失败取消订单并回补库存。
父订单完成要求所有子订单完成。当前发货只更新子订单，父订单不会在发货时变为 shipped。
支付没有真实账户扣款，不测真实银行支付或清结算。

## 用例从哪里来

业务规则 → 出错风险 → 测试方法 → 前置、操作、预期 → 响应和数据库断言。
例如「购买数量不能超过库存」对应数量输入分类、临界数量、累计加购、下单前库存变化和多个买家竞争。
正常链路不是覆盖率指标；不能用全部用例通过证明没有缺陷。

## 运行前提与隔离

- 被测服务需开启 DEBUG_MODE=1，便于重置、切换模拟支付模式和触发关单。
- 每条测试前清表并重播种子，须用专用测试库，禁止指向生产数据。
- 这些用例目前按顺序运行；不同测试不能并行共享同一库，否则重置会干扰其他测试。用例内部并发与用例之间并行是两回事。
- 每条用例独立客户端；并发抢购的两个买家使用各自账号、购物车和会话，先完成准备再同时下单，关闭客户端重试。
- 数据库是数据验收必需依赖，连接失败报告准备错误，不以跳过掩盖缺少校验。
- 测试与被测服务必须连接同一专用库。Docker test 显式使用 DB_HOST=db 和相同库名。

## 用例分布（参数展开后67条接口，另18条单元和5条UI）

| 文件 | 数量 | 关注点 |
|---|---:|---|
| api/tests/test_auth.py | 14 | 服务、登录、搜索、身份令牌有效性、密码存储 |
| api/tests/test_cart.py | 13 | 加购/删除、非法值、数量上下边界、累计加购、空购物车 |
| api/tests/test_order.py | 9 | 购买全流程、权限、状态、支付失败/超时、重复关单 |
| api/tests/test_order_integrity.py | 10 | 下单回滚、重复支付、非法状态操作、部分收货、跨店失败 |
| api/tests/test_store.py | 7 | 店铺权限、拆单、独立金额、跨店库存隔离 |
| api/tests/test_concurrency.py | 3 | 两个商品的最后一件竞争、同时收货 |
| api/tests/test_e2e.py | 2 | 搜索到收货的链路衔接、直接触发关单 |
| api/tests/test_quality_regressions.py | 9 | 输入边界、库存原值更新、用户隔离、他人收货拒绝、超时筛选 |
| unit/test_quality_regressions.py | 18 | 写请求不重放、读请求重试上限、模型校验、库存原值与不存在区分 |

UI另有5条；下单除跳转外，检查页面数量2、金额598及数据库订单明细。unit通过本目录fixture覆盖根目录reset_data，不连接服务或数据库。默认pytest已包含unit。

## 核心设计矩阵

| 业务风险 | 方法和具体输入 | 前置与动作 | 必须验证 | 代码入口 |
|---|---|---|---|---|
| 正常购买算错钱或重复扣库存 | 正常链路；键盘 2 件 | 下单前查店铺商品价和库存，独立算预期，再走支付、发货、收货 | 价×量的独立金额；父子单及明细；下单扣 2、支付不再扣；购物车清空；最终完成 | test_order_full_flow |
| 非法数量或未知商品 | 输入分类；-1、0、远超库存、未知编号 | 干净购物车发起加购 | 拒绝及具体原因；没有购物车残留；全部店铺库存不变 | test_cart_add_boundary |
| 数量临界点判断错误 | 边界值；1、99、100、101 | 键盘库存 100，合法数量加购后下单 | 加购不扣库存；99/100 下单后库存 1/0；101 拒绝；DB 数量正确 | test_quantity_stock_boundary |
| 只检查单次数量 | 累计边界；60+39/40/41 | 先加购 60，再追加 | 最终为 99/100，追加 41 拒绝后仍保留 60；库存不变 | test_cart_accumulated_quantity |
| 空车生成空单 | 异常输入 | 空购物车下单 | 无父子单、无明细，全部库存不变 | test_order_empty_cart_rejected |
| 加购后库存已变 | 状态变化 | 加购 2，商家将该商品库存改到 1，再下单 | 拒绝；购物车、库存和所有订单表逐行不变 | test_order_stock_changed_rolls_back[single-product] |
| 多商品仅撤销部分扣减 | 原子性，即一次下单全部成功或全部撤销 | 加购商品 2、3，让后扣库存的商品 3 不足，再下单 | 前一个商品的扣减也必须撤销；无半成品订单；购物车保留 | test_order_stock_changed_rolls_back[rollback-earlier-product] |
| 已支付再支付 | 重复操作 | 成功支付后再请求支付 | 明确拒绝，订单、明细、库存、购物车都不变 | test_paid_order_repay_has_no_side_effect |
| 非法状态操作写入脏数据 | 状态迁移；pending发货、pending/paid收货、重复发货/收货 | 构造合法前置状态，使用正确角色触发不允许操作 | 状态码和原因、请求前后完整数据快照相等 | test_invalid_order_actions_preserve_data |
| 一家收货导致整单提前完成 | 多子单组合 | 跨店下单支付、两家发货，先后收货 | 第一次只完成一个子单，父单必须仍为paid；第二次父单完成 | test_parent_completes_only_after_all_suborders |
| 支付失败漏回补某店库存 | 跨店组合、小数金额、重复操作 | 店铺1键盘1件，店铺2 U盘2件、线1件，模拟支付失败 | 独立金额398.70；两子单均取消；所有库存恢复；明细保留；再次支付不改变数据 | test_cross_store_failure_preserves_amount_and_items |
| 拆单归属或金额错误 | 跨店组合 | 店铺1商品1、店铺2商品5各1件 | 两子单按店铺归属，父单/子单/明细都与下单前独立预期匹配；分别扣库存 | test_order_split_by_store |
| 同商品跨店库存串扰 | 数据隔离 | 仅购买店铺1商品1 | 店铺1减1，店铺2同商品不变 | test_same_product_multi_store_stock_isolation |
| 越权 | 角色×资源归属组合 | 买家发货、改他人订单、商家操作他人店铺 | 明确拒绝且目标状态或库存保持不变 | test_order.py / test_store.py 对应越权用例 |
| 并发超卖或零成交假通过 | 两个独立买家抢1件 | 商家调库存为1，两买家先各加购1，屏障同步下单 | 精确1成功1库存不足；库存0；只有1父单1子单1件明细；失败方购物车保留，成功方清空 | test_concurrent_order_no_oversell |
| 并发收货遗漏父单更新 | 两子单同时操作 | 两子单已发货，独立会话屏障同步确认 | 两个请求成功、两子单完成、父单完成 | test_concurrent_confirm_parent_completed |
| 重复关单重复回补 | 幂等，即重复执行效果不叠加 | 待支付订单连续关单两次 | 首次1条、第二次0条，库存正好恢复，父子单取消 | test_cancel_expired_idempotent |

## 为什么这些断言更可信

1. 下单前用店铺价格和购买数量独立计算金额，不仅检查父子表彼此相等。
2. 金额用 Decimal 精确小数比较，不允许一分钱误差被容差吞掉。
3. 数据库查询不复用被测服务的数据访问函数。按本次订单号识别订单，避免列表排序影响结果。
4. 失败场景比较请求前后的库存、父单、子单、明细、购物车，既测拒绝也测无副作用。
5. 并发断言既限制不能超卖，也要求合法请求确实成交，不能全失败仍通过。

## 验证方式与边界

常规验证：`.venv/Scripts/python.exe -m pytest api ui -q -o addopts=`，先设置专用 DB_NAME 与 BASE_URL。
关键用例还做临时内存故障注入：让父子订单及明细一起多算单价、失败下单额外扣库存，检查测试能失败，然后关闭故障服务并对原始业务重新回归。
故障注入用于验证测试敏感度，不是发现了真实生产缺陷，不计入真实缺陷经历。

目前尚未覆盖：完整非法状态矩阵、所有接口的权限矩阵、支付与关单同时竞争、真正 Celery 调度与消息交付、真实支付对账、所有类型/字段校验。并发屏障增加请求重叠机会，不保证穷尽服务端所有交错；两人竞争验证正确性，不表示性能容量。
2026-11-09修复了输入非负有限校验及库存同值更新误报不存在，另修复客户端写请求重放。真实Celery调度、支付与关单同时竞争仍未验证；完整结果见 `quality-review-2026-11-09.md`。

参考：[ISTQB黑盒测试技术](https://astqb.org/4-2-black-box-test-techniques/)、[pytest参数化](https://docs.pytest.org/en/stable/how-to/parametrize.html)、[pytest跳过含义](https://docs.pytest.org/en/stable/how-to/skipping.html)。要求 DB 不可达时报错，是本项目把数据库验收视为必需环节的选择，不是 pytest 禁止跳过数据库测试。

## 2026-11-09补充矩阵

| 风险 | 场景与预期 | 用例 |
|---|---|---|
| 响应丢失导致重复写 | 写请求只发送一次并抛超时；GET/HEAD网络失败才有界重试 | unit/test_quality_regressions.py |
| 负价格或负库存入库 | 上架/改库存返回422，业务数据无变化；零值保留 | test_product_negative_values_rejected_without_writes / test_stock_negative_value_rejected_without_writes / test_zero_price_and_stock_are_accepted |
| 未改变库存被误报不存在 | 对存在商品重复写原值成功，不存在商品返回404 | test_stock_same_value_update_succeeds / test_stock_missing_product_rejected |
| 购物车串用户 | 两用户分别增查删，互不影响，库存不变 | test_cart_isolated_between_users |
| 确认他人收货 | 正确前置已发货，由另一买家确认拒绝，五类数据不变 | test_confirm_other_buyers_shipped_order_has_no_side_effect |
| 超时筛选或已支付保护错 | 已超时pending取消、未超时pending保留、已超时paid保留；只回补一件，重复取消为0 | test_cancel_expired_only_cancels_old_pending |

输入校验的422来自框架请求体校验；这与业务规则失败的400是不同层次。0元是否允许是产品规则问题，本轮保留原本允许的零值，只拒绝负数与非有限值，不擅自新增正价格要求。
