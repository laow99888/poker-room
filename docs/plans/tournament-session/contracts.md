# 连续牌桌：领域、金额、状态与接口契约

本文件的M/S/C/P/L/A编号由 [验收场景](./acceptance.md) 引用。用户目标、默认值和交付阶段见 [开发总说明](./README.md)。以下TypeScript风格结构是契约表达，不要求把原生JS迁移成TypeScript。

## 1. M：身份与持久数据

### M01 四种身份严格分开

| 名称 | 生命周期 | 用途 |
|---|---|---|
| sessionId | 创建整桌会话时生成 | 隔离不同牌桌及其当前手 |
| seatId | 此牌桌物理座位，1…capacity，固定顺时针 | 固定界面位置；空位也存在 |
| occupantId | 某玩家本次入座时生成 | 承接筹码、你的身份和可选代号；换人一定换ID |
| handId | 每次准备一手时生成 | 回放身份、提交幂等、学习去重；重录本手不换ID但增加revision |

BTN/SB/BB/CO不是玩家ID。engineIndex也不是玩家ID。映射仅在当前handId的快照内有效。

heroOccupantId为会话中的唯一“你”；通过入座记录找到当前seatId。你转桌/换座需显式操作：同一会话仅在手间换座，occupantId保持，旧座清空；普通“换人”不可偷偷把你转成另一个occupant。

### M02 建议持久结构

```ts
type Occupant = {
  id: string;
  status: 'active' | 'eliminated' | 'departed';
  confirmedChips: number;       // 最近已确认边界余额，整数筹码
  profileName: string | null;   // 可选，功能关闭时不显示/消费
};
type Seat = { id: number; occupantId: string | null };
type BlindLevel = { sb: number; bb: number; anteEach: number };
type PositionAssignment = {
  buttonSeatId: number;         // 可以指向空座
  smallBlindSeatId: number | null; // >=3人时可无小盲；2人必须存在
  bigBlindSeatId: number;       // 必须有实际入局者
  source: 'initial' | 'automatic' | 'manual';
  confirmed: boolean;
};
type Session = {
  schemaVersion: 1;
  sessionId: string;
  sessionRevision: number;
  phase: 'setup' | 'ready' | 'playing' | 'settling' | 'ended';
  capacity: number;
  seats: Seat[];
  occupants: Record<string, Occupant>;
  heroOccupantId: string;
  blindLevel: BlindLevel;
  displayUnit: 'bb' | 'chips';
  learningEnabled: boolean;
  icm: { scope: 'off' | 'final_table'; payouts: number[]; rosterConfirmed: boolean };
  positions: PositionAssignment;
  previousBigBlindOccupantId: string | null;
  previousBigBlindSeatId: number | null;
  handNumber: number;
  currentHand: HandSnapshot | null;
  settlementDraft: SettlementDraft | null;
  nextHandDraft: NextHandDraft | null;
  lastCommit: { sourceHandId: string; transactionId: string; nextHandId: string | null } | null;
  recentHands: HandSummary[];
  learningJobs: Record<string, LearningJob>; // key=handId，独立于摘要裁剪
};
```

类型定义中未列出的运行时fetch/DOM数据另放runtime，不序列化Promise、AbortController、busy、错误堆栈或计算中的advice。confirmedChips在playing中不随每次下注改写；实时余额来自回放结果，防止刷新再次扣盲。

### M03 本手冻结快照

```ts
type HandSnapshot = {
  handId: string;
  handNumber: number;
  context: HandContext;        // 见API，已由后端prepare验证
  heroCards: [string | null, string | null];
  ops: HandOperation[];        // 对外以seatId标记，不存易变化的pos标签
  revision: number;            // 本手请求相关输入变化时递增，整数不回退
  recordQuality: 'complete' | 'manual_close';
  manualCloseReason: string | null;
};
type SettlementDraft = {
  sourceHandId: string;
  sourceRevision: number;
  rows: Array<{
    seatId: number;
    occupantId: string;
    finalChips: number | null;
    source: 'engine_verified' | 'manual' | 'pending';
    confirmed: boolean;
  }>;
  adjustmentReason: string | null;
  differenceAccepted: boolean;
};
type NextHandDraft = {
  rosterEdits: RosterEdit[];    // enter / leave / move；具体载荷见M04
  blindLevel: BlindLevel;
  positions: PositionAssignment;
  learningEnabled: boolean;
  icm: Session['icm'];
};
```

HandContext固定本手入局名单、起始筹码、盲注、物理位置、画像开关和奖金。编辑下一手草稿不会改变currentHand.context，也不触发当前手重算。当前手动作/底牌更改则推进revision并重新回放。

### M04 成员操作

- enter：`{type:'enter', seatId, occupantId:newUUID, chips}`；只能空座、筹码>0。
- leave：`{type:'leave', occupantId, reason:'eliminated'|'table_transfer'}`；结算确认0自动提议eliminated，非0只允许table_transfer。
- move：`{type:'move', occupantId, targetSeatId}`；只能手间移到空座，筹码与occupantId不变；如果是你，heroOccupantId不变。
- replace：UI组合leave+enter，领域层原子应用二者；旧记录保留，不能覆盖其id。
- 弃牌是HandOperation，与上述名单操作完全不同。
- 剩余active少于2时不调用引擎。你的余额0或你离开本桌时结束当前会话；不得自动选择另一个人当英雄。

### M05 历史与保留

recentHands保留最近100手的HandSummary（ID、手数、原名单/位置、起止筹码、手工纠错原因、记录质量）。当前手完整ops独立保存，不受此数量限制。淘汰/转走的occupant在被历史引用时保留。

HandSummary至少保存 `handId, handNumber, context, finalChipsByOccupant, recordQuality, adjustmentReason, positionSource, settledAt`。开启学习且牌谱完整时，另在learningJobs保存 `LearningJob={payload:原手完整记录请求, status:'pending'|'success'|'failed', lastError:string|null}`；异步重试只能用这一份原payload。关闭的手不创建job。待同步job不随100手摘要窗口丢失，应独立保留至成功或用户明确放弃；成功job在对应summary被裁剪后可清理。本期无需后台定时器，在恢复页面/用户点重试时处理即可；当前学习模式关闭时暂停发送旧job，重新开启后才允许重试。

这不是永久牌谱库。超出窗口只裁剪最老summary，不影响当前会话金额、手数、lastCommit或画像存储。保存失败不得自动删除历史应付配额；先明确提示并保持上一已保存状态。

## 2. C：金额和真实结算

### C01 单一筹码基准

- 所有领域存储/API金额为非负安全整数筹码（JS Number.isSafeInteger；后端相同上限）。入局起始筹码必须>0，最终余额允许0。
- 盲注sb>0、bb>=sb；anteEach>=0；每项均整数。使用明确的config.bb，不从state.blinds_or_straddles[1]猜大盲。
- 显示BB=chips/bb，通常保留1位小数；筹码同时可见以便核对。显示舍入不能回写金额。
- BB输入允许小数；用十进制字符串解析换算到筹码。非整数结果按最近整数、恰好半个筹码向上取整，先显示换算值和取整值，用户明确接受后提交。例如0.333BB×300=99.9，确认后100；0.335BB×300=100.5，确认后101。使用十进制精确运算，不能依赖二进制浮点误差决定方向。
- 全桌合计、底池合计及换算结果也必须是安全整数；输入单项合法但合计溢出时拒绝提交。
- 单位切换只改变展示与输入解释。未提交的非法字符串原样保留并提示，不变成0或默认10000。
- 默认批量50BB使用建桌BB换算；盲注以后翻倍时，既有余额不翻倍。

### C02 三类余额不能混用

1. **手前已确认余额**：HandContext.participants中的starting_chips；是本手回放的输入。
2. **本手剩余/已投入**：由合法回放得到；当前手结束前可显示，0可能表示全下。
3. **手后实际余额**：由可信终局或用户核对确认；只有它能接续下一手。

当前PokerKit自动分池使用未知底牌的占位组合；这种末态stacks、payoffs、revealed只用于旧模拟显示，不是第3类数据。

### C03 结算预览来源

后端view增加 `settlement_preview`，每行以seat_id返回，并标可信度：

```json
{
  "status": "needs_manual",
  "reason": "unknown_showdown",
  "rows": [
    {"seat_id": 1, "suggested_chips": 9900, "source": "verified", "reason": "folded"},
    {"seat_id": 2, "suggested_chips": null, "source": "unknown", "reason": "unobserved_showdown"}
  ]
}
```

- 未到结束时status='not_ready'；手工结束可进入结算，但所有无法证明的行unknown。
- 所有人弃牌至唯一可获池者：不用对手底牌即可证明收益，可返回全部行verified。测试须覆盖未跟注超额退回。
- 需要未知对手底牌才能分池：活跃/摊牌者unknown；已弃牌者若投入确定，其结算余额可verified。
- 不允许通过检查模拟输出的“赢家”推断真实赢家；可信终局分类依赖动作和已知信息，而非结果恰好看起来合理。
- 无法可靠提取结算前投入时，行unknown并要求人工输入，比给错误的verified更可接受。
- UI默认隐藏未知摊牌的模拟亮牌/盈亏，提供说明“对手底牌未录入，请核对实际筹码”。不新增赢家选择和边池分配表。

### C04 结算校验

当前手入局者每人必须有一行，不能多/少/重复。finalChips全部有限整数>=0，confirmed全部为true后才可提交。已知预填值也需通过“确认当前填写值”或整体提交明确确认。

`delta = sum(finalChips) - sum(hand.context.participants.map(p => p.starting_chips))`。

- delta=0正常。未知行不能用差额自动填成某个玩家余额，除非用户主动采用计算提示并确认。
- delta!=0显示差额，要求非空adjustmentReason和differenceAccepted=true；允许现场漏记/初始录错的纠正，不把实战工具锁死。
- 人员转入/转出必须在本手结算之后算：先保存本手所有人的余额，再从下一手名单移除转出余额、加入转入余额。不得把转桌额当成牌局输赢来抵消delta。
- 当前手升盲草稿不改变delta和结算BB换算。

### C05 下一手余额与防重复扣款

下一手startingChips=本手确认余额（加上手间名单变化），不能再减上一手盲注/动作投入。PokerKit为新手只扣一次新盲注/前注。

例：六人无前注，全部10000，SB100/BB200，所有人弃牌至BB获胜。最终SB9900、BB10100、其他10000，总60000。新手BB身份换人，余额仍按occupant跟随；再次刷新同手不能继续扣200。

## 3. P：物理座位、位置和引擎适配

### P01 座位顺序和正常自动轮转

所有物理seatId按1…capacity顺时针。正常>=3人、上一手与下一手名单相同且没有特殊校准时：新BTN是旧BTN顺时针下一个有玩家座位，新SB是BTN后首位，新BB是SB后首位。常规空座始终保留在图上。

固定六人示例：原BTN=6、SB=1、BB=2，你=5(CO)。下一手BTN=1、SB=2、BB=3，你=5(HJ)。再下一手BTN=2、SB=3、BB=4，你=5(UTG)。这组方向是必须锁住的独立预期。

### P02 名单变化与校准

进入/离开/淘汰/move改变名单时，旧“下一个有玩家就是BTN”的算法不能直接视为已确认结果。

- 生成候选预览；保存上一手BB的occupantId及seatId。
- >=3人：候选新BB从上一手BB的物理座位向顺时针找新名单第一位；候选SB优先为旧BB座位上的原玩家（仍在时），否则允许空小盲；候选BTN可参考旧SB位置。这里只是预填，需要用户确认现场BTN/SB/BB。
- 候选BTN也必须满足下述顺序校验：优先参考旧SB座位（允许为空庄位），旧SB为空时参考旧BTN；若参考位置与新名单不自洽，从候选SB（无SB则BB）逆时针找前一位入局者作为候选BTN。不得使用“BB之后的下一人”作为兜底，否则可能把其他入局者夹在BTN与SB之间。
- 用户可以把buttonSeatId设为空座，smallBlindSeatId设null；bigBlindSeatId必须是实际入局者。空座绝不能以0筹码假玩家加入引擎。
- 确认前验证沿物理顺序的盲位/行动顺序能自洽。若SB存在，必须是BTN后第一位入局者、BB为其下一位；若SB为空，BB必须是BTN后第一位入局者。
- 旧存档可能已有不合法的位置草稿：在位置输入附近提示错误，禁止将非法输入标为已核对；提供“重新生成位置”，仅清除位置覆盖并按候选名单重新预填，保留本手ID、余额及人员变动。合法的用户校准不能被刷新静默替换。
- 本期不声称自动实现所有赛事死庄位/转桌处罚规则；明确位置预览由用户校准后才开始。普通无人变动的手无需额外校准。
- 一旦确认一次特殊位置，下一手仍显示预览，直到恢复常规BTN/SB/BB安排，防止空庄位被正常算法静默跳过。

### P03 二人单挑

两人时BTN同时是SB，另一个是BB；翻前BTN/SB先行动，翻后BB先行动。

已有两人且名单不变：两人交换BTN/SB与BB。

>=3转2：若上手BB仍在，新BB必须先建议另一名玩家，避免默认让同一人连续付BB；若上手BB离开，候选BB从上手BB物理位置顺时针找。显示并要求一次确认。实际场地不同安排可以通过校准指定，并在历史标记manual。

current安装PokerKit对player_count==2在get_effective_blind_or_straddle和_begin_betting中反转raw盲注向量。本期建议引擎顺序 `[BB玩家, BTN/SB玩家]`，传原始 `(sb, bb)`，以独立回放验证实际扣款/行动顺序。不能将多人数的索引0=SB假设套到两人。升级依赖后也必须保留该行为测试。

### P04 显示位置与范围位置

role徽标根据真实buttonSeatId/smallBlindSeatId/bigBlindSeatId展示。范围图用range_position，是相对行动顺序的估算标签，不能用它决定实际扣盲。

常规>=3人从SB开始顺时针、BTN结束的range_position表：

| 人数 | 顺序 |
|---|---|
| 3 | SB, BB, BTN |
| 4 | SB, BB, CO, BTN |
| 5 | SB, BB, UTG, CO, BTN |
| 6 | SB, BB, UTG, HJ, CO, BTN |
| 7 | SB, BB, UTG, MP, HJ, CO, BTN |
| 8 | SB, BB, UTG, UTG+1, MP, HJ, CO, BTN |
| 9 | SB, BB, UTG, UTG+1, UTG+2, MP, HJ, CO, BTN |
| 2 | 引擎顺序BB, BTN；BTN同时承担SB角色 |

每种新人数必须验证范围非空；复用现有范围时标为“基础范围估算”，不声称专门校准后的短桌GTO。heads-up BTN的call/rfi回退规则需明确测试；不能对缺图返回空范围继而把玩家从权益对手集合删除。

>=3人无小盲时，引擎按button后首位到button前最后一位排序，首位是BB。n名入局者的range_position取常规(n+1)人表去掉SB：例如3人使用 `[BB, CO, BTN]`，5人使用 `[BB, UTG, HJ, CO, BTN]`。本期该配置要求桌上确有空位、n<=8；满桌无空位却指定无SB返回明确4xx，提示校准现场位置，不自造LP等位置。最终交付必须覆盖3–8名且存在空位的无SB适配，不能全部以unsupported替代。

### P05 >=3人的引擎配置

order=从物理button顺时针开始遇到的入局玩家，按顺时针直到一圈结束；button有人时它最后入列。引擎只包含本手active且筹码>0玩家。

真实raw_blinds按order对应玩家分配SB金额/BB金额/0；无小盲首位只扣BB。button空位不加虚拟玩家。前注向量按每个实际入局者anteEach配置。

不要继续在decision/table/UI散落读取 `state.blinds_or_straddles[1]` 作为配置BB。新增适配上下文统一提供 `bigBlindAmount`、`anteEach`、`indexToSeatId`、`seatIdToIndex`、`rangePositionByIndex`。legacy路径仍提供同一内部上下文，让算法只有一个金额来源。

每个输出actor、seat、op、ICM行、side_pot成员必须映射回固定seat_id。物理空位不参与权益/ICM，弃牌玩家仍属于本手但不属于争池对手。

## 4. S：状态机与事务

### S01 转移表

| 起点 | 事件 | 终点 | 必须完成 |
|---|---|---|---|
| setup | 确认建桌 | ready | 验证名单/余额/位置，生成第1手handId及冻结context，持久化 |
| ready | 选齐底牌并开始回放 | playing | 发送当前快照；收到合法view后显示自动扣盲，不改confirmedChips |
| playing | 引擎牌局结束 | settling | 生成与handId/revision绑定的结算草稿，不自动推进手数 |
| ready/playing | 手工结束录入 | settling | 标manual_close、填写原因，不调用学习记录，不虚构剩余动作 |
| settling | 编辑余额/下一手成员/盲注 | settling | 只改草稿，未提交不改confirmedChips |
| settling | 取消结算/继续纠正动作 | playing或ready | 原手ID不变，失效结算草稿；revision推进，回放 |
| settling | 确认并下一手 | ready或ended | 原子保存结算、成员、位置、新手号/ID；重复请求返回同结果 |
| ready/playing | 重录本手 | ready | 清本手动作/底牌/结算，保留本手context和ID；revision推进；不移动庄位 |
| 任意 | 结束当前牌桌 | ended | 明确确认，保存结束状态；不自动新建/重置所有人的筹码 |

退出settling重录后，以前草稿不能自动恢复成已确认结果；可保留为用户参考，但需要重新确认。

### S02 结算并下一手的原子步骤

1. 校验sourceHandId和sourceRevision仍匹配当前手，取得唯一transactionId。
2. 校验所有原入局者最终余额及差额确认。
3. 在克隆状态中记录本手summary、应用确认余额、淘汰0筹码者。
4. 应用转入/转出/move。若你已淘汰/离开或不足2人，保存本手summary与最终余额，进入ended、nextHandId=null、手数不增加；跳过下一手位置及prepare要求。否则候选位置校准必须已确认，继续下一步。
5. 应用下一手盲注、学习开关与ICM；生成人数及prepare输入。取得后端规范化预览，核对仍是同一事务。
6. 在克隆状态中生成nextHandId、handNumber+1及冻结context，清本手草稿，设置lastCommit。
7. **一次持久化成功后**替换内存可见状态并渲染下一手。失败保留结算页及输入；不提前显示“第N+1手”。
8. 只有完整牌谱且本手开关开启时才在同笔保存中创建上一手学习job，载荷取原快照/ops，绝不能取新手payload；保存成功后，当前模式仍开启才异步发送，否则保留待同步。

相同sourceHandId/transactionId重试返回已保存nextHandId，不再次轮转、不再次加减筹码。按钮disabled是辅助，业务必须幂等。等待后端prepare过程中禁止第二次提交不同草稿；允许取消则需失效事务和响应。

先检查lastCommit幂等命中，再判断currentHand是否匹配sourceHandId，否则成功后的重试会误报过期。比lastCommit更旧的提交直接报告过期且不产生副作用；无需永久保留全部事务。相同源手的不同transactionId在源手已结算后也不能再次执行。

若准备新手服务不可用，可保留已校验草稿，但本期保持整笔结算待确认，不部分提交。这样重试不会产生“余额已保存、手数未保存”的半状态。

### S03 版本和请求协调

- sessionRevision覆盖会话草稿/事务；hand.revision覆盖本手输入。异步请求捕获 `{sessionId, handId, handRevision, requestId}` 和不可变请求JSON。
- 所有await之后、状态写回之前验证四者；最后的response.json()之后也验证。较新的同revision请求也要以requestId胜出。
- 旧请求的成功、错误、finally均不能修改新请求busy/error/view/advice。busy归属requestId，不是一个谁都能清的布尔量。
- 修改下一手草稿不改变当前手快照，不误触发当前手请求；需要预览则使用独立previewRequestId。
- 修改底牌、undo、重录或当前手修订，推进hand.revision，安排最新快照请求。AbortController仅优化，守卫是正确性保障。
- 防止Enter快速提交、点击/键盘双发，不能只在click入口检查busy。公共牌和动作使用统一submitOperation入口。
- 网络失败保留已确认动作。非法最后一步只允许回滚该次待确认操作，回滚也推进revision；禁止连续猜测性pop历史。
- record失败属于上一手summary的同步状态，只显示“上一手学习记录待重试”；不得覆盖当前手操作台或新手recorded标记。

### S04 实时录入与计算状态分离

runtime分别保存viewRequest与adviceRequest的ID/状态及source={sessionId,handId,handRevision}；本地保存状态另行管理。单一busy不得同时把动作验证、建议计算与本地持久化混为一体。

1. 用户提交一个动作后进入view pending；该动作可显示“待验证”，依赖下一行动者的按钮禁用。不接收多个基于旧actor的动作排队。
2. 当前view成功通过S03守卫后，接受该动作及新合法状态，释放录入锁。轮到你且未终局时可开始advice；advice pending不继续占用录入锁。
3. 用户继续记录实际动作或撤销时，立即失效旧advice并更新其可见状态，不等新view返回才隐藏旧建议。按钮合法性始终取最新已验证view，不能取advice决定。
4. advice失败只更新推演区域，不回滚已接受动作、不禁用整个操作台；重试使用当前手当前revision的不可变payload。
5. view失败仍按S03处理待确认操作。未验证的actor、金额和操作不能成为下一次输入的基准；断网不会自动降级成无校验录入。
6. 画面上的当前推演必须满足advice.source与当前已验证view.source及本手revision完全匹配。source只需在runtime维护，本期无需后端增加revision协议；以发出请求时的绑定信息判断。

用受控延迟验证非阻塞，而非承诺某种设备上的固定计算秒数。响应时间可实测记录，但不得为追求快而静默减少采样、跳过合法性验证或用历史建议填充新局面。

## 5. A：后端接口契约

### A01 新旧协议分流

保留原 `/api/hand/view` 和 `/api/hand/advice` 的legacy请求形式供已有测试/外部脚本；新增 `protocol_version:2` 的显式请求模型，由字段区分后归一到同一内部回放上下文。不要让同一字段在有无seat_id时猜语义。

新增只读 `POST /api/table/prepare` 验证HandContext并返回规范化映射；不写用户数据、不代替前端结算持久化。

```json
{
  "protocol_version": 2,
  "hand_id": "h-1",
  "hand_number": 1,
  "context": {
    "session_id": "s-1",
    "capacity": 6,
    "hero_occupant_id": "p-5",
    "participants": [
      {"seat_id": 1, "occupant_id": "p-1", "starting_chips": 10000},
      {"seat_id": 2, "occupant_id": "p-2", "starting_chips": 10000},
      {"seat_id": 3, "occupant_id": "p-3", "starting_chips": 10000},
      {"seat_id": 4, "occupant_id": "p-4", "starting_chips": 10000},
      {"seat_id": 5, "occupant_id": "p-5", "starting_chips": 10000},
      {"seat_id": 6, "occupant_id": "p-6", "starting_chips": 10000}
    ],
    "button_seat_id": 6,
    "small_blind_seat_id": 1,
    "big_blind_seat_id": 2,
    "blinds": {"sb": 100, "bb": 200, "ante_each": 0},
    "learning_enabled": false,
    "profile_names": {},
    "icm": {"scope": "off", "payouts": []}
  }
}
```

prepare返回：`{context:规范化上下文, mapping:[{seat_id,occupant_id,engine_index,range_position,roles}], warnings:[]}`。输入participant顺序不定义引擎顺序，后端按物理座位/按钮确定；请求乱序也产生同一映射。

HandSnapshot.context直接保存上述snake_case的HandContext结构；Session与草稿使用camelCase，通过唯一的prepare输入构造器转换，其他模块不自行猜字段名。profile_names是occupant_id到非空代号字符串的映射，只允许本手参与者的ID；关闭模式统一传空对象。角色徽标roles只能取BTN/SB/BB，允许两人BTN与SB同时存在；range_position独立。

view/advice v2额外带 `hero_cards`、`ops`、`iterations`、可选seed；context必须与prepare一致的结构，但每次请求仍做验证，不信任客户端先调过prepare。

v2动作：`{op:'action',seat_id:3,type:'call'}`；raise附`to`为本街加注到的整数筹码。board=`{op:'board',cards:[...]}`。拒绝v2动作同时携带矛盾的legacy seat角色。

### A02 view/advice输出

v2必须返回 `hand_id`、`seats[{seat_id,occupant_id,range_position,roles,stack,bet,...}]`、`actor_seat_id`、hero.seat_id、board、taken、street、hand_over及settlement_preview。

旧pos文本可作为显示兼容字段，但前端事件不可再用它作为身份。advice opponents、side_pots.seats和ICM rows同样提供seat_id；跨模块不通过中文名字或range_position猜玩家。

实际余额、投入、底池及下注金额保持整数筹码；EV、ICM等期望值和BB换算可以是小数，不能写回实际余额。完整的error.detail给用户，预期非法输入返回4xx，不能逃逸500。

### A03 输入校验

校验capacity2–9、participant2–capacity、seat_id唯一且范围合法、occupant_id唯一、hero恰好一次、初始筹码正整数、所有身份引用有效、位置满足P规则、金额安全且有限、奖金有效。

prepare不要求底牌；view/advice要求合法英雄两张牌。未录底牌也能手工结束为manual_close，不通过构造假的英雄牌调用advice。

1人结束状态由会话模块处理，不把player_count=1送进PokerKit。未知角色/不支持映射明确返回4xx并停准备页，不能自动补假人到6人。

### A04 学习模式后端门控

legacy HandIn新增可选learning_enabled默认false；v2从context读取。advice_for显式接收该值：false时get_stats/get_pool均不调用，profile_names/names忽略。基础player_intel当手动作分析仍运行。

兼容指原请求结构仍可回放，并不保留旧版默认启用学习的行为；需要学习的旧调用方必须显式开启。开关是产品行为控制，不是账号授权边界。

record入口接收开启标记及完整牌谱；关闭请求返回409、明确“学习记录未启用”，且不调用_save。legacy测试需要记录的用例显式开启；不能为了让旧测试通过使默认继续记录。

stats GET/reset实现保留，不因一次本地关闭而删除服务端所有用户的数据。主UI关闭时不调用；重新开启后再恢复调用。保留既有X-Player-Id命名空间，它与sessionId/occupantId不同，不能混用。

### A05 ICM与短桌

只有icm.scope=final_table且用户确认本桌包含全部剩余选手时计算。奖金从第一名向后，不超过剩余人数。淘汰后下一手可把已支付末位从剩余奖金草稿移出，但需要用户确认；多桌转入转出时默认关闭ICM并提示重新确认范围，不自动把本桌当全赛事。

无特殊收入下一个淘汰案例：3人奖金[500,300,200]，本手结束一人淘汰，下一手两个余额与剩余奖金[500,300]；上一手ICM仍保留原三人快照。不得以“少一个玩家”直接修改旧手payouts或把支付奖金当筹码扣减。

## 6. L：本地恢复与写入失败

- 独立key建议 `paishi_tournament_session_v1`；序列化整个会话作为一次替换。sessionRevision在每次可保存修改后递增。
- 存储适配先生成/校验完整JSON，再setItem；异常不更新UI为“已保存”。关键换手提交失败保留旧正式状态及当前草稿。
- 自动保存输入草稿可做短防抖，但换手提交必须flush；刷新时能恢复最后成功保存的草稿，并清楚显示尚未保存提示。
- 恢复校验schemaVersion、ID、金额、名单、phase、hand/context一致性。损坏或未来schema数据先保留原值，不悄悄覆盖；提供导出原始文本和重新建桌入口。
- 不持久化模拟建议/引擎状态为事实；恢复playing后根据同一handId/context/ops重新view，hero行动时再advice。恢复settling保留人工输入，不让回放结果覆盖已编辑余额。
- 旧 `paishi_config_v2` 可作为首次新建桌预填；旧pos没有固定座位信息，要求一次确认，不擅自推断英雄seat。旧names与统计保留但不自动启用。
- 单浏览器多tab：监听storage变化；发现别的tab更新本session，当前tab进入冲突提示并停止关键写入，允许“重新加载最新”。本期不实现并发合并；至少避免旧tab无提示覆盖新余额。
- 不落地密钥、Cookie、令牌或真实统计内容到测试报告/仓库。测试用假ID和临时存储。

## 7. 非目标与可延期项

本期不开发自动读牌/桌面识别、现金桌补买、BBA新收费规则、完整赛事裁判系统、永久牌谱库、用户登录、云同步、个人风格训练、自动精确分配未知摊牌边池。它们不能成为完成已承诺固定座位、连续换手、手工余额、2–9人和恢复功能的前置条件。
