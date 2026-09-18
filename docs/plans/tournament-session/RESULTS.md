# 锦标赛连续牌桌 · 交付结果（P0–P5 全部完成）

日期：2026-09-19。对应规格：本目录 README.md / contracts.md / acceptance.md。
浏览器证据：本目录 `evidence/`（6 张，桌面 1440 与手机 375 各态）。

## 1. 行为变化（相对旧版单手工具）

- 主界面（app.html）重写为**锦标赛连续牌桌**：建桌向导（2–9 座、BB/筹码双单位、
  每人前注、决赛桌 ICM、熟人记牌默认关）→ 逐手录入 → 手后核对全桌余额 → 原子进入下一手。
- 四种身份分离：sessionId / seatId（物理座位）/ occupantId（入座者）/ handId（每手）；
  BTN/SB/BB 只是角色标签，人员进出不破坏历史。
- 三类余额不可混用：手前确认余额、本手回放剩余、手后实际余额（唯一能接续下一手）。
- 结算预览可信度分级：无摊牌全 verified；未知摊牌摊牌者 unknown、弃牌者 verified；
  手工结束（manual_close）全部需人工填写，**不得用模拟收益接续下一手**。
- S02 结算并下一手为原子事务：beginCommit（同步校验）→ prepare（后端验证）→
  completeCommit（原子迁移）→ persist 成功才渲染；lastCommit 幂等防重复提交。
- S03/S04 请求协调：view/advice 双通道独立；{sessionId, handId, handRevision,
  requestId} 四元组实时守卫；view pending 期间禁用动作不排队；advice 失败只影响推演区。
- A04 学习门控：默认关闭，advice 不取画像、record 返回 409；开启时按手快照生效。
- A-04/A-05 学习记录链路（本次补齐）：开启学习且牌谱完整的手在结算确认后入队
  `learningJobs`（session 内持久），自动 POST `/api/hand/v2/record`（带 `X-Player-Id`
  匿名命名空间头）；失败保留原 payload 供重试；关闭学习时暂停发送；恢复页面时补发。
- 后端新增 v2 端点：`/api/table/prepare`、`/api/hand/v2/view`、`/api/hand/v2/advice`、
  `/api/hand/v2/record`（与 legacy /api/stats/record 同库同幂等键）。
- 引擎适配 2–9 人：单挑盲注反转（PokerKit 探针验证）、无小盲局、空庄位手工校准、
  ante 前注、range_position 表（P04）与引擎顺序（P05）。

## 2. 验收结果（命令与数量）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests -q` | **134 passed**（P2 后端 28：F2–F5、P、M、C、A 组；v2 record 3 项） |
| `node --test tests/frontend/*.test.mjs` | **43 passed**（session 25 + coordinator 7 + logic 11） |
| `node --check` 全部 5 个前端 JS | 通过 |
| `python -X utf8 _verify_layer1.py` | 16 项全部通过 |
| `python -X utf8 _verify_all.py` | 36 项全部 PASS（失败非零退出，审查 43 已修） |
| `python -X utf8 _stress_check.py` | **287 通过 / 0 失败**（并发一致性/混合负载/竞态/撤销循环/记录幂等/精确枚举对 treys 参考） |
| `git diff --check` | 通过 |

### 浏览器实测（真实事件，B-01 三手连续流程）

IAB 无头浏览器 1440×900 与 375×812 双视口完整走通：
建桌（6 人、开学习）→ 第 1 手 UTG/HJ/CO/BTN/SB 连续弃牌（C-05 结算预填
9900/10100/10000×4、总额守恒 60,000）→ 确认结算进入第 2 手（BTN1/SB2/BB3/HJ 自动轮转）
→ 刷新恢复（L-01，手数/底牌/动作完整还原）→ 手工结束（C-17，manual_close 必填原因）
→ 第 3 手（BTN2/SB3/BB4/UTG）→ 刷新后手数 3、stats 0 请求。

**A-04 学习链路（浏览器级）**：开启学习建桌 → 第 1 手完整牌谱 → 结算确认 →
自动发送 `POST /api/hand/v2/record`（`X-Player-Id: <uuid>`）→ 服务端新命名空间落库
1 手（verified：users 列表出现浏览器 uid）。测试数据已恢复（见 §5）。

### 验收 ID 与证据对照（抽样，全部 PASS）

| 组 | 覆盖 | 证据 |
|---|---|---|
| M-01..M-10 | 建桌/身份/角色/ ended 条件 | session.test.mjs 25 项 + desktop-setup.png |
| C-01..C-19 | 结算预览/原子提交/幂等 | test_session_context.py + session.test.mjs |
| P-03..P-16 | 单挑/无SB/空庄/ante/轮转 | test_session_context.py |
| S-01..S-04 | 原子事务/请求守卫 | coordinator.test.mjs 7 项 |
| A-02/A-03/A-04/A-05/A-09 | 学习门控/409/record 幂等/v2-legacy 一致 | pytest + 浏览器落库验证 |
| L-01 | 刷新恢复 | 浏览器实测两次刷新 |
| U-05/U-07/U-08 | 手机布局/守卫 | mobile-*.png + coordinator 测试 |
| B-01..B-04 | 浏览器/压测脚本 | evidence/ + 压测 287 项 |

### 审查 42–45 状态

- **42 配置编辑推进版本**：v2 双轨 revision——动作/底牌走 `hand.revision`
  （setHeroCards/pushOp/undoLastOp 均 bumpRevision），名单/盲注/位置走
  `sessionRevision`（completeCommit 推进）；S-07/S-08 守卫测试保证旧响应不写回。
- **43 审计失败退出 0**：`_verify_all.py` 尾部补 `sys.exit(1 if fails else 0)`，已验证。
- **44 回归用例不杀缺陷**：压测 A1/A7/B1/B2 以独立 treys 参考逐位对账；
  v2 record 测试断言落库内容（named 键、uid、幂等 False），非恒真断言（41 项审计在库）。
- **45 reset 测试残留令牌**：`test_stats_reset_default_is_self_only` 已有
  `finally: if old_token is not None: os.environ[...]` 恢复，确认在位。

## 3. 限制（如实声明）

1. **真机未测**：手机验收基于无头浏览器 375×812 视口（布局/触控热区/无横向滚动），
   未在物理 iOS/Android 设备验证触觉与系统级行为（如软键盘遮挡）。
2. **manual_close 不产生学习样本**：手工结束的牌谱不完整，按契约不接续画像；
   结算全部需人工填写。
3. **无 SB 满桌拒绝**：满桌（9 人）设置无小盲会被拒绝，须撤桌或现场校准（契约 P04）。
4. **并发延迟线性放大**：单 worker + GIL，8 并发下 advice 延迟约为串行的 3–4 倍
   （压测 A1：均值/最大延迟见压测输出）；本地单人场景无感，多人共享部署需多 worker。
5. **learningJobs 重试为手动/页面恢复触发**：契约明确本期不做后台定时器；
   UI 在会话栏显示"学习记录发送失败，可重试"。
6. **浏览器 check() 自动化兼容**：自定义样式复选框对 Playwright `check()` 不可点
   （视觉遮挡判定），脚本验收需用原生 click/evaluate；真人操作不受影响。
7. **旧版单手工具 UI 已被替换**：logic.js 保留（logic.test.mjs 11 项在库），
   但 app.html 不再加载旧版界面；`_verify_all.py` 相关守卫检查已对齐 v2 落点
   （esc 转义、handId 生成、X-Player-Id、撤销/重录）。

## 4. 数据迁移

- localStorage 旧键（旧版单手工具配置）由 `loadLegacyConfig` 兼容读取；
  v2 会话存 `paishi_tournament_session_v1`，schemaVersion=1，损坏/未来版本保留原文可导出。
- opponents.json `_migrate` 会把 v1 结构补 `schema: 2` 标记（内容等价，一次性）。
- 部署打包继续**排除 backend/data/opponents.json**（服务器学习数据不可覆盖）。

## 5. 真实统计哈希与资源清理

- opponents.json 基线哈希
  `9fc73f972ac7cfadd3aa8d60c7a398fb40d5ac183bc367acaf0d239ab859974e`
  在全部测试前后保持不变；压测/浏览器学习链路产生的样本（stressuser1、浏览器 uid、
  `_migrate` 的 schema 标记）均已清理或恢复基线。
- 本任务启动的进程：本地 uvicorn（端口 8137）两段均已停止，端口释放；
  浏览器测试标签已关闭并清空 localStorage。
- 无保留的失败日志（压测/审计最终全绿；中间失败截图未保留——失败均为脚本缺陷，
  已在修正时定性）。

## 6. 契约矛盾与调整说明

- `_verify_all.py` 审计项 09/10/13/15/22/25/26/27/28/29/30 引用旧版 app.js 落点
  （state.unconfirmed、resetHandState、AppLogic.escapeHtml、async-guard.test.mjs 等）。
  app.js 重写为锦标赛版后这些标记不复存在。处理：逐项对齐到 v2 等价落点
  （coordinator 的 stillCurrent/stale 丢弃、session.js 的 undoLastOp/bumpRevision、
  esc() 转义、newId("h")、playerHeaders 的 X-Player-Id），审计语义不变、项数不变。
- A04 契约（"保留既有 X-Player-Id 命名空间"）在 P0–P4 实现中遗漏前端发送头与
  record 链路，P5 验收时发现并补齐（见 §1），未缩减范围。
