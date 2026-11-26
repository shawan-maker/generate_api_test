# EIS 云主机批九源头修复方案

> 定位：针对 r11 真机复验 + 用户界面复核发现的全部问题（P-1 删除先行连锁 / P-2 恢复状态机断链 / P-4 F6 假成功 / P-5 标签管理不可达 / hover 归因缺口 / P-3 el-select 值同步 / 配额豁免），基于日志原文与代码精读的源头级根因定案与修复设计。
>
> 基线：r11 验收报告（commit 4e78798）、`workspace/eis/output/eis_s1_vm_r11.err.log`、`workspace/eis/kb/module_discovered/云主机-云主机_ui.json`（r11 产物，29 操作 / 14 转移边 / 6 态矩阵）。
>
> 修复原则（与项目宪法一致）：
> - **源头修复、不打补丁**——不新增兜底分支，改机制本身；
> - 每项修复**反例论证不引入新问题**（estack / 用户管理 / 黑白名单等既有模块零回归为硬约束）；
> - 每项修复**配单测**，落地批次收尾跑全量回归；
> - 本文档**不修改任何代码**，实施批次按 §八 顺序执行。
>
> 本文档同时承载 **r11 报告 P-2 断点②③的勘误**（§一.2），勘误结论以本文档为准。

---

## 〇 问题总表与批次归属

| 编号 | 问题 | r11 现象 | 修复批次 |
|---|---|---|---|
| P-1 | 级联批删除先行 → marker 行真删 → 6 操作连锁 row_not_found | 判定 21/8 中 6 败源于此 | 批九-A |
| P-2 | 恢复 状态机窗口断链（含 Stopping 过渡态误判、伪转移边） | 恢复 终身未执行 | 批九-A |
| P-4 | F6 导航型判定过宽 → 规格变更/系统盘扩容 假成功 | 判定成功 21 中 2 项假成功（业务真实 19） | 批九-B |
| P-5 | 标签管理 矩阵全态零观测 → R1 无需求态可驱动 → 永久 pending | 终态 click_failed 挂账 | 批九-A（诚实短路）+ r12 观察项 |
| SCAN | hover 归因链断裂 + 黄金窗残留弹窗无防御 | 退出运维模式 收集晚 3 分钟（vue_show 窗） | 批九-B |
| P-3 | el-select 点选后 Vue model 值同步失败 → 误判「填充失败」→ 盲提交 | r10 定案，r11 未复验（死于 P-1 连锁） | 批九-C |
| QUOTA | 配额类终态拒绝未入状态性豁免表 | r10 实证「上限为3」 | 批九-C |
| S2 | r11 存量 ui.json 的 execution_order 含删除连锁序，回放将复现 | Stage 2 风险 | 重跑 Stage 1（§二.5） |

---

## 一 P-2 恢复断链：状态机窗口调度三缺口（含 r11 报告勘误）

### 1.1 r11 实证时间线（日志原文）

```
14:12:00 [STATE-RECONCILE] 启动: pending=11 项（触发: 进入运维模式）
14:12:07 [STATE-RECONCILE] R1 业务执行: 开机（当前状态: Stopped）      → ✓，post-settle Running
14:13:00 [STATE-RECONCILE] R1 业务执行: 暂停（当前状态: Running）      → ✓，post-settle 读到 Paused，
                                                                        记边 暂停 Running→Paused（该边 r11 首采）
14:13:40 [STATE-RECONCILE] R1 业务执行: 硬重启（当前状态: Running）    → ✓，但此时行实际处于 Paused
14:14:14 本轮剩余 ready 项（重启/重置密码/执行命令/热迁移/更新密钥对）预检全跳过（快照已刷新为 Stopping 期）
14:14:21 [STATE-RECONCILE] 无可用恢复边，结束                        → reconcile 整体退出
14:16:42 / 14:16:59 两轮 reconcile: pending=['恢复','标签管理']        → 因 P-1 删除（14:15:50）行已灭，全军 row_not_found
```

三点关键判读：

- **14:13:40 的「当前状态: Running」是轮起始 settle 的 status 变量**（`crud_executor.py` L1541-1542 打印的是 round 级 `status`，L1489 轮首赋值），**不是**暂停后的实时读数。暂停 post-settle（L1563-1567）实际读到 Paused 并记录了 Running→Paused 边——种子 dump 中该边标注「r11 14:13:00 后首次采集」自证。
- 硬重启 在**新旧两个矩阵行都可用**（种子矩阵 Running 行与 Paused 行均含 硬重启），预检快照虽已刷新为 Paused（L1569 `_r1_snap = post`），预检照常通过 → **同一轮的 stale ready 队列把 Paused 窗口摧毁**。
- 14:14:21 内嵌 round 3 的 settle 读到 Stopping（EIS Stopping→Running 过渡 >10s，「值两次相同」双条件失效）→ 恢复 的需求态 Paused 无 Stopping 出边 → reconcile **整体终结**——而行约 15s 后回 Running（该处 暂停 Running→Paused + 恢复 Paused→Running 两条边均可再驱动一轮完整恢复）。

### 1.2 r11 报告 P-2 勘误（断点②③修正）

| 原断点 | 勘误结论 | 证据 |
|---|---|---|
| ②「settle 回扫读 Running 而非 Paused，永久错过窗口」 | **误读**。post-settle 读到了 Paused 且记边成功；真实错过点是 **轮内 stale ready 继续消费硬重启毁窗 + Stopping 稳态误判后无出边即整体终结** | L1541-1542 打印轮首变量；断点①自己的「边在 14:13:00 首采」即 post-settle 读 Paused 的直接证明 |
| ③「EIS 暂停不持久（r11 13 秒后已是 Running）」 | **推翻**。r9 实证 16:42:48 暂停 → 16:48:54 仍 Paused（6+ 分钟持久）；r11 的回 Running 由 14:13:40 硬重启执行解释，无需「自动回醒」假设 | r9 日志时间线（更新密钥对 的 R1 恢复转移以 Paused 为 from 态成立） |
| ①「种子冷启动滞后无入边」 | **成立但非决定性**。14:11:13 轮（13 边种子）确实因缺 Running→Paused 边而「无可用恢复边」结束；但 14:13:00 后边已具备，最终错过在调度时机（Gap A/B），不在种子 | 种子 dump L4/L8 两边齐备；14:11:13 与 14:12:00 两轮行为对比 |

### 1.3 根因定案（三个结构性缺口）

**Gap A 轮内 stale ready（核心缺口）**：ready 列表在轮首由 settle 快照计算（L1501-1503），轮内某项成功翻转状态后（暂停 → Paused），循环继续按旧快照消费；「本次转移解锁的 pending」（恢复@Paused）因轮首不可用而不在队列中，等下一内嵌轮重新评估时窗口已被同轮后续项（硬重启，新旧态均可用）摧毁。状态机的转移边、矩阵、R1 驱动逻辑全部正确——缺的是**转移成果的轮内定向消费**。

**Gap B 过渡态稳态误判 → 无出边整体终结**：`_wait_row_states_stable`（L1343-1400）双条件「值两次相同」对 >10s 的过渡态（Stopping）失效（这是「不预设过渡态特征」设计的固有代价，本身不是错）；但 R1 恢复转移评估要求 `from == 当前状态` 的边（L1631-1638），过渡态无出边 → L1642-1644 直接 break——reconcile 整体退出，不等待过渡态消化。14:11:13 轮（7 秒即死）与 14:12:00 轮（终结于 14:14:21）两次命中同一堵墙。

**Gap C 伪转移边进入 R1 候选**：转移边按「操作执行前后 status 恰好不同」记录（`_record_state_transition` 调用点 L1567 / L1670），无操作语义门。种子中的 `网络配置: Stopping→Stopped` 即观察巧合产物（导航型操作执行时恰逢状态漂移）；r11 两次被 R1 选作恢复转移执行（14:00:42 驱动 开机、14:03:07 驱动 冷迁移），各耗 ~30s，均未解锁任何 pending（post-settle 仍 Stopping → 无可用恢复边结束）。

### 1.4 修复设计

**F1 轮内定向消费（Gap A 主修）**

ready 循环内（L1504-1603），成功项 post-settle 记边后（L1563-1569），若 `post_status != status`（状态翻转）：

1. 以 post 快照重算调度序：`新序 = [post_status 矩阵解锁且未消费的 pending] + [旧 ready 中按 post 态仍可执行者]`；
2. **解锁项绝对优先**（窗口优先序）——它们只在当前状态活着的窗口内可执行，而旧 ready 项在新旧态大多同样可执行（硬重启型），不存在抢占损失；
3. 旧 ready 中在 post 态不可用者自然出列（交下一内嵌轮 settle 重新评估，pending 不丢）；
4. R3 业务身份每轮一次、attempted 预算过滤、F2-预检、消息捕获器重装、批六修复3c 清扫——全部既有机制原样沿用；
5. post-settle 采用**语义目标态等待**：优先等待 `status == post 预期目标态` 出现（有界，命中即返回；超时回落双条件结果）——对「暂停 → Paused」类慢翻转不再依赖「两次相同」碰运气。

日志签名：`[WINDOW-UNLOCKED] 状态 {old}→{new} 解锁 pending: [...]`。

**F2 过渡态再稳态（Gap B 主修）**

R1 恢复转移评估走到「无可用恢复边」分支（L1642-1644）时，break 前追加数据驱动的过渡态判定：

- 条件：① pending 中仍有未耗尽非 destroy 项；② 当前 status **不在 `state_button_matrix` 已观测态集合中**（matrix keys——数据驱动判据，不预设「含 ing/中」等过渡态文本特征，与既有设计原则一致：从未在任何 settle 点作为稳态被观测过的 status 即过渡态）；
- 满足 → `[TRANSIENT-WAIT]` 再 settle 一轮后重新评估（上限 2 次额外 settle，每次受既有 180s 封顶）；仍无出边再按现状 break。

**F3 转移边质量门（Gap C 主修）**

- **记录时**：`_record_state_transition` 两个调用点（L1567 / L1670）在记边前检查本次执行 result：`interaction_mode == "page-nav"` 且 `nav_info.form_fields` 为空 → 导航型操作不记边（`[EDGE-SKIP-NAV]`）；
- **消费时**（对存量伪边生效）：R1 边候选循环（L1631-1638）追加同一判据——`validated[edge_action]` 为 page-nav 无表单 → 跳过该边。两道门同判据单源。

### 1.5 反例论证（不引入新问题）

- **无状态模块（estack 等降级环境）**：status 恒 None → F1 翻转分支不触发（post_status None）、F2 matrix 判据空、F3 记边早已静默跳过（status None 不记边）——三修复零行为变化，零降级等价性保持。
- **无 pending 模块**：reconcile 在 L1473-1474 直接返回，零变化。
- **F1 死循环风险**：新序每次执行要么消费 business_executed（R3 每身份一次）要么被 attempted 过滤——单调递减必然终止；解锁项失败走 attempted 入册，同批不重试；12 轮挂死保险原样兜底。
- **F2 拖长风险**：仅在「无出边 + pending 未清 + status 未入矩阵」三条件同时成立的角落生效，上限 2 次额外 settle（Stopping 场景典型 ~10-20s）；对 r11 的 14:11:13 轮（7s 即死）与 14:12:00 轮（差 15s 即可再驱动一轮 暂停→恢复 完整恢复）收益直接。
- **F3 误杀真边风险**：种子 14 条真实边（开机/关机/暂停/恢复/重启/硬重启/硬关机/冷迁移/创建快照/创建镜像/退出运维模式）全部来自行级确认类操作，无一 interaction_mode=page-nav——判据只命中观察巧合边，真边零损失。
- **F4 语义目标态等待**：仅替换成功后的 post-settle 口径（等待目标态出现 vs 等两次相同），失败/超时路径回落现状结果，不影响 settle 的通用调用方（批七 F7 稳态等待等沿用原函数不动）。

### 1.6 单测方案

- **F1**：FakePage——pending={恢复, 硬重启}，轮首 status=Running、ready=[暂停, 硬重启]，暂停 执行成功后 post=Paused、matrix[Paused] 含 恢复 → 断言下一个执行的是 恢复（窗口优先）而非 硬重启；恢复 成功后 硬重启 按新态可执行性继续；断言 `[WINDOW-UNLOCKED]` 日志。
- **F2**：status 序列 Stopping,Stopping →（TRANSIENT-WAIT）→ Running,Running：断言不提前 break、重评后找到 暂停 边驱动恢复转移；2 次上限耗尽后 break 与现状一致。
- **F3**：result 为 page-nav 无 form_fields → 断言不记边；dialog 确认类 result → 记边；R1 候选过滤对存量伪边（网络配置 Stopping→Stopped）生效。
- **回归**：批六/批七/批八既有套件 + 全量 pytest exit 0。

### 1.7 验收口径

- r12：恢复 成功，日志含 `[WINDOW-UNLOCKED] ... ['恢复']` 或 `R1 业务执行: 恢复`；硬重启 若执行须在 恢复 之后；
- 无伪边驱动的恢复转移（网络配置 不再作为转移手段出现）；
- estack / 用户管理产物零变化（build_playbook 幂等重放核对）。

---

## 二 P-1 删除先行：destroy 统一收尾路径

### 2.1 实证时间线（日志原文）

```
14:15:07 [STATE-CHANGE] 发现 7 个新启用按钮: ['重启','热迁移','删除','重置密码','更新密钥对','修改Hostname','执行命令']
         （退出运维模式 ✓ 后置扫描触发；删除在扫描发现序第 3 位）
14:15:14 执行新启用按钮: 重启            → ✓（27s，含递归重扫）
14:15:43 [STATE-CHANGE] 发现 1 个新启用按钮: ['删除']   ← 重启翻转后递归批再发现
14:15:50 执行新启用按钮: 删除            → ✓（marker 行 test672628 真删；此时主循环未走完、pending 含 恢复/标签管理）
14:16:23-14:16:42 同批队列继续消费: 热迁移/重置密码/更新密钥对/修改Hostname/执行命令 全部 row_not_found（预检即失败）
14:16:26 [STATE-CHANGE] 跳过已执行按钮: 删除（批队列后续位点）
14:16:48 查看详情（名称链接）            → row_not_found（主循环仍在删除位之后到达）
14:16:59 跳过 删除: 已在级联/状态重试中成功，不重复执行  ← 主循环到达删除位（末位）时行早已没了
```

### 2.2 代码定位与根因

- **主循环有序**：`_find_create_delete_actions`（button_detector L839-880）+ 装配「删除最后」（crud_executor L1894-1897 过滤 / L1907-1908 extend）+ 增量入册 delete 追加末位（L2548-2550）+ marker 重收集同口径（L1747-1749）——主循环路径的删除严格末位 ✓。
- **级联批无序**：`_check_and_execute_newly_enabled` L1234 `for text in newly_enabled` 按扫描发现序执行，**无角色判定、无排序、无门禁**；未入册文本按 dropdown 兜底构造 btn_info（L1250-1256）；每项成功后递归再扫（L1309-1317）——r11 的删除正是从该递归进入（14:15:43）。
- **R1 ready 无门禁**：L1501-1503 pending 启用即执行。
- **单源已备而未接线**：`is_destroy_text` / `DESTROY_KEYWORDS`（shared/button_clicker L375-385，含「勿在别处复制」注释）与 `_infer_action_role` 的关键词兜底（button_detector L818-829，批六修复1）都已存在——但批执行路径 L1234 循环完全不消费角色。
- **根因一句话**：「删除排最后」不变量只在主循环装配层成立，级联批 / R1 两个增益层旁路了它；删除一旦执行即终结 marker 行生命周期，其后一切 marker 定位操作必败（6 连锁 row_not_found + P-2 恢复的最后两轮 reconcile 机会一并被掐灭——P-1 与 P-2 在 r11 相互咬合）。

### 2.3 修复设计（destroy 统一收尾路径）

**G1 收尾化（主修）**——「删除最后」由结构保证而非排序技巧：

1. ordered_actions 装配**移除删除项**：L1907-1908 不再 extend `_delete_actions`；增量入册（L2548-2550）与 marker 重收集（L1747-1749）遇 `new_role == "delete"` 改入 `deferred_destroy` 清单，不入主序；
2. 主循环遍历所有非删除项（含每项后的级联/reconcile 增益层）→ **主循环收尾 reconcile（L2574-2595）→ clear_table_search（L2596-2602）之后、返回前**，插入「收尾删除」阶段：对 deferred_destroy 逐个以业务身份经既有 `_do_delete` 链（L2275-2287 同构调用：`_error_driven_retry` + 消息捕获 + 结果落账 + `_record_execution(action, "main", ...)`）执行；
3. 收尾时点「其余操作均已终态」恒成立——不需要复杂的门禁判定，三条执行路径收敛为单一路径，排序问题结构性消失。

**G2 增益层 defer**：级联批循环（L1234 项前）与 R1 ready 过滤（L1501）遇 `is_destroy_text(text)` → 不执行、不入 executed_actions、记 `[DESTROY-DEFER]` 日志（幂等去重）；递归批同守卫。

**G3 不新增错误类型**：删除收尾后不再有 marker 定位操作，r11 型连锁结构性消失——无需 marker_gone 新语义；共享环境他人删行（我方未删）仍按现状 row_not_found 如实记录，两类场景天然可区分。

execution_order：删除在收尾记录 → 必然末位；builder 的 Cascade-Order（3f3b29d）对 main 身份的删除无 triggered_by 重排副作用，无需改动。

### 2.4 反例论证

- **既有模块零回归**：estack / 用户管理 / 黑白名单的删除原就在主循环末位执行，收尾化后仅后移到收尾 reconcile 之后——执行链与调用方式同构，时机后移无语义变化；无删除操作的模块 deferred 清单空 → 零变化。
- **覆盖率不丢**：收尾删除复用主循环删除位的完整执行链（探测/确认/二次确认层/消息验证），仅调用时机后移。
- **不会永久丢失**：deferred 清单在收尾阶段无条件消费——此时不再有任何未终态的非删除操作可被破坏；唯一失败路径是 marker 行已不存在（环境变）→ row_not_found 如实记录。
- **「删除先行解锁操作」假想场景不存在**：删除是行终结，不解锁任何 marker 行操作；种子 14 条边无一以删除操作为 action（seed dump 可证）。
- **批量删除（toolbar「批量删除」类）**：`_find_create_delete_actions` 关键词命中入 deferred → 收尾执行，与现状主循环末位等价。

### 2.5 Stage 2 存量产物处置

r11 ui.json 的 execution_order 中删除(cascade) 位于 5 个 cascade 项之前——直接生成 playbook 回放将复现连锁。处置：**批九-A 落地后重跑 Stage 1 生成新 ui.json，不手改存量产物**；r11 产物仅作验收基线留存。builder 无需改动（修复后写入序自然正确）。

### 2.6 单测方案

- 批循环 defer：newly_enabled=['重启','删除'] → 断言执行 重启、`[DESTROY-DEFER]` 删除、executed_actions 无删除、递归批同守卫；
- R1 ready 过滤 destroy；
- 收尾阶段：deferred=['删除'] → 在 final reconcile 与 clear_table_search 之后执行、`_record_execution` phase=main、execution_order 末位；
- ordered_actions 装配无删除项断言；
- 回归：用户管理/黑白名单既有 builder 幂等测试 + 全量 pytest exit 0。

### 2.7 验收口径

- r12：删除 收尾执行（execution_order 末位、收尾签名日志）；row_not_found 连锁 0；恢复/标签管理 的 reconcile 消费发生在删除之前；
- Stage 2 回放：删除为最后一个回放步骤、其前步骤零 row_not_found。

---

## 三 P-4 F6 导航型判定收紧（规格变更/系统盘扩容假成功）

### 3.1 证据链

- r11：14:10:26 级联批发现 ['规格变更','系统盘扩容']，14:10:32 / 14:10:52 批内执行，F6 判定成功（报告 [F6] 日志 14:10:47 / 14:11:07）——**0 填写 0 提交即判导航成功**；用户界面复核证实两项业务零变更（判定成功 21 中 2 项假成功，业务真实 19）。
- r9 对照（真完成）：规格变更 走 EDIT-FLOW——探索 changeSpec 页、页内「确定」提交 19s、捕获真成功消息。
- 网络配置（F6 设计的正主）：r9 无分流时 4 分钟 tab 扫描 no_submit_button；r10/r11 分流后 ~5s 判成功——**收紧不得回吐此收益**。

### 3.2 代码定位

F6 分支（crud_executor L5585-5642）：判据 = URL 跳转 + `marker in url_after` + 无弹窗（`_f6_has_dialog`）→ 判导航成功直接返回。**双条件不含「目标页是否存在待编辑表单」约束**——changeSpec / expandVmVolume 是编辑表单页（URL 含 marker、无弹窗），被误判为导航型。

### 3.3 修复设计

在 `_f6_has_dialog` 检查通过后、返回成功前，追加 `_f6_target_has_edit_form` 探测（同窗单次 evaluate）：

- **判据 A**：`.el-form` 内**可见、非 disabled、非 readonly** 的 input/textarea 计数 > 0（可编辑性，不是 .el-form 存在性）；
- **判据 B**：页面存在可见确定类 footer 按钮——新增 const.py 单源词表 `PAGE_NAV_EDIT_SUBMIT_KEYWORDS = ('确定', '保存', '提交', '应用', '更新')`（先例：DESTROY_KEYWORDS / TOGGLE_PAIR_KEYWORDS / CLONE_ACTION_KEYWORDS / DIALOG_TITLE_SUBJECT_WORDS——「语义词表单源 + 不写死具体操作名」既有惯例）；
- **A ∧ B** → 编辑型目标页：不判导航成功，落回 `_explore_and_operate_in_new_page`（L5644 既有探索链，r9 已验证可完成）；否则维持 F6 快路径。

**反例防护（关键设计点）**：判据用**可编辑性**而非 `.el-form` 存在性——只读详情页（vmDetail 类）常用 el-form 布局展示描述项，但其 input 全 disabled/readonly → A 不命中 → 网络配置 保持 ~5s F6 快路径（r10 4 分钟回吐风险消除）；详情页 tab 内即便有可见搜索框，无确定类 footer → B 不命中 → 仍 F6。双条件互锁防两类假阳性。原边界（有弹窗的跳转、marker 不在目标 URL 的跳转）不受影响。

### 3.4 单测方案

FakePage 四态：只读详情页（el-form 存在但 input 全 disabled、无 footer 确定）→ F6；编辑页（可编辑 input + footer 确定）→ EDIT-FLOW；无 form 纯导航页 → F6；有弹窗 → 原路径不变。

### 3.5 验收口径

- r12：规格变更/系统盘扩容 有页内提交与成功消息（业务真实成功 = 判定成功，用户界面复核抽验）；网络配置 仍 ~5s；
- ui.json 两项 `nav_info.form_fields` 非空、`submit_result.button_text` 非空；Stage 2 步骤含 fill + submit。

---

## 四 P-5 标签管理：矩阵全态零观测的诚实短路

### 4.1 定性

- 标签管理 为行级「更多」下拉项（location=dropdown），主循环 14:02:01 尝试 click_failed（行 Stopped 期菜单项不可见）→ pending。
- **种子矩阵 6 态（Pending/Running/Stopping/Stopped/Paused/Starting）的 enabled 清单全部不含 标签管理**（seed dump 实证）→ R1 的 `needed` 计算（L1621-1624：需求态 = 矩阵中该操作可用的状态）恒为空 → 永久 pending 至收尾，终态沿用 click_failed 判失败。
- r11 报告「可见窗口随行状态动态出现消失」成立，但矩阵全态零观测说明其窗口**比 settle 采样更窄**（或依赖未观测维度，如运维模式正交维）。

### 4.2 修复设计

1. **主修依赖 P-2**：窗口定向消费 + 过渡态再稳态落地后，行状态生命周期全程被 settle 覆盖采样——若其窗口落在任何已观测态内即可被驱动执行；
2. **诚实短路**：收尾时对「矩阵全态零观测的 pending 项」给 `skipped_state_window` 终态（detection_status=skipped + wait_reason），不沿用 click_failed 判失败——**不猜测其窗口、不假装可恢复、不做假成功**；
3. **r12 观察项**：若仍零观测，需真机探针确认其可见性维度（可能依赖运维模式正交态）——列为观察项，不盲改机制。

### 4.3 反例与验收

- 反例：skipped_state_window 仅替换收尾未决项的呈现口径，不改变 pending 回收机制；estack 类无状态模块 pending 项本就少，零影响。
- 验收：r12 标签管理 **成功**（理想）或 **skipped_state_window**（诚实）——两者皆达标；继续 click_failed 挂账 = 未达标。

---

## 五 扫描可观测性与 hover 归因补强

### 5.1 r11 实证

- 全程仅 **4 次**走到展开步骤的 INCREMENTAL-SCAN：14:01:15 vue_show / 14:03:40 vue_show / **14:11:22 dispatchEvent** / 14:14:24 vue_show。
- 退出运维模式 实际收集窗 = 14:14:24（vue_show）；黄金翻转窗（14:11:24 进入运维模式成功后）内的扫描用 dispatchEvent（`__vue__` 不可用窗，批八定论的 F6 导航往返后 ~15s 窗口）——**批八 hover 先行在该窗是否生效不可证**：hover 成败日志一条是 info（L219 ok）、一条是 debug（L221 失败被吞），归因链断裂。
- 批八 P2 判定维持「结果达成、归因部分」；本项补强目标是让 r12 的归因**可证**。

### 5.2 修复设计

1. hover 失败行升 info 并带 reason（`dropdown_real_hover=fail: ...`），与成功行对称——归因链闭合；
2. hover 前单次 evaluate 检测可见残留弹窗（复用 `_f6_has_dialog` 同选择器集），有则 `[SCAN-SWEEP] 检测到 N 个残留弹窗` → 调既有 `_dismiss_all_overlays`（幂等单源 helper，批六修复3c 已在批/reconcile 路径使用）→ 重试 hover 一次；
3. `dropdown_expand` 已含 method 字段（L252）保持不变。

与批六 A2（未实施）的边界：A2 修「操作入口的弹窗归属校验」，本项修「扫描入口的防御性清扫」——同用 `_dismiss_all_overlays` helper，不新增第二套清扫逻辑；A2 落地后本项清扫自然收窄。

### 5.3 反例与验收

- 无残留弹窗时仅 +1 次 evaluate（~10ms）；清扫失败不阻断扫描（回落既有 JS 链兜底）。
- 单测：FakePage 有可见 dialog → 扫描先 [SCAN-SWEEP] 再 hover；hover 失败日志为 info 级。
- 验收：r12 黄金翻转窗扫描的 hover 成败可从日志直接归因（不再依赖推断）。

---

## 六 P-3 el-select 值同步（r10 定案承接）

### 6.1 r10 定案回顾

更新密钥对：el-select「密钥对」4 步点选全成功 → Vue model 值同步校验失败判「填充失败」→ 无可填字段直接提交 →「更新」命中否定前缀「放弃更新」被 F4 正确拦截 → footer primary「更新并重启云主机」因 model 未更新保持 disabled → no_confirm_button 终败。**判定质量提升**（r9 的成功是误点工具栏按钮的假成功），暴露的真缺口是点选后 Vue model 同步。r11 未复验（更新密钥对 死于 P-1 删除连锁，el-select 链未被触达）。

### 6.2 修复设计（r10 方向落地）

MultiStepExecutor 选项点选成功后：

1. **同步探测**：读该 el-select 展示 input 的 value，不含所选 option 文本 → dispatchEvent 补发原生 change + input 事件（触发 el-select 组件自身的 onChange 同步链）→ 复读验证；
2. **仍不同步 → 该字段判 fill_failed 且禁止走「无可填字段直接提交」**——含失败字段的表单不提交，操作以 form_validation 终败（诚实暴露，不盲提交污染环境）。

### 6.3 反例与验收

- 反例：单选 el-select 展示文本与 option 文本一致（EIS 密钥对名），判据可靠；多选/tags 形态不在此路径（既有分支不受影响）。
- 单测：FakePage model 不随 click 更新 → 断言 dispatchEvent 被调用且值同步；完全不更新 → 断言无提交、error_type=form_validation。
- 验收：r12 更新密钥对 成功（model 同步后 primary 可点）或 form_validation 诚实终败——两者皆达标；no_confirm_button 复现 = 未达标。

---

## 七 配额类终态拒绝豁免（防御）

- 现状：`STATE_PENDING_ERROR_TEXTS = ('qga 未就绪', '克隆中', '请等待', '未就绪', '正在')`（const.py L115）；语义：错误文本命中 → 不入 attempted 终态记忆，保留状态机恢复窗口（批七 F3-2）。
- 承接 r10 待修清单③：配额类文本（实证「上限为3」）扩表 `('上限为', '配额不足', '超出配额', 'quota')`——语义：配额饱和是环境态而非操作缺陷，不烧终态预算、不判操作质量问题；运行中配额若释放（共享环境）状态机仍可恢复。
- **权衡如实记录**：豁免后该项在 reconcile 轮内保持可重试（快败 ~2s/次，受 12 轮上限与 R2 边耗尽约束）——重试成本远小于 r10「假失败重试烧满配额」的污染；该污染的根因（守卫 B 假失败）已由批八轮询修复闭环，本条为尾部密封。
- 反例：正常业务错误文本不含这些词条，误命中概率极低（词条足够特异）。
- 单测：error_text 含「上限为3」→ 断言不入 attempted_actions。

---

## 八 实施顺序与批次划分

| 批次 | 内容 | 依赖关系 |
|---|---|---|
| 批九-A | P-2（F1/F2/F3）+ P-1（G1/G2）+ P-5 诚实短路 | **同批落地**：收尾删除需要 P-2 先消化 pending 才有意义；P-2 的末轮恢复机会须删除收尾化后才不被掐灭（r11 两问题相互咬合，单独修任何一个都会留下另一缺口） |
| 批九-B | P-4 F6 收紧（含 const 词表）+ §五 扫描可观测性 | 独立 |
| 批九-C | P-3 el-select + §七 配额扩表 | 独立 |

- 每批：单测 → 全量 pytest exit 0 → 独立提交（commit -F tmp 文件惯例）→ 既有模块（estack-ecso-cz1 用户管理/黑白名单）build_playbook 幂等重放零变化核验。
- 批九全部落地后：r12 真机 Stage 1 复验（run_r11.py 同型后台脚本 + 对账脚本）→ r12 验收报告 → 全链 Stage 2-5（用**新 ui.json**，存量 r11 产物不手改，见 §二.5）。

---

## 九 r12 验收口径总表

| 口径 | r11 基线 | r12 目标 |
|---|---|---|
| 判定成功 / 业务真实成功 | 21 / 19（2 项 F6 假成功） | ≥21 且 **两者相等**（用户界面复核抽验：规格变更/系统盘扩容/恢复/标签管理/删除） |
| row_not_found 连锁 | 6 项（P-1 连锁） | **0**（删除收尾化） |
| 恢复 | fail（click_failed，终身未执行） | 成功（[WINDOW-UNLOCKED] 签名） |
| 删除 | 级联批中间执行（14:15:50） | 收尾执行（execution_order 末位、reconcile 之后） |
| 标签管理 | fail（click_failed 挂账） | 成功 或 skipped_state_window（诚实） |
| 网络配置 | F6 ~5s | **保持 ~5s**（收紧不回吐） |
| 运行时长 | 20.6 分钟 | ≤25 分钟（F6 收紧使规格变更/系统盘扩容 各 +20s 级；F2 过渡态典型 +10-20s；删除收尾无额外成本） |
| dialog_still_open | 0（批八成果） | 0（保持） |
| 伪边驱动恢复转移 | 网络配置 两次 | 0（[EDGE-SKIP-NAV] 生效） |
| Stage 2 回放 | —（r11 产物有毒序，不复用） | 新 ui.json 全操作意外失败 0，删除为末步 |
| 全量测试 | exit 0（683+ 用例） | exit 0 |

---

## 附：本方案取证产物索引（scratch，不入库）

- `tmp/p9_probe1.txt`：r11 日志关键签名行（验证操作/R1/STATE-CHANGE/reconcile 全序列）
- `tmp/seed_dump.txt`：14 转移边 + 6 态矩阵全量
- `tmp/r11_full_table.txt`：29 操作全表 + r10/r11 逐操作对比 + reconcile 轮汇总
- 关联文档：`docs/debug/eis云主机r10验收报告.md`（P3 定案）、`docs/debug/eis云主机r11验收报告.md`（P-1/P-2/P-4/P-5 定案底账，P-2 断点②③以本文档 §一.2 勘误为准）
