# 浏览器验收

启动专项：`node tests/browser/startup.e2e.mjs`。用`POKER_STARTUP_SCENARIO`选择`normal`、`old-dependency`、`module-failure`、`syntax-failure`、`unsupported`、`bootstrap-failure`、`javascript-disabled`、`stalled`；`POKER_STARTUP_OUTPUT`指定独立证据目录。验证手机空壳、旧依赖缓存、可见报错与恢复，线上检查和本地故障注入须分别记录；详情见`docs/plans/tournament-session/MOBILE-STARTUP-2026-09-19.md`。

连续千手专项：`node tests/browser/soak.e2e.mjs`，默认在同一9人桌操作1000手（950手快捷弃牌、50手输入现场余额），独立核算自己的强制投入，穿插40次单人校准、25次刷新、10次升盲和手机/电脑切换。校验手号、身份、历史快照、估算来源、禁止学习写入及存档恢复；每100手输出检查点和截图。`POKER_SOAK_HANDS` 可缩短调试运行，`POKER_SOAK_OUTPUT` 指定证据目录。约需12分钟，包含防连点等待，不代表推演耗时。

深度操作边界：`node tests/browser/deep-edges.e2e.mjs`，覆盖清空金额后恢复估算、修改淘汰结果后的人员草稿撤销、强制全下拦截，以及快捷弃牌/prepare/view真实请求挂起15秒后的恢复与重试。`POKER_EDGES_OUTPUT` 指定输出；`POKER_EDGES_CASE` 可单独选择用例。上述两项均需下述隔离服务，不包含在聚合脚本的浏览器部分。

完整牌局随机回归：`python -m pytest tests/test_playthrough_stress.py -q`，固定种子的120手真实API回放，覆盖6/8/9容量、2–9实际人数、空座/空小盲、短筹码、跟注/过牌/加注/全下到摊牌，检查每步筹码守恒、同牌谱重放一致及未知摊牌不被当成实际赢家。此项已由常规pytest收集。

快捷弃牌专项：`node tests/browser/quick-fold.e2e.mjs`，沿用下述隔离服务与Puppeteer配置。覆盖6/8/9座在桌面及手机的一整圈弃牌、单人校准、估算延续、未选牌弃牌、跟注后弃牌、已弃牌续手、防连点、网络/保存失败、刷新及多标签冲突。`POKER_QUICK_OUTPUT` 可指定输出，默认 `docs/screenshots/quick-fold-2026-09-19/quick/`。本专项需单独运行。

使用独立浏览器和隔离统计服务，不连接日常浏览器，不写用户的 opponents.json。

1. 从项目根目录运行 `python tests/browser/serve.py`，默认监听 127.0.0.1:8142。
2. Node.js 需要可导入 Puppeteer。也可用 `PUPPETEER_MODULE` 指向已安装模块的 ESM 文件 URL，并用 `BROWSER_EXECUTABLE` 指向 Chrome 可执行文件。不要把个人路径写入脚本。
3. 运行 `node tests/browser/session.e2e.mjs`。可用 `POKER_QA_URL` 修改地址，用 `POKER_QA_OUTPUT` 修改证据目录。
   再运行 `node tests/browser/acceptance.e2e.mjs` 完成扩展验收；输出由 `POKER_ACCEPTANCE_OUTPUT` 指定，默认 `docs/screenshots/full-acceptance-2026-09-19`。
4. 脚本在 finally 中关闭自己启动的浏览器。验收完毕停止步骤 1 的进程；记录输出中的 PID 和临时目录，核对后清理。不得停止原有共享服务。

测试通过页面按钮、选牌、表单事件和真实 API 验证建桌、四条街、未知余额、草稿恢复、保存失败回退、升盲、淘汰续手、多标签页冲突。故障注入只替换测试浏览器里的 Storage.setItem；其后恢复原方法。`failure.json/png` 保留最后一次失败证据，后续通过不自动删除。

`python -X utf8 _verify_all.py` 聚合 pytest 和 Node 行为测试；`--browser` 额外运行上述两个浏览器流程（须先启动隔离服务）。不传 `--browser` 时不声称完成 UI 验收。

三栏布局与选牌反馈专项：运行 `node tests/browser/workspace-ui.e2e.mjs`，沿用同一隔离服务及 Puppeteer 环境变量。覆盖桌面三栏、步骤高亮、首张底牌和公共牌预览、键盘取消/重新选择、选齐后可见性、真实接口推演、九人桌响应式布局、手机触控和减少动态效果。`POKER_UI_OUTPUT` 可指定截图与结果目录。该专项需单独运行，不包含在 `_verify_all.py --browser` 中。

加注单位专项：`node tests/browser/raise-units.e2e.mjs`。检查 BB/筹码输入、单位切换保留金额、真实接口收到的整数筹码、越界拦截、零头取整确认与取消、全下上限、循环小数最小加注、刷新恢复单位、375px 布局，以及跟注不使用加注输入框。输出由 `POKER_RAISE_OUTPUT` 指定，默认 `docs/screenshots/raise-units-2026-09-19/`；该专项单独运行。

位置续手专项：`node tests/browser/positions.e2e.mjs`。覆盖6/8/9座的双盲离桌、空庄位/空小盲连续续手、恢复普通轮转、新人入座、旧错误草稿恢复与合法手动校准保留。桌面点击及手机触控均调用真实prepare接口；旧存档案例通过领域模块构造后刷新验证。`POKER_POSITIONS_OUTPUT` 指定输出，默认 `docs/screenshots/positions-fix-2026-09-19/`。

牌桌布局专项：`node tests/browser/table-layout.e2e.mjs`。覆盖6/8/9座、320/375/390/768/1440px、进行中/结算中共30组，检查含位置文字和估算标记的座位、公共牌、底牌、底池之间的重叠及页面溢出。输出由 `POKER_LAYOUT_OUTPUT` 指定，默认 `docs/screenshots/positions-fix-2026-09-19/table-layout/`。上述两个专项需单独运行，不包含在 `_verify_all.py --browser` 中。

扩展脚本用真实接口响应和受控 `Response.json()` 延迟验证并发，手机宽度使用触控点击。覆盖精度与取整确认、完整F3、双击与慢prepare、取消结算纠错、保存失败恢复、学习开启/关闭/迟到失败、3人到单挑、ICM转桌校准、损坏存档、105手领域历史窗口的配套验收，以及45种布局组合。浏览器重启测试会打印 `QA_PROFILE`；成功后该临时用户目录可按工作约定核对清理。此脚本没有真实手机软键盘，不将该项计为通过。
