# 手机启动空白排查与修复

发布状态更新：2026-09-19 已将修复提交 `16b3b66` 推送并部署到 `https://ace.iiiu.im`，线上启动及12组操作回归通过。以下保留修复过程的阶段性记录；最新部署、备份和验收结论见 [上线验收记录](./DEPLOYMENT-2026-09-19.md)。用户原Via真机仍待使用反馈。

## 状态与结论

用户在安卓Via及Zcode内置浏览器打开`https://ace.iiiu.im`时，只看到静态表单，没有座位和牌库，点击开始无反应。

本轮从外部用隔离Chromium实际访问该域名，生成6个座位、52张牌，未选庄位直接开始时能显示正确提示；线上资源返回200、JS MIME正确，经Cloudflare REVALIDATED/MISS获取。随后用户提供Zcode控制台的实际网页报错：

```text
app.js?v=20260923qf:10 Uncaught SyntaxError:
The requested module './session.js' does not provide an export named 'calibrateStartingChips'
```

**已确认故障机制是主模块与依赖模块版本不匹配，导致应用启动在模块链接阶段被终止。** 用户同时贴出的Zcode Electron preload错误属于Zcode自身，不是该网页缺少导出的原因。新查询URL的线上`session.js?v=startup-20260919-1`返回200且包含`calibrateStartingChips`和`closeQuickFold`导出，说明源站当前有新依赖；用户命中的旧缓存或不同发布副本仍需上线后核验。尚未在用户原Via设备确认恢复，不宣称线上已修复。

独立故障注入先以同批新增的`closeQuickFold`缺失复现：座位数0、牌库0、错误提示为空，与用户截图一致。收到用户实际报错后，将旧依赖夹具改成缺少`calibrateStartingChips`；修复后经版本化URL避开旧依赖，6个座位、52张牌正常生成，见`calibrate-cache-final/results.json`。阻断主脚本下载也会产生同样空壳。

## 本地交付

1. `app.html`、主脚本、所有静态/动态模块导入统一使用资源版本`startup-20260919-1`，避免新主脚本继续访问旧的无版本依赖URL。
2. 新增经典脚本`frontend/startup.js`，在应用模块之前安装错误提示。主模块网络失败、语法/初始化错误、15秒未就绪均显示可见错误和重新加载入口。
3. 建桌按钮在初始化完成前禁用；只有界面已初始化才启用。启动脚本自身下载失败而主模块正常时，主模块仍可完成初始化并解锁。
4. 缺少ES模块、BigInt、structuredClone或AbortController时显示浏览器能力不足提示；JavaScript被禁用时有`noscript`提示。不声称兼容所有旧版安卓WebView。
5. 重新加载不清除localStorage，用户现有手牌记录保持原样。错误详情按文本渲染，长字符串在手机内换行。

## 验证

测试脚本：`tests/browser/startup.e2e.mjs`。默认检查本地隔离服务；`POKER_QA_URL`改地址，`POKER_STARTUP_OUTPUT`指定证据目录，`POKER_STARTUP_SCENARIO`选择用例。

| 场景 | 结果 |
|---|---|
| normal，正常启动 | 6个座位、52张牌；开始按钮能提示未选庄位 |
| old-dependency，缓存返回旧无版本依赖 | 修复前空壳，修复后使用版本化模块正常启动 |
| module-failure，主模块下载失败 | 修复前无提示，修复后显示失败；点击重载后恢复，存储哨兵值仍存在 |
| syntax-failure，模块语法错误 | 显示具体错误，开始按钮禁用 |
| unsupported，缺少structuredClone | 显示浏览器内核能力提示，停止初始化 |
| bootstrap-failure，启动保护脚本下载失败 | 主模块仍正常生成界面并启用开始 |
| javascript-disabled，关闭JavaScript | 显示明确提示，按钮禁用 |
| stalled，模块请求一直挂起 | 15秒后显示启动超时 |

8类本地用例通过；证据在`docs/screenshots/mobile-startup-2026-09-19/`。`online-before/results.json`是实际线上检查，不应与本地故障注入结果混淆。`stalled-after/results.json`保留了测试脚本等待整个页面加载的时序失败；页面已正确显示超时，修正脚本等待策略后`stalled-final/results.json`通过。

`node --test tests/frontend/*.test.mjs`：74 passed。`quick-fold.e2e.mjs`：12组通过，含6/8/9座桌面/手机建桌及连续弃牌、保存/请求失败、刷新和跨标签页。未改后端，本轮未重跑全部pytest。错误状态375px截图已打开检查；`git diff --check`通过。

## 线上更新与核验

本轮只修改共享工作区，没有可用服务器部署入口或已执行的部署步骤，**线上尚未部署本轮修复**。线上已存在quick-fold后端接口，本次启动故障不能归因于缺少该接口。

让负责部署的Zcode同步完整`frontend/`目录，尤其不能漏掉新增`startup.js`及现有`session.js`、`session-storage.js`、`request-coordinator.js`。确保`/app.html`引用`startup-20260919-1`版本；Cloudflare若仍返回旧HTML，应清除该站相应静态缓存。不能只更新`app.js`。

发布后先在原Via浏览器重载，检查庄位6/8/9个按钮和52张牌是否出现。若仍失败，记录顶部错误文本及Via/WebView版本；若仍连启动提示都没有，则优先确认实际收到的HTML版本、缓存或脚本禁用情况。不要用“清除全部站点数据”作为第一步，以免删除本地手牌。

## 清理

本轮隔离服务PID22336和准确报错补测服务PID17884均已停止，8142释放；原8765服务未动。浏览器PID36876、38464、25308、14300、23792、36036、31996、31436、23400、16444、15180、34784、17204、37440、26864均关闭并核对不存在。真实统计SHA256仍为`9fc73f972ac7cfadd3aa8d60c7a398fb40d5ac183bc367acaf0d239ab859974e`。

空测试目录`C:\Users\Administrator\AppData\Local\Temp\poker-session-qa-6366lrhy`和`C:\Users\Administrator\AppData\Local\Temp\poker-session-qa-zq0dt683`的删除均被自动审批拒绝（`blocked by policy`），已保留；无已删除路径。失败截图、结果和回归证据保留。没有创建提交或使用子代理。
