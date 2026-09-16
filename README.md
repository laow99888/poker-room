# 牌室 · 德州扑克离线胜率计算器

完全离线的德州扑克分析工具：点选式界面 + 蒙特卡洛胜率模拟 + 底池赔率计算。
无 AI 推理、无网络请求，全部计算在本机 CPU 上完成。

## 运行

```bash
pip install -r requirements.txt
python run.py
# 浏览器打开 http://127.0.0.1:8765
```

开发/测试依赖：`pip install -r requirements-dev.txt`，运行 `python -m pytest tests -q`。

## 功能（v1）

- **点选发牌**：52 张牌网格点选底牌、公共牌与对手手牌，无需手打牌名；
- **对手范围**：13×13 范围矩阵（同花/不同花/对子）+ 常用预设，支持 1–5 个对手；
- **胜率模拟**：蒙特卡洛 1 万–50 万次，输出胜/平/负分布；
- **底池赔率**：输入底池与跟注额，算出需要的最低胜率，并自动与模拟胜率对比给出跟注 EV；
- 固定随机种子可复现结果（仅供测试用）。

## 技术栈与选型

| 层 | 选择 | 原因 |
|---|---|---|
| 牌力评估 | [treys](https://github.com/ihendley/treys) (MIT) | 纯 Python 免编译；eval7 的 C 扩展在 Python 3.14 无轮子，`backend/equity.py` 保留了 eval7 适配分支，环境允许时自动切换提速 |
| 后端 | FastAPI + uvicorn | 一个命令启动，参数校验内建，后续牌谱监控/训练器同栈扩展 |
| 前端 | 原生 HTML/CSS/JS | 零构建、零外部资源（无 CDN、无在线字体），保证离线可用 |

## 结构

```
backend/ranges.py   手牌代码与范围字符串解析（TT+、A2s+、AKo…）
backend/equity.py   蒙特卡洛引擎 + treys/eval7 适配层
backend/app.py      FastAPI：POST /api/equity + 静态页面
frontend/           单页界面（牌面网格、范围矩阵、结果与底池赔率）
tests/              范围解析、已知对局基准（AA vs KK ≈ 81.9%）、API 校验
```

## 路线图

- v2：线上平台牌谱（Hand History）文件夹监控与自动复盘
- v3：翻前范围训练器（随机发牌出题）
- v4：河牌圈 GTO 求解（参考开源 TexasSolver / CFR）

## 合规提醒

本工具仅供复盘、学习与研究。在在线对局进行中使用任何实时辅助
（包括自动读牌）违反各平台规则，可能导致封号与资金没收。
