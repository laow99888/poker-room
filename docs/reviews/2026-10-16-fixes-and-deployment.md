# 2026-10-16 缺陷修复与部署验收

对应[本轮审查](2026-10-16-bug-security-review.md)的81–89及R1–R4。用户已授权修复、提交、推送、部署；没有使用子代理。

## 修复结果

| 编号 | 已交付行为 | 验证 |
|---|---|---|
| 81 | 结算页增加“保存并结束本桌”；高级区结束也共用结算事务，保存失败保留草稿。未完成的牌局先进入核对，不能直接丢弃记录；未开始的占位手可直接结束 | 领域事务断言；手机输入9000、结束保存失败、恢复后重试、刷新仍为9000 |
| 82 | 提交finally统一解除布局和页头inert，失败后的导出、输入、重试可用 | 控制view/prepare返回顺序并注入QuotaExceededError；原核心浏览器失败已转为通过 |
| 83 | completedHands独立累计，历史裁剪不影响；旧存档从最后已结算手号兼容读取。结束显示“最后记录”，估算明确标记 | 0/1/105手、100条窗口及旧格式；手机结束显示正确 |
| 84 | 面对下注的check返回400，无需跟注的call也返回400 | 真实v2 API；合法call扣200，不再把错误check当付费动作 |
| 85 | allin按剩余筹码判断为全下跟注或全下加注 | 300面对1000的allin与call结果一致；120手API完整回放及原短码测试 |
| 86 | API保留weights；接口与内部引擎统一验证长度、有限非负及正有限总和；去除零权重死组合 | 同种子API/引擎都为0%；非法权重及总和溢出4xx |
| 87 | 入口共用奖金校验；先限制安全金额再做有限数值判断，同时检查名次顺序和总额 | prepare/view/advice的超大整数、溢出、负数、零及倒序错误均4xx；数学ICM枚举对照保留 |
| 88 | 推演等待最多120秒，支持主动取消/重试，显示服务端错误；保留旧响应版本守卫 | 响应头/正文超时的假时钟测试，手机取消及超时后重试；没有降低采样配置 |
| 89 | reset_all与记录、用户重置共用锁；写文件使用独立临时文件、fsync及原子替换 | 受控并发：清空等待旧记录完成，完成后旧数据不复现；当前服务器1个worker |
| R1 | 服务端签名匿名Cookie决定数据归属，忽略X-Player-Id；Cookie HttpOnly/SameSite=Strict，公开HTTPS部署Secure；拒绝跨站写入，API禁止缓存 | 已知UID伪造、篡改Cookie、持久密钥重载、跨站及反代Origin专项 |
| R2 | 损坏/不可读统计文件停止读取和覆盖，接口503并保留原文 | 错误JSON、错误根结构、错误users结构；正常记录不能覆盖原文 |
| R3 | iterations表示有效评分次数，另返回requested_iterations/rejected_iterations；界面区分精确枚举、有效模拟及冲突跳过数 | 独立评分计数核对991/1000，避免声称完成1000次有效样本 |
| R4 | 用户完成手数与共享对手样本数分离，幂等重试不增加手数；旧共享池按位置计数重建，旧总手数无法还原时返回未知 | 1手/5人样本、重试、两用户隔离、旧池修复及旧手数未知专项 |

额外补齐公开推演的资源准入：默认每进程2个计算槽，繁忙429并附Retry-After；计算预算90秒，在Monte Carlo/CFR循环中合作检查，超时503且释放槽。不返回因超时中断的半成品建议，不改变请求采样设置。普通局面录入不占计算槽。浏览器取消请求不承诺立即杀掉服务器计算，服务端通过预算限制残留任务时长。

## 数据与兼容边界

- 本地牌局存档仍使用原键和schemaVersion=1，不要求清除浏览器数据。新增累计计数字段兼容旧存档，真实手牌、筹码和历史不迁移到新键。
- 匿名身份是本轮漏洞修复，不是完整注册/登录系统。新Cookie使用持久服务端密钥；密钥禁止入Git，默认位于backend/data/session.key，也可用POKER_SESSION_KEY_FILE或POKER_SESSION_SECRET配置。
- **旧画像文件原样保留，但旧客户端UID没有可验证的归属证明，不能据此自动认领到新Cookie。** 需要旧画像时应由管理员核验后迁移；不能为了自动兼容重新开放UID冒认。熟人功能仍默认关闭。
- total_hands现在是当前匿名用户的真实记录手数；player_samples为共享观察数。旧数据缺少可靠累计手数时total_hands=null、legacy_count_unknown=true，recorded_hands只计修复后新记录；没有把玩家观察数当手数或锦标赛场数。
- 文件锁及计算槽均为进程内。当前生产服务已核实为1个worker；以后增加worker必须先改用跨进程事务及全局计算准入，不能直接按worker数量放大并发。

## 本地验证

| 检查 | 结果 |
|---|---|
| `python -m pytest -q --tb=short` | 337通过，17条既有警告，约221秒；含120手完整API回放 |
| `node --test tests/frontend/*.test.mjs` | 79通过，包含105手累计计数、超时和原位置组合回归 |
| 核心真实浏览器流程 | 通过；原保存失败恢复超时已修复 |
| 快捷弃牌浏览器专项 | 12组通过，6/8/9座、桌面/手机、估算、刷新及故障恢复 |
| 扩展浏览器验收 | 17组通过，其中布局覆盖45种视口/状态/名单组合 |
| 操作边界浏览器专项 | 6组通过，含3类15秒无响应恢复 |
| 本轮修复浏览器专项 | 4组通过，包含结束保存失败重试、实际余额、完成手数、推演取消/超时、受控请求逆序后的导出与重试 |
| 旧依赖启动专项 | 通过；资源统一更新为review-20261016-1，6个座位选项、52张牌，未选庄位有提示 |

API正确行为断言在[新增测试](../../tests/test_review_20261016.py)，前端在[领域/超时测试](../../tests/frontend/review-fixes.test.mjs)及[真实浏览器脚本](../../tests/browser/review-fixes.e2e.mjs)。审查期probes目录保留原始缺陷复现，不作为当前验收命令。

本地日志与截图：[完整pytest](../screenshots/review-2026-10-16/fix-pytest-final.txt)、[Node](../screenshots/review-2026-10-16/fix-node.txt)、[本轮修复](../screenshots/review-fixes-2026-10-16/results.json)、[核心流程](../screenshots/review-fixes-2026-10-16/core/results.json)、[快捷](../screenshots/review-fixes-2026-10-16/quick/results.json)、[扩展](../screenshots/review-fixes-2026-10-16/acceptance/results.json)、[边界](../screenshots/review-fixes-2026-10-16/edges/results.json)。已打开检查手机结束及淘汰续手截图。

本轮未重跑1000手、整天运行、100人并发及安卓Via实机；手机验收为Chromium触控模拟。不能将上述通过项称为全部运行环境无缺陷。

本地真实opponents.json SHA256保持 `9fc73f972ac7cfadd3aa8d60c7a398fb40d5ac183bc367acaf0d239ab859974e`。

## 发布与回滚

发布目标：https://ace.iiiu.im ，服务器 `/opt/poker-tools`，服务 `poker-tools.service`。发布仅包含受版本控制的前端及Python源码，不覆盖backend/data、虚拟环境、证书或旧配置中的管理令牌。生产配置增加POKER_PUBLIC_ORIGIN=https://ace.iiiu.im，以兼容反代并明确安全Cookie与跨站校验来源。

提交、发布包哈希、备份目录、线上验收和资源清理将在实际完成后补记；本段不是部署已完成的声明。
