# 2026-09-19 全面验收覆盖矩阵

当前结论：101个ID中99项在注明层级通过，2项的桌面模拟部分通过、物理手机部分待确认（B-04、U-11）。这是行为覆盖矩阵，既不是101个独立自动测试，也不是对所有浏览器/设备的保证。此前53已执行、46部分执行、2未执行的记录已由本轮补测更新。

证据缩写：browser/core = `tests/browser/session.e2e.mjs`；新增浏览器分组 = `tests/browser/acceptance.e2e.mjs`；领域/协调器 = `tests/frontend/*.test.mjs`；API = `tests/test_session_context.py`、`test_session_regressions.py`、`test_acceptance_edges.py`。新增脚本17组场景通过，45组布局截图位于 `docs/screenshots/full-acceptance-2026-09-19`。

验收按用户确认后的简化规格：物理桌型6/8/9、初始自己固定底部1号座，其他人可直接改整数筹码。C-18、L-06、B-01保留与旧逐座向导的差异说明。S-13采用请求验证后才写入动作，400时没有已提交动作需要回滚，旧响应由请求身份守卫。详情见 [全面验收报告](./FULL-ACCEPTANCE-2026-09-19.md)。

| ID | 状态 | 证据与限制 |
|---|---|---|
| M-01 | 通过 | session.test.mjs：连续三手固定身份与角色轮转 |
| M-02 | 通过 | session.test.mjs：换手保留物理座位/余额 |
| M-03 | 通过 | regressions.test.mjs + browser：6→5 人，排除零筹码 |
| M-04 | 通过 | session.test.mjs：转走再入座新身份和8000余额 |
| M-05 | 通过 | session.test.mjs：英雄换座与旧快照 |
| M-06 | 通过 | browser：弃牌后下一手仍在名单 |
| M-07 | 通过 | 浏览器 allin-and-adjustment + API P15：低于盲注的玩家全下后仍在本手六人名单，人工结算前不淘汰 |
| M-08 | 通过 | browser：英雄余额0后 ended，摘要新增而手数不加 |
| M-09 | 通过 | session.test.mjs / test_session_context.py：非法身份 |
| M-10 | 通过 | test_acceptance_edges.py：12次固定随机排列，prepare mapping及含3个真实动作的完整view逐字段一致 |
| C-01 | 通过 | browser：BB200、50BB确认为10000 |
| C-02 | 通过 | 领域精确换算 + 浏览器 rounding-and-input：10001筹码三次切换单位保持10001 |
| C-03 | 通过 | 领域验证99.9→100和100.5→101；真实表单分别取消0.333BB取整、接受0.335BB取整 |
| C-04 | 通过 | 浏览器空白/负数/NaN/Infinity/超安全整数均拒绝；API畸形类型4xx |
| C-05 | 通过 | test_session_context.py F2 + browser：五次弃牌结算 |
| C-06 | 通过 | 浏览器 f3-showdown 原F3动作与五张公共牌：仅P2/P6未知，UI显示待核对 |
| C-07 | 通过 | 浏览器 f3-showdown 填9800/10300，刷新后下一手余额逐位精确承接，总60000 |
| C-08 | 通过 | 浏览器F3先填一行按钮仍disabled；所有未知余额填写后才允许提交 |
| C-09 | 通过 | browser：填写全部实际余额后刷新不覆盖 |
| C-10 | 通过 | session.test.mjs：差额必须说明并确认 |
| C-11 | 通过 | session.test.mjs M04：转出再转入，独立于本手结算 |
| C-12 | 通过 | 浏览器 three-hands：下一手P1=9900/P2=10000/P3=9800，三次刷新底池均300 |
| C-13 | 通过 | 浏览器 three-hands：同一handId连续刷新3次，底池及动作不重复 |
| C-14 | 通过 | session.test.mjs + browser：100/200→200/400承接余额 |
| C-15 | 通过 | session.test.mjs：不足两人结束 |
| C-16 | 通过 | test_session_context.py：超额下注退回 |
| C-17 | 通过 | session.test.mjs + browser：手工结束原因/草稿/结束本桌 |
| C-18 | 通过 | 按简化规格，其他玩家输入整数筹码：真实表单P4=6000，其余10000；50BB/30BB等值由领域测试覆盖 |
| C-19 | 通过 | session.test.mjs：全桌总额溢出拒绝 |
| P-01 | 通过 | session.test.mjs：三手轮转 |
| P-02 | 通过 | regressions.test.mjs + browser：淘汰触发位置确认 |
| P-03 | 通过 | test_session_regressions.py：2–9人真实HTTP advice及四街回放 |
| P-04 | 通过 | test_session_context.py F4 + 每个人数HTTP回放 |
| P-05 | 通过 | session.test.mjs：3→2、原SB淘汰 |
| P-06 | 通过 | session.test.mjs：3→2、原BB淘汰 |
| P-07 | 通过 | session.test.mjs：单挑角色互换 |
| P-08 | 通过 | test_session_context.py F5a：空庄位引擎、角色断言 |
| P-09 | 通过 | test_session_context.py F5b + regressions：无SB建议名义BB |
| P-10 | 通过 | test_acceptance_edges.py：3–8人数逐一真实HTTP，无SB、每人前注25，底池/余额独立核对 |
| P-11 | 通过 | test_session_context.py：错误盲位4xx |
| P-12 | 通过 | session.test.mjs：人数不足结束 |
| P-13 | 通过 | test_acceptance_edges.py：实际满9人、SB=null请求明确4xx |
| P-14 | 通过 | regressions.test.mjs空庄位 + acceptance.test.mjs连续两手无SB，均要求重新确认 |
| P-15 | 通过 | API分别SB仅50/BB仅75，底池250/175，余额0但未弃牌；浏览器英雄50筹码全下仍保留 |
| P-16 | 通过 | test_session_context.py：每人前注 |
| S-01 | 通过 | session.test.mjs：重录保留身份、清动作及版本 |
| S-02 | 通过 | 浏览器three-hands：真实按钮双击/Enter混发，慢prepare期间只产生一个请求并仅推进一手 |
| S-03 | 通过 | session.test.mjs：同事务返回已保存状态，其他同源事务拒绝 |
| S-04 | 通过 | 领域正式余额不变；浏览器cancel-completed：结束牌谱取消结算后撤销并改为跟注，原handId/手数不变 |
| S-05 | 通过 | 浏览器three-hands：真实prepare正文延迟，期间输入不能改持久化草稿，释放后仅提交原事务 |
| S-06 | 通过 | browser：setItem抛QuotaExceededError，停原手、显示失败 |
| S-07 | 通过 | coordinator.test.mjs：真实协调器正文延迟版本守卫 |
| S-08 | 通过 | coordinator.test.mjs：等长分支隔离 |
| S-09 | 通过 | coordinator.test.mjs：requestId控制结果及busy |
| S-10 | 通过 | 浏览器icm-delayed-restore：真实view正文暂停时奖金600→1000；当前手仍600，下手1000 |
| S-11 | 通过 | 浏览器learning-failure/roster-icm/icm-delayed-restore + 领域冻结快照校验 |
| S-12 | 通过 | regressions.test.mjs + browser：网络错误恢复与重试 |
| S-13 | 通过 | 浏览器F3注入board 400：已确认ops保留，重试后可重新发3/1/1张；待确认op从未进入历史，隔离由requestId及失效建议保证 |
| S-14 | 通过 | 浏览器learning-failure：先确认record请求已发1次，再跨手释放503；旧job失败保留，当前动作可用 |
| S-15 | 通过 | 浏览器原F3：英雄弃牌后两家完整录至河牌结算 |
| S-16 | 通过 | coordinator.test.mjs：换会话旧响应作废 |
| L-01 | 通过 | 浏览器three-hands：playing重复刷新，恢复同handId/位置/起始余额并重放 |
| L-02 | 通过 | browser：结算填写、下手升盲、刷新恢复 |
| L-03 | 通过 | 浏览器three-hands换手后立即刷新；领域S03与105手循环验证同事务幂等及lastCommit |
| L-04 | 通过 | 浏览器view-double-and-save：保存失败提示后刷新恢复上一次成功动作；原core脚本覆盖结算保存失败 |
| L-05 | 通过 | 浏览器corrupt-legacy：损坏JSON/未来schema原文保留且可导出；领域覆盖损坏嵌套对象 |
| L-06 | 通过 | 浏览器corrupt-legacy：旧9座/50-100/10001原值准确预填，自己固定1号座，熟人功能默认关闭（简化规格） |
| L-07 | 通过 | 旧names配置保留且不启用；默认三手全程网络统计请求0；真实opponents.json前后哈希一致 |
| L-08 | 通过 | browser：两个真实标签页，旧页提示冲突并拒绝写入 |
| L-09 | 通过 | acceptance.test.mjs：领域连续105次提交，保留最新100摘要、105个待重试job、当前手与lastCommit |
| L-10 | 通过 | 浏览器browser-restart：关闭全部Chromium再用同一独立用户目录启动，整份会话和UID一致 |
| A-01 | 通过 | 浏览器three-hands：Node侧跨刷新累计Network，三手所有record及/api/stats/请求总数0 |
| A-02 | 通过 | test_session_context.py：get_stats/get_pool均不调用 |
| A-03 | 通过 | test_session_context.py：关闭record409 |
| A-04 | 通过 | 浏览器learning：开启后结算自动发送1次，原handId/context/payload与独立UID正确，成功清队列 |
| A-05 | 通过 | test_session_regressions.py：2–9人同handId重复record不增样本 |
| A-06 | 通过 | session.test.mjs M04：新成员不继承档案 |
| A-07 | 通过 | 浏览器learning-failure：旧手开启，下手关闭；迟到503仅标旧job，关停后刷新不重试且job保留 |
| A-08 | 通过 | session.test.mjs：manual_close不开记录任务 |
| A-09 | 通过 | test_session_context.py：v2和legacy引擎一致 |
| A-10 | 通过 | test_session_regressions.py：名义BB、M值和建议无None |
| A-11 | 通过 | browser：默认ICM不展示，仅配置奖金后显示 |
| A-12 | 通过 | 领域+浏览器heads-up-icm：3人500/300/200，淘汰后2人新余额500/300，旧快照不变 |
| A-13 | 通过 | 浏览器roster-icm：转出及转入均暂停ICM，实际重新勾选/填写奖金后生效 |
| A-14 | 通过 | API test_acceptance_edges.py及原测试：未知seat、旧seat/seat_index/index混用、非整数seat均4xx |
| B-01 | 通过 | 简化UI固定自己1号座的等价三手脚本；原F1自己5号座的CO/HJ/UTG序列由领域测试验证 |
| B-02 | 通过 | 浏览器heads-up-icm：3→2，真实扣盲/翻前翻后先手，下一手角色交换；另一淘汰分支由领域P05验证 |
| B-03 | 通过 | 浏览器async-body/icm-delayed-restore/picker-and-advice：真实DOM发起且实际Response.json受控延迟；协调器补逆序组合 |
| B-04 | 待真机 | 已完成320/375/390/768/1440 × setup/playing/settling × 6座/9座/2人空位共45组合，检测座位与公共牌碰撞并人工查看关键图；物理手机软键盘待确认 |
| U-01 | 通过 | 浏览器首次加载与恢复时MutationObserver监测熟人区域无可见闪现，默认隐藏；实际12次Tab未进入该区域 |
| U-02 | 通过 | browser截图：默认隐藏代号、设置折叠 |
| U-03 | 通过 | 浏览器three-hands/async-body/cancel-completed：录入、撤销、重录、取消结算/纠错、换手、刷新手数一致 |
| U-04 | 通过 | browser：英雄0筹码结束，手数不加 |
| U-05 | 通过 | 375×812触控选齐底牌牌库收起，刷新恢复也收起；牌桌与状态先于操作和折叠设置 |
| U-06 | 通过 | 浏览器picker-and-advice：迟到正文释放时选择/焦点/滚动不变，触控取消后焦点回发牌；确认后回动作区 |
| U-07 | 通过 | 浏览器async-body：真实advice正文受控暂停，仍可提交下一动作且view请求增加；旧建议不覆盖 |
| U-08 | 通过 | 浏览器view-double-and-save：卡住view后call双发+Enter混发，只有一个请求和一条新op |
| U-09 | 通过 | 浏览器async-body：成功推演后新动作立即清旧建议，后续英雄轮次advice503显示重试且动作可继续 |
| U-10 | 通过 | 浏览器save-advice-status双向交错：保存失败+建议成功仍标未保存；保存成功+建议等待分开显示 |
| U-11 | 待真机 | 320/375触控目标、输入焦点、金额标签和页面布局已测；真实Android/iOS软键盘、系统缩放及遮挡尚待实机 |
| U-12 | 通过 | browser四街 + API：未知余额为空，牌桌待核对，无模拟分池事实 |
