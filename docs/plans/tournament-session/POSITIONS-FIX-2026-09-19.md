# 庄位续手修复与系统回归

日期：2026-09-19。范围：用户截图中的位置校准阻断、旧草稿恢复，以及本次回归发现的牌桌重叠。用户授权直接修复；未使用子代理，未创建Git提交。

## 1. 结论与恢复方法

截图中的阻断是程序候选位置生成错误，不能归因于用户不懂德州。当前仅1–4号座有人、BTN=4、无SB、BB=1；旧程序生成下一手BTN=3、SB=1、BB=2，但3号庄后先遇到4号座，因此被程序自身的顺序校验拒绝。

本次修复后，该例预填BTN=4、SB=1、BB=2，确认后可继续下一手；特殊位置仍以现场校准为准。普通名单不变的手继续自动轮转。

已有存档的操作：刷新页面，在“人员变动 / 校准庄位”中点“重新生成位置”，核对现场位置后勾选“已核对现场位置”，再确认余额进入下一手。无需重建牌桌或重填余额。若浏览器还显示旧界面，使用Ctrl+F5重新加载资源。

## 2. 已修复问题

| 编号 | 问题 | 发生场景 | 交付方案及结果 |
|---|---|---|---|
| 75 / P1 | 程序生成的位置候选无法通过自身校验，阻断下一手 | 上手无SB、SB离桌，或新人入座使旧参考位置不再成立；旧错误草稿刷新后继续保留 | 修正BTN候选：优先保留合法旧SB座位（允许空庄位），无旧SB时参考旧BTN；冲突时从首个盲位逆向找前一入局者。新增就地校验和重新生成按钮，非法输入不能标为已核对；旧草稿、余额和身份保留。已通过领域与真实浏览器回归 |
| 76 / P2 | 新增位置标签后，牌桌信息在部分宽度重叠 | 320px六人桌的座位与公共牌约重叠2px；768px九人桌行动座位与邻座约重叠2px | 手机桌心宽度按侧座边界留出间距，减小牌间距和内边距；非手机牌桌高度由440px调为480px。保留文字大小。新增30组几何检查并复跑原45组布局检查，均通过 |

实现文件：`frontend/session.js`、`frontend/app.js`、`frontend/style.css`。领域规则没有放宽：非法位置仍拒绝提交。没有修改后端推演算法、采样率、用户历史统计或存储版本。

## 3. 复现与修复证据

先新增可失败的领域用例，再修改生成逻辑。截图等价用例在旧逻辑下抛出同一条“盲位与物理顺序矛盾”错误；扩展到7项后旧逻辑5项失败、2项通过。修复后增加穷举检查，最终8项全部通过。

- 14,186组：容量2–9、各有玩家座位组合、合法普通/空庄位/空小盲初始位置，检查新BB推进及生成位置的物理顺序。
- 6,836组：6/8/9座、每种剩余至少2人的淘汰组合、各旧BB位置，确认并实际执行下一手事务，检查手数、身份映射、余额守恒和源手不被改写。
- 独立固定预期：无小盲恢复、仅SB离开、仅BB离开、双盲离开、BTN离开、旧SB与BB之间有新人入座。不是仅调用同一校验器判断输出“合法”。

浏览器新增10组通过：6/8/9座在1440px和375px各连续提交4次；覆盖双盲转出、空盲/空庄和恢复普通轮转。另验证1440/375/320px旧错误草稿恢复，以及手机新人入座、合法手动校准刷新保留。按钮使用桌面点击或模拟触控，调用真实prepare接口并检查成功响应。旧存档案例通过领域模块构造隔离存储后刷新，不冒充从UI录入全过程。

布局缺陷先由原扩展验收报错，再用独立布局脚本稳定复现。失败截图与几何数据保留；最终布局脚本30组及原45组通过。已打开检查修复后的320px九人桌及1440px九人桌截图。

## 4. 本次执行结果

| 命令 / 检查 | 实际结果 |
|---|---|
| `python -X utf8 _verify_all.py` | 176项pytest、62项Node通过，退出0。pytest耗时173.04秒，有2条既有依赖弃用警告 |
| `python -X utf8 _verify_layer1.py` | 16项算法基础检查通过，退出0 |
| `node tests/browser/positions.e2e.mjs` | 新增10组通过；最后CSS调整后再次运行通过 |
| `node tests/browser/table-layout.e2e.mjs` | 6/8/9座 × 320/375/390/768/1440px × 进行中/结算中，共30组通过 |
| `node tests/browser/session.e2e.mjs` | 原核心及扩展流程通过：录牌、筹码、升盲、淘汰、保存失败、刷新、多标签页与网络恢复等 |
| `node tests/browser/acceptance.e2e.mjs` | 修复布局后17组全部通过，含45种布局、完整四街、未知摊牌、3人到单挑、迟到响应和完整浏览器重启 |
| `node tests/browser/workspace-ui.e2e.mjs` | 三栏布局、高亮、选牌反馈、位置标签、手机触控、真实推演等检查通过 |
| `node tests/browser/raise-units.e2e.mjs` | BB/筹码换算、取整确认、上下限、真实API加注金额及手机布局通过 |
| `node --check` | app.js、session.js及3个新增测试文件语法检查通过 |
| `git diff --check` | 通过；Git提示自动换行策略，不是差异错误 |

聚合测试完成后仅追加CSS布局调整与文档；布局、扩展验收、UI、加注和位置专项已覆盖最终CSS。无需为CSS重复运行全部后端推演测试。

证据入口：

- [位置浏览器结果](../../screenshots/positions-fix-2026-09-19/results.json)
- [30组布局结果](../../screenshots/positions-fix-2026-09-19/table-layout/results.json)
- [核心回归](../../screenshots/positions-fix-2026-09-19/session-regression/results.json)
- [扩展17组回归](../../screenshots/positions-fix-2026-09-19/acceptance-regression/results.json)
- [三栏及选牌回归](../../screenshots/positions-fix-2026-09-19/workspace-ui-regression/results.json)
- [加注单位回归](../../screenshots/positions-fix-2026-09-19/raise-units-regression/results.json)
- [旧错误草稿恢复后的界面](../../screenshots/positions-fix-2026-09-19/saved-invalid-375-recovered.png)

## 5. 验证边界与资源

本次验证证明已列出的状态、规则与交互通过，不等于没有任何剩余BUG。未验证真实手机软键盘、Safari、长期压力、100人并发或生产部署。人员变化后的候选位置仍需现场确认；本次不宣称自动实现全部赛事死庄位、转桌或处罚规则，也不自动识别现场真实筹码。

使用独立Chrome与8142隔离服务，未操作用户浏览器会话。真实 `backend/data/opponents.json` 前后SHA256相同：`9fc73f972ac7cfadd3aa8d60c7a398fb40d5ac183bc367acaf0d239ab859974e`。

资源清理结果：

- 本次服务PID22516已Ctrl+C停止，8142无监听；临时统计目录 `C:\Users\Administrator\AppData\Local\Temp\poker-session-qa-l_eutvtu` 已由TemporaryDirectory清理，路径不存在。
- 本次记录的浏览器PID30504、28664、28500、7060、38660、24364、14456、32352、38772、26832、33376、34196均已退出；原8765服务PID16608保持运行。
- 成功重启测试生成的 `C:\Users\Administrator\AppData\Local\Temp\poker-acceptance-profile-uKn9uK` 已核对为本次目录且不是链接；删除被自动审批系统拒绝，原因仅返回 `blocked by policy`。目录保留，没有绕过规则删除。
- 保留本轮 `docs/screenshots/positions-fix-2026-09-19/` 中截图及JSON。`acceptance-regression/failure-responsive.*`、`table-layout/failure-6-320-playing.*`、`table-layout/failure-9-768-playing.*` 是修复前实际失败证据，不代表最终验收仍失败。
- 保留项目依赖、浏览器运行时、原有第二版规格与其他无关修改。
