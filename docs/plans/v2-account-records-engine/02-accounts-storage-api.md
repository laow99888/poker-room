# 账户、存储与接口实现规格

依赖：[实战记录契约](./01-real-records-and-statistics.md)。本文接口前缀采用 `/api/r2`，避免与旧 `/api/hand/v2` 混淆。URL 命名可在第一阶段统一调整一次，但必须同步契约、前端和测试，禁止存在两套含义不同的写入入口。

## 1. 账户生命周期与权限

### 1.1 注册和管理

账户状态为 `active / disabled`，角色为 `user / super_admin`。没有待审批状态。注册开放时创建 `active` 普通账户并登录；注册关闭返回 `403 registration_closed`，已经注册的用户继续登录和使用。

首个总管理员通过部署 CLI 创建，交互读取密码，不提供默认密码、不把密码写进命令行参数或 Git。普通注册不能提交角色；即使请求带 `role=super_admin` 也不得提升权限。不能禁用最后一个有效总管理员。

管理员第一期能力：开关注册、查询用户列表及账户状态、禁用/启用账户、配置计算配额、查看匿名化服务负载、发起密码重置。私人牌谱列表、导出及底牌不属于默认管理员能力；不实现任意用户身份模拟登录。

账户禁用立即使现有会话失效，并取消其排队任务；运行任务尽快协作取消。保留其历史，不等于删除数据。管理员高风险操作需要近期重新验证密码并记审计日志。

### 1.2 输入约束

- 登录名：3–32 位 ASCII 字母、数字、下划线；大小写不敏感，数据库存规范化形式并设唯一索引；显示名独立支持中文，1–64 字符。
- 显示名可不填，默认使用登录名，不增加首次注册必填项。
- 密码：15–128 个 Unicode 字符，允许空格和粘贴，不进行静默截断，不要求机械组合大小写符号；后端限制整体请求体大小，避免超长输入耗尽散列资源。
- 账户时区：合法 IANA 时区名，默认 `Asia/Shanghai`；不接受任意字符串代替时区。
- API 和 HTML 对展示名、赛事名、备注输出转义，禁止通过 `innerHTML` 直接拼接用户文本。
- 登录失败统一提示“账户或密码错误”，禁用账户也不向未登录调用者透露详细状态。重复注册提示不包含该用户的其他信息。

## 2. 会话与安全基线

使用经过维护的 Argon2id 实现保存密码，参数至少满足 OWASP 当前建议：内存 19 MiB、迭代 2、并行度 1；实施时在 4C8G 上测量登录开销后固定配置。密码散列自带独立盐，不能使用单次 SHA-256 替代密码散列。会话令牌则是高熵随机值，可以保存其 SHA-256 摘要用于查找。

推荐不透明服务端会话：使用密码学安全随机源生成至少 32 字节随机令牌，浏览器 Cookie 保存原值，数据库只保存摘要。生产 Cookie 使用 `__Host-poker_session`、`Secure`、`HttpOnly`、`SameSite=Lax`、`Path=/`，不设 Domain。开发环境仅在明确 localhost 模式允许普通非 Secure Cookie 名，生产配置禁止此例外。

登录时生成新会话；密码修改/重置撤销该账户全部旧会话；退出撤销当前会话。默认空闲有效期 24 小时、绝对有效期 7 天，服务端检查，不依赖浏览器自行过期。可配置但需给出上限；无需每个 GET 写更新时间，可限频更新以控制写压力。

所有状态变更采用 POST / PATCH / DELETE，不能由 GET 触发。Cookie 认证下必须校验 CSRF 令牌及 Origin；登录与注册也检查同源，不能只依靠 SameSite。CSRF 值可通过同源引导接口取得，会话轮换时刷新；未登录注册使用短期预会话 CSRF 上下文。CORS 默认仅同源，禁止 `allow_credentials=true` 搭配任意来源。

默认限流基线：登录每账户 15 分钟 10 次失败、每 IP 15 分钟 60 次；注册每 IP 每小时 10 次。超额临时退避返回 429，不永久锁定被攻击账户。登录、注册、密码重置和计算限流分别计数，不能互相绕过。实际值可由管理员配置，压测验证注册和登录散列并发不会挤占所有内存。

密码散列操作在专用受限执行器中运行，并发起点为 2；等待队列也设上限，超额返回可重试错误。不能让任意数量的注册请求同时申请 Argon2 内存，也不能在 API 事件循环直接执行密码散列。

无邮件部署下，管理员生成一次性重置令牌，通过项目外渠道交给用户；令牌有效 15 分钟、数据库仅存摘要、仅展示一次，完成后失效并撤销旧会话。不能把重置令牌写日志、URL 查询串、错误上报或导出文件。本任务不要求程序自动向任何人发消息。

服务端从有效会话解析 `owner_user_id`。请求中的 `user_id`、`X-Player-Id`、本地存储 UID、`X-Admin-Token` 均不能授予云端身份或管理员权限。

## 3. 持久化模型

PostgreSQL 为唯一云端权威来源；SQLAlchemy 管理访问，Alembic 管理迁移。账户、赛事、手牌、余额与任务不再写共享 JSON 文件。应用代码分为 HTTP / 领域服务 / 仓储与事务 / 计算适配四层即可，不新增不必要的通用框架。

所有核心表使用 UUID 主键；所有用户私有表保存 `owner_user_id`，外键用 `(owner_user_id, parent_id)` 复合约束或同等数据库保证阻止跨账户挂接。查询仍需按 owner 过滤，数据库约束不是鉴权替代品。

### 3.1 建议表及必需字段

| 表 | 核心字段及规则 |
|---|---|
| `users` | id、normalized_username 唯一、display_name、password_hash、role、status、timezone、revision、created_at、updated_at |
| `auth_sessions` | token_hash 唯一、user_id、csrf_hash、created_at、last_seen_at、absolute_expires_at、revoked_at；不得存明文 Cookie |
| `password_resets` | token_hash 唯一、user_id、created_by、expires_at、consumed_at |
| `system_settings` | registration_open、服务端质量档版本、默认配额、revision；只允许受控字段更新 |
| `user_compute_limits` | user_id 唯一、最大运行/排队数、每日 CPU 秒上限、revision；覆盖默认值 |
| `tournaments` | owner、name、status、started_at、ended_at、currency、final_rank、paid_places、actual_prize、revision、deleted_at |
| `entries` | owner、tournament_id、entry_number、buy_in、fee、started_at、ended_at、end_reason、revision；同场编号唯一 |
| `table_sessions` | owner、entry_id、table_label、capacity、hero_occupant_id、current_hand_id、next_hand_number、status、revision |
| `occupants` | owner、table_session_id、seat_id、display_label、joined_at、left_at、leave_reason；位置不是身份字段 |
| `roster_events` | owner、table_session_id、effective_hand_number、类型、前后座位快照、原因、created_at |
| `hands` | owner、table_session_id、hand_number、played_at、recorded_at、state、current_revision_id、revision、deleted_at；同桌手数唯一 |
| `hand_revisions` | owner、hand_id、revision_number、context_snapshot、cards_snapshot、actions_snapshot、action_coverage、change_reason、created_at；版本唯一，不可变 |
| `balance_observations` | owner、occupant_id、手间边界、hand_revision_id、model_chips、actual_chips、provenance、basis、source_observation_id、needs_review、created_at |
| `settlements` | owner、hand_id、hand_revision_id、结束余额引用、hero_net_chips 可空、hero_net_valid、won_any_pot 三态、结果依据、revision、supersedes_id |
| `reconciliation_links` | owner、来源修订/观测、受影响手牌修订、状态、原因；用于补正依赖和过期标记 |
| `idempotency_records` | owner、操作范围、key、request_hash、resource_id、response_snapshot、created_at；前三项唯一 |
| `analysis_jobs` | owner、hand_revision_id、decision_index、state_hash、quality_profile、engine_version、状态、排队及租约时间、取消标记、尝试次数、资源消耗 |
| `analysis_results` | owner、job_id、输入快照引用、模块结果、采样元数据、完整度、过期状态、created_at |
| `import_batches` | owner、source_kind、source_checksum、status、统计、created_at；相同来源导入幂等 |
| `audit_events` | actor_user_id、action、资源类型及 ID、before/after 摘要、request_id、created_at；去除凭据与不必要的底牌内容 |

金额可使用 bigint；真实余额可空，建模余额按状态可空。赛事费用/奖金使用固定精度 decimal + 币种，不用浮点数；未知 `null` 不等于免费或奖金 0。数据量小的嵌套动作快照可存 JSONB，但必须经严格版本化 schema 验证，不能接受任意未验证 JSON。

每个 `hand_revisions` 保存当时全部实际动作、英雄牌、已知公开牌、已知对手牌及各自公开时点、盲注/前注类型、固定座位与引擎映射、手前余额来源、位置校准记录及 schema 版本。未知底牌存 null，不存引擎随机占位牌为真实牌。分析的具体输入另存快照或不可变引用，以便重放历史。

建议索引：`hands(owner, played_at)`、`hands(owner, table_session_id, hand_number)`、`tournaments(owner, started_at)`、`analysis_jobs(status, next_attempt_at, created_at)`、`analysis_jobs(owner, status)`、依赖来源索引。每条列表查询须有分页和上限，禁止把全部历史一次返回浏览器。

### 3.2 约束与状态

手牌状态：`recording -> awaiting_settlement -> settled_provisional / settled_confirmed`。由于英雄结束或整桌结束，不一定生成下一手。两种结算状态均可产生下一手；从暂定改为确认通过新修订完成。

`requires_reconciliation` 是独立质量标志，不替代生命周期状态。`action_coverage=complete/partial/summary_only` 与余额质量独立；整手确认不自动令英雄收益有效。

同桌只能有一个未完成的当前手；同一参赛次数默认只能有一张活动桌，转桌事务负责关闭旧桌并创建新桌。比赛和参赛结束状态须区分，重入不再使用旧参赛 ID。

赛事状态为 `draft / ongoing / completed / abandoned`；首个实际手牌使其从 draft 进入 ongoing。参赛状态为 `active / eliminated / withdrawn / completed`，桌状态为 `active / closed`。转桌只关闭旧桌，不结束参赛；英雄淘汰可结束参赛和当前桌，但赛事是否结束取决于用户是否继续重入或确认最终结束。

## 4. API 合同

### 4.1 通用规则

日期为 ISO 8601 UTC，金额为安全整数或明确的 decimal 字符串。UUID 一律作为字符串，不把物理座位号当 UUID。所有列表采用游标分页，默认 20、最大 100；筛选时间传明确时区或由账户时区解析。

所有私有资源先验证身份，再验证归属与父子关系；不可见对象统一 404，避免泄露他人资源是否存在。普通用户访问管理员路由返回 403。版本冲突返回 409，客户端必须展示服务器最新状态并请求人工选择，不能偷偷覆盖。

建赛事、建参赛、建桌、保存动作、结算、补正、转桌、导入和新建任务均需幂等键；修改现有资源还需 expected_revision。统一幂等实现通过事务内唯一键占位串行化同 key 请求，响应与业务数据一起提交，失败一起回滚。并发相同 key 等待已有事务完成后读取原响应，不能把“先查询不存在”当作无竞争保证。

```json
{
  "error": {
    "code": "revision_conflict",
    "message": "该手牌已在另一设备更新",
    "request_id": "uuid",
    "details": {"expected_revision": 7, "current_revision": 8}
  }
}
```

其他标准错误：401 `authentication_required`、403 `registration_closed/account_disabled/csrf_failed`、409 `idempotency_conflict/stale_analysis_input`、422 `validation_error/invalid_action/estimated_zero_requires_resolution`、429 `queue_full/user_quota_exceeded/rate_limited`、503 `compute_unavailable`。错误详情不得包含堆栈、SQL、密码、Cookie 或其他用户数据。

### 4.2 路由清单

| 方法与路由 | 输入重点 | 成功与副作用 |
|---|---|---|
| GET `/auth/bootstrap` | 无身份也可调用 | 注册是否开放、预会话 CSRF；不返回用户清单 |
| POST `/auth/register` | username、password、display_name | 201，创建 active 用户并设会话 Cookie |
| POST `/auth/login` | username、password | 200，轮换会话，返回公开账户资料与 CSRF |
| GET `/auth/me` | Cookie | 当前账户、角色及配额摘要 |
| POST `/auth/logout` | CSRF | 204，撤销会话 |
| POST `/auth/password` | 原密码、新密码 | 撤销旧会话，要求重新登录 |
| POST `/auth/reset-password` | 一次性令牌、新密码 | 消耗令牌、撤销旧会话 |
| PATCH `/account` | display_name、timezone、expected_revision | 保存个人偏好，不允许 role/status 字段 |
| GET / POST `/tournaments` | 分页筛选 / 可选名称及开始时间 | 列出或创建赛事草稿 |
| GET / PATCH `/tournaments/{id}` | expected_revision、允许修改的赛事属性 | 返回或修订本人赛事 |
| POST `/tournaments/{id}/entries` | 买入费用可空、expected_revision | 首次参赛或重入；已结束赛事需明确恢复 |
| POST `/entries/{id}/tables` | 桌型、盲注、座位默认配置、幂等键 | 建桌并创建第 1 手 |
| GET `/tables/{id}` | 无 | 权威当前状态、当前手和保存版本 |
| POST `/tables/{id}/transfer` | 旧桌版本、已结算边界、新桌配置 | 同一赛事参赛下转桌，不伪造新赛事 |
| PATCH `/hands/{id}/record` | expected_revision、动作/牌面修改 | 服务端校验、创建修订、旧分析过期 |
| POST `/hands/{id}/settlement-preview` | expected_revision | 计算可确定余额，不发布结算 |
| POST `/hands/{id}/settle` | 见下方合同 | 一次事务结算并按需创建下一手 |
| POST `/hands/{id}/correction-preview` | 原版本、修改值、原因 | 返回受影响范围及短期有效 preview_id |
| POST `/hands/{id}/correct` | preview_id、版本、幂等键 | 发布修订、标记受影响记录，不静默重写后续 |
| POST `/tables/{id}/calibrate-balance` | 手间边界、观测值、版本、原因 | 新实测锚点；不计作本手收益 |
| GET `/history/hands` / `/history/hands/{id}` | 日期、赛事、质量、分页 | 本人有效历史、修订和分析引用 |
| POST `/history/hands/{id}/archive` / `/restore` | 版本、原因、幂等键 | 软删除/恢复，活动手需先结束或放弃草稿 |
| GET `/statistics` | 日期范围、时区、赛事可选 | 返回定义明确的指标和覆盖率 |
| POST `/imports/preview` / `/imports/commit` | 旧数据 / 预览 ID、幂等键 | 预检后显式导入本人历史 |
| POST `/exports` / GET `/exports/{id}` | 日期范围、格式 | 导出本人数据；下载地址必须鉴权 |
| POST `/analysis/jobs` | 手牌版本、决策点、质量档、幂等键 | 202，任务 ID、状态、请求质量 |
| GET `/analysis/jobs/{id}` | Cookie | 状态、进度、模块结果、有效样本数 |
| POST `/analysis/jobs/{id}/cancel` | CSRF | 幂等取消，只能取消本人任务 |
| GET / PATCH `/admin/settings` | 管理权限、版本、近期验证 | 注册开关和配额，记录审计 |
| GET `/admin/users` | 分页 | 最少账户资料和负载，不返回密码摘要 |
| PATCH `/admin/users/{id}` | 状态或配额、原因 | 权限检查、审计、必要时撤销会话 |
| POST `/admin/users/{id}/password-reset` | 近期验证、原因 | 一次展示短期重置令牌 |

列表中所有路径均加 `/api/r2` 前缀。未列出的结束赛事、结束参赛、放弃草稿等动作需补入实现 OpenAPI 和验收映射，语义沿用本文，不能通过任意 PATCH 放开生命周期跳转。

### 4.3 结算请求和事务

```json
{
  "expected_hand_revision": 8,
  "expected_table_revision": 14,
  "idempotency_key": "client-generated-uuid",
  "completion_mode": "continue_with_estimates",
  "balances": [
    {"occupant_id": "hero-uuid", "model_chips": 11200, "actual_chips": 11200,
     "provenance": "observed", "basis": "manual", "confirmed_eliminated": false},
    {"occupant_id": "opponent-uuid", "model_chips": 8800, "actual_chips": null,
     "provenance": "estimated", "basis": "carry_forward", "confirmed_eliminated": false}
  ],
  "actual_outcome": {"hero_won_any_pot": null, "source": null},
  "adjustment": null,
  "next_hand": {"start": true, "roster_changes": [], "blind_change": null,
                "position_confirmation": null}
}
```

示例只列两行；实际提交必须包含本手全部参与者，不能省略离桌者。`completion_mode` 为 `require_confirmed` 或 `continue_with_estimates`；前者存在估算应拒绝，后者仍验证所有活跃下一手参与者有正的建模余额。服务器验证来源依赖；客户端不能自称 `derived` 就绕过校验。

事务顺序必须固定：鉴权 -> 查幂等记录 -> 锁定桌和当前手 -> 校验预期版本与参与者 -> 校验余额及差异原因 -> 生成结算修订 -> 应用手间人员事件 -> 验证下一手位置与盲注 -> 创建下一手或结束桌 -> 保存幂等响应 -> 提交。

幂等重复请求若正文哈希相同，返回原响应，不受原 expected_revision 已过期影响；相同 key 但正文不同返回 409。两个并发不同 key 的相同结算只有一个成功，另一请求发现版本冲突。当前动作更新与结算都锁定相同手/桌版本，避免结算后再追加旧动作。

历史关键结算的幂等记录随资源保留，不设置很短 TTL 导致离线重试重复建手。事务失败不能出现“上一手已提交，但下一手半创建”。响应包含新版本、当前手 ID、结算质量、待核对数量和必要的位置提示。

补正 preview_id 绑定 owner、原始版本、目标修改哈希、依赖版本及过期时间。提交时依赖有变化则 409 重新预览；不能拿旧预览覆盖新记录。

## 5. 覆盖旧接口，防止绕过

必须盘点所有已注册路由，包括 `/api/equity`、旧 `/api/hand/*`、`/api/hand/v2/*`、`/api/table/prepare`、`/api/stats/*` 和静态调试页面调用的接口。公开部署不能留下匿名重计算入口。

旧高成本同步分析接口在云端模式下统一返回 410 及迁移错误码，前端切换新任务 API；不保留“通过旧路径即可无配额同步计算”的兼容方式。必要的纯视图、准备下一手逻辑移入已鉴权的领域服务或鉴权路由，设置输入大小上限。

新分析任务从服务端已授权的手牌修订构建输入；客户端只能指明决策点及允许的质量档。假设分析使用单独标记、验证过的分支快照，不能靠任意提交 hand_id 加不相符的牌局正文冒充真实历史分析。

旧画像接口云端默认关闭；以后恢复时必须按账户隔离，不能继承匿名共享池做个人数据。原 `POKER_ADMIN_TOKEN` 不能继续承担公开管理员入口。确需保留离线旧模式时使用单独显式部署配置，绑定本机且不能与公开账户模式混用；默认交付只保证账户模式。

## 6. 浏览器迁移与离线冲突

浏览器现存 `paishi_tournament_session_v1` 和匿名 UID 不是可信所有权凭证。首次登录检测旧记录，展示数量、日期范围、含哪些摘要/当前草稿，让当前用户选择导入；不得自动认领共享设备上的全部内容。

导入先在服务端验证 schema、金额、唯一牌、动作、大小及账户归属，再给出预览。无明确赛事归属的旧手牌归“旧版未分类记录”，不要凭一份本地缓存虚构完整赛事；在用户归类之前不加入赛事场次统计。

旧摘要通常没有完整动作和英雄牌，标记 `summary_only`；保留能证明的信息，其余为 null，不生成随机牌或假动作。当前手如有完整快照可导入为待恢复草稿。旧 `recordQuality` 不是统一可信凭证，应逐条映射，无法证明的结果排除相应统计。

导入唯一键包含 owner、来源批次校验和与来源手 ID。重复导入同一文件不重复计数；内容冲突时展示差异，不按本地修改时间直接覆盖。大文件分批事务，返回成功/失败条目；重试可继续。

登录后的本地草稿、待同步队列和缓存以服务端账户 ID 分区，可用 IndexedDB 保存较大牌谱；Cookie 之外不在 localStorage 保存会话令牌。退出和切换账户清除内存状态、显示中的私人内容及计算轮询；未同步草稿保留为原 owner 的隔离草稿并提示，不能混入新账户。

网络响应同时校验当前 owner、手牌 ID、revision、state_hash 和请求序号。A 退出后 B 登录，A 的迟到请求不得显示在 B 页面。服务器 401 时停写、提示登录，不能转而匿名写入。

本期跨设备是恢复和版本冲突处理，不是实时协同编辑。离线动作恢复后版本相同可补传；不同则展示两份差异，允许保留副本或人工重录。不能把服务器和本地动作数组简单拼接。

## 7. 部署、备份与迁移验收

- 提供锁定依赖、环境变量示例、数据库迁移、管理员创建、启动/停止和健康检查命令；示例不含真实密钥。
- 生产 API 文档按部署需要限制访问；错误页禁用调试堆栈。数据库不直接暴露公网，应用使用专用最小权限账户。
- 日志记录 request_id、任务 ID、时间、状态和性能数据；移除密码、Cookie、CSRF、重置令牌、连接串，默认不记录完整底牌和私人动作正文。
- 每日数据库备份，保留最近 7 日和最近 4 周周备份；至少一份位于该服务器之外且访问受控。512 GB 本机磁盘不能替代异机备份。
- 上线前在空数据库恢复一次备份，验证账户、历史、当前桌和统计一致；目标 RPO 24 小时、RTO 4 小时，实际演练结果写入交付报告，达不到需调整方案。
- 数据库升级先备份，迁移在单独步骤执行，不能由多个 Web worker 启动时争抢执行；有破坏性变更时采用先加字段、迁移、切换、后移除的方式。
- 回滚保留新产生的真实牌谱，不能简单恢复昨天数据库导致今日记录丢失。开发者必须给出程序回滚兼容范围和数据恢复步骤。
- 自动化测试使用专用数据库和专用账户；不得向真实账户注入测试手牌或清空真实画像文件。

## 8. 官方依据

安全实现应对照 [OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、[CSRF 防护](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)及[逐请求授权](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)。这些资料支持实现机制，不替代本项目针对性的越权测试。

事务锁与队列领取参考 [PostgreSQL 显式锁](https://www.postgresql.org/docs/current/explicit-locking.html)和 [SELECT / SKIP LOCKED](https://www.postgresql.org/docs/current/sql-select.html)。`SKIP LOCKED` 可用于任务竞争领取，不能拿来读取要求完整一致的赛事统计。
