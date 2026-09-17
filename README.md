# 牌室 · 德州扑克离线决策辅助

完全离线的德州扑克分析工具：点选式牌局录入 + 三层决策算法 + 学习型对手画像。
无 AI 推理、无网络请求，全部计算在本机 CPU 上完成。

## 运行

```bash
pip install -r requirements.txt
python run.py
# 浏览器打开 http://127.0.0.1:8765
```

开发/测试依赖：`pip install -r requirements-dev.txt`，运行 `python -m pytest tests -q`。
深度推演脚本：`python _verify_layer1.py` / `python _verify_layer3.py`（失败即非零退出）。

## 四层决策算法

| 层 | 内容 | 入口 |
|---|---|---|
| 1 | 蒙特卡洛权益、牌面结构（湿润度/连牌/同花）、范围优势 RA / 坚果优势 NA、SPR 与底池赔率的混合策略建议 | `textures.py` `equity.py` `decision.py` |
| 2 | 对手建模：按位置人群池统计 + 具名档案（VPIP/PFR/3bet），按行为情报（rfi/3bet/跟注/弃牌）贝叶斯式收窄对手范围 | `opponents.py` |
| 3 | CFR+ 求解器：河牌单街精确解；翻牌/转牌把剩余牌 rollout 成期望胜率作终值（近似）。双方范围分桶抽象后输出均衡参考频率 | `cfr.py` |
| 4 | ICM 决赛桌奖金换算：Malmuth–Harville 子集 DP 精确计算各座位奖金期望，配置奖金结构后展示 | `icm.py` |

第三层的正确性由教科书锚点局保证（tests + 推演脚本双重校验）：

- **Kuhn 扑克**：游戏值收敛到理论值 −1/18，策略收敛到已知均衡族（J 诈唬 α、K 下注 3α、Q 遭注跟注 α+1/3）；
- **AKQ 单街**：空气牌诈唬频率 = B/(P+B)、抓诈方跟注频率 = P/(P+B)，在半池/底池/超池三种尺度下全部吻合；
- **河牌 AKQ 同构**（AA+22 vs KK，真实 treys 评估）：复现上述定理频率。

工程约束：求解器固定迭代数且范围封顶（120 组合/人、8 桶抽象），
rollout 固定种子，同参数结果逐位可复现；翻牌/转牌/河牌单挑限定，
超出的场景优雅降级并说明原因。翻牌/转牌的 CFR 是"期望终值近似"
（不含未来街的行动博弈价值），界面已标注（近似）。

## 功能

- **点选式牌局录入**：52 张牌网格选底牌与公共牌，操作台按座位顺序录入动作，支持撤销与一键重开；
- **实时建议**：胜率/平/负分布、跟注所需胜率与 EV、SPR、M 值、牌面结构标签；
- **行动建议**：启发式混合策略（带百分比频率）+ 翻牌/转牌/河牌 CFR 均衡参考并列展示；
- **ICM 决赛桌面板**：配置奖金结构（如 500,300,200）后，实时展示各座位奖金期望与份额；
- **对手画像**：每手结束自动累计人群统计与具名档案，建议自动按统计收窄对手范围；
- **6/8/9 人桌**：位置感知范围图（RFI/3bet/跟注/防守），`backend/data/charts.json` 可调；
- 固定随机种子可复现结果（仅供测试用）。

## 技术栈与选型

| 层 | 选择 | 原因 |
|---|---|---|
| 牌力评估 | [treys](https://github.com/ihendley/treys) (MIT) | 纯 Python 免编译；eval7 的 C 扩展在 Python 3.14 无轮子，`backend/equity.py` 保留了 eval7 适配分支，环境允许时自动切换提速 |
| 博弈求解 | 自研 CFR+（`backend/cfr.py`） | 单街规模小到纯 Python 可控；避免引入带原生依赖的求解器，保证离线与可解释 |
| 后端 | FastAPI + uvicorn | 一个命令启动，参数校验内建 |
| 前端 | 原生 HTML/CSS/JS | 零构建、零外部资源（无 CDN、无在线字体），保证离线可用 |

## 结构

```
backend/ranges.py    手牌代码与范围字符串解析（TT+、A2s+、AKo…）
backend/charts.py    位置范围图加载与查询
backend/equity.py    蒙特卡洛引擎 + treys/eval7 适配层
backend/textures.py  牌面结构 + 范围/坚果优势（第一层）
backend/opponents.py 对手画像统计与范围收窄（第二层）
backend/cfr.py       CFR+ 引擎、锚点局、单街求解器：河牌精确 + 翻牌/转牌 rollout（第三层）
backend/icm.py       ICM 奖金期望：Malmuth–Harville 子集 DP（第四层）
backend/decision.py  建议链路：情报 → 范围 → 权益 → 建议 + CFR 块
backend/table.py     PokerKit 状态机封装（replay/校验/视图）
backend/app.py       FastAPI：/api/hand/*、/api/stats/*、/api/equity
frontend/            三栏单页（设置+牌库 / 牌桌 / 操作台+建议）
tests/               78 项测试：锚点局、河牌定理、多街对账、ICM 暴力枚举对账、集成与 API
_verify_layer1.py    第一层深度推演
_verify_layer3.py    第三层深度推演（收敛/确定性/稳定性/性能）
_verify_layer4.py    第四层深度推演（河牌回归恒等式、转牌全枚举对账、ICM 对账、性能）
```

## 路线图

- ✅ v2 四层决策算法（权益与结构 → 对手建模 → CFR 求解 → ICM 换算）
- v5 线上平台牌谱（Hand History）文件夹监控与自动复盘
- v6 用户系统（注册登录、学习数据按用户隔离）

## 合规提醒

本工具仅供复盘、学习与研究。在在线对局进行中使用任何实时辅助
（包括自动读牌）违反各平台规则，可能导致封号与资金没收。
