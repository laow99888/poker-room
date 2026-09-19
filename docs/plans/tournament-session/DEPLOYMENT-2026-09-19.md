# 2026-09-19 上线验收记录

## 发布结果

用户明确授权提交、推送和部署。修复提交 `16b3b66`（修复手机启动版本混用和连续记录异常）已推送到 `laow99888/poker-room` 的 `main`，并发布到 https://ace.iiiu.im 。资源版本为 `startup-20260919-1`。本记录与线上验收证据随后单独提交，未再修改运行代码。

服务器目录 `/opt/poker-tools` 为文件部署，不含Git仓库。发布前逐项比较已跟踪的后端、依赖及前端文件：后端与依赖一致，差异仅为本次前端修改。发布使用修复提交导出的完整 `frontend/`，先安装依赖脚本，最后安装 `app.html`，没有覆盖后端数据或服务配置。

## 备份与一致性

- 服务器保留目录 `/opt/poker-tools-releases/16b3b66/`，权限700，含发布前的 `before.tar.gz`、原文件/数据哈希清单及本次发布包，可用于回滚。
- 发布包SHA256：`0160c8a221f6dc124b6ed683232825dc3ef32b714d861ca886dc36a9a60b1433`，上传后核验一致。
- 完整9个前端文件与提交一致。Windows Git导出的归档使用CRLF；首次直接比较Git内部LF对象时哈希不同，按归档换行规则核验后9个全部一致。
- 发布前后及线上测试后的 `backend/data/charts.json`、`backend/data/opponents.json` 均通过原始SHA256校验。测试使用独立浏览器存储，未启用熟人学习。
- `poker-tools.service` 持续为active，PID2570396未变；静态文件发布无需重启。本机原有8765服务也未操作。
- 未写入任何密钥或令牌到仓库；提交前扫描156个待提交文件未发现提供的凭据或私钥。

## 实际验证

本轮再次运行 `node --test tests/frontend/*.test.mjs`：74项通过。未改后端，因此本轮部署未重复完整pytest或千手测试；既有312项pytest及1000手结果见深度测试报告，不能当作本轮线上压力测试。

以 `POKER_QA_URL=https://ace.iiiu.im` 运行以下真实浏览器检查，证据在 `docs/screenshots/deployment-16b3b66/`：

| 检查 | 结果 |
|---|---|
| startup.e2e.mjs，normal | 手机375px生成6个庄位选项、52张牌；点击开始能提示未选庄位；无JavaScript异常 |
| startup.e2e.mjs，old-dependency | 模拟无版本session.js缺少calibrateStartingChips导出；页面通过版本化URL避开旧依赖，正常启动 |
| quick-fold.e2e.mjs | 12组全部通过 |
| 6/8/9人桌，桌面1440px和手机375px | 各完成整圈弃牌，自动位置轮转、盲注前注扣除、估算延续、刷新恢复一致 |
| 操作边界 | 单人筹码校准、手动核对、网络与保存失败恢复、防连点、跟注后弃牌、已经弃牌续手、迟到响应与跨标签冲突均通过 |
| 移动端视觉 | 页面无横向溢出，已打开检查线上9人桌375px截图，牌桌与操作台正常显示 |
| 资源与服务 | HTML返回新资源版本，所有所需JS/CSS为200且MIME正确；Cloudflare首次MISS、再次REVALIDATED；服务日志所查发布时段无Traceback/ERROR/HTTP500匹配 |

页面仍有不影响操作的 `/favicon.ico` 404，本次未新增站点图标。测试采用Chromium手机尺寸与触控模拟，不等同于已经在用户原Via设备验证。建议直接刷新原网页；若缓存仍未更新，可打开 `/app.html?reload=16b3b66`，不要清除站点数据，以免删除本地手牌记录。

## 资源清理

本轮测试浏览器PID37128、23876、29216均已关闭，并核对不存在。没有新建本地开发服务器、watcher或监听端口，无需释放端口。

已删除本轮本地临时发布包 `C:\Users\Administrator\AppData\Local\Temp\poker-release-16b3b66.tar`。服务器发布备份和仓库验收证据作为交付资料保留；未清理历史任务的失败证据或用户文件。
