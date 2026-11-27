# EIS 云主机批十源头修复方案

> 定案基础：r12 真机验收（docs/debug/eis云主机r12验收报告.md）+ 本轮系统性 r11/r12 对照取证。
> 约束：只做源头修复，不打 fallback 补丁；不确定处加探针；不改既有验收口径的语义。
> 核心命题：r12 终局「退出运维模式未执行 → 行锁运维态 → 12 项 pending + 删除收尾全灭」
> 不是单一 bug，而是**四重独立缺口叠加**——r11 靠时序运气全部绕过，r12 全部踩中。

## 〇 问题总表与批次归属

| 编号 | 问题（r12 报告编号） | 源头根因定案 | 修复项 | 批次 |
|------|----------------------|--------------|--------|------|
| 缺口① | P1 运维态锁死（漏收「退出运维模式」） | STATE-CHANGE diff 条件机制性排除**新出现菜单项**（`text in button_states` 语义盲区） | F1 新菜单项检测语义 | A |
| 缺口② | P1 同上 | 运维态黄金收集窗（8.7 分钟菜单可展开）内 **zero should_scan**（级联递归内无 per-op 扫描钩子；唯一一次落在批后失效窗） | F2 互斥切换对成功即定向扫描 | A |
| 缺口③ | P1 同上（表象「hover 失败」「取消/确定残留」） | 扫描链三弱点：`__vue__` F6 导航后失效窗 + 位置式 nth-child 失锚（克隆行插入） + targetMenu 定位 dropdownIndex/回退第一个可见 menu（与级联链 menuId 不同源） | F3 menuId 统一+展开复核；F4 row_selector 文本锚定 | B |
| 缺口④ | P1 下游（reconcile「无可用恢复边」） | pending 无该项 + 行状态读取同依赖 `__vue__` 展开（失效窗内 status 不可读） + **运维正交维度不在状态矩阵**（R1 无边可走） | F5 运维维度恢复边 | C |
| 新 P7 | （r12 报告未列，本轮新定案） | submitB 网络监听「捕获 0 请求」误判 → URL 已变+消息已捕获仍 JS click 盲重试 → 列表页盲点 primary 按钮 → 残留弹窗（与 `__vue__` 失效叠加） | F9 重试判据收窄 | A |
| P2 | F2 TRANSIENT-WAIT 判据缺陷 | 已观测过渡态 Stopping 入矩阵 keys 后条件②永不触发（r12 已定案） | F6 判据数据驱动化 | C |
| P3 | F3 伪边首碰盲区 | 首次消费无 validated 记录漏拦（r12 已定案，后续 3 次 EDGE-SKIP-NAV 正常） | F7 边候选角色门 | C |
| P4 | R1 ready 疑点 | 09:18:37 对 Stopped 态开机未执行，成因未定案 | F10 settle 可观测性 + 探针③ | C |
| P5 | 装配序（进入运维模式 main 中段第 24 位） | 互斥态切换对在中段执行放大运维态干扰半径 | F8 切换对装配排尾 | C |
| P6 | 开机四级连锁挂账 | 延续观察（本轮无新证据） | 不修 | — |

批十-A（F1+F2+F9）是收益主体：**单独 F2 即可在黄金窗内确定性捡到「退出运维模式」**（r11 成功路径的机制化），F1 是不依赖单次扫描的第二通道，F9 消除盲点击污染。

---

## 一 退出运维模式漏收：四重缺口叠加（P1 修正定案）

### 1.1 权威菜单快照（黄金证据，修正「运维态锁死」表述）

r12 09:19:42（进入运维模式成功后 22 秒）冷迁移 dropdown-click 的菜单快照（log L803，22 项）：

- **enabled(11)**：开机、冷迁移、修改别名、重装操作系统、创建快照、克隆、创建镜像、规格变更、系统盘扩容、网络配置、**退出运维模式**
- **disabled(11)**：关机、硬关机、重启、硬重启、热迁移、**删除**、重置密码、更新密钥对、修改Hostname、Console、执行命令
- 暂停/恢复/标签管理不在菜单（行 Stopped 态窗口性不可见，P5 定案一致）

三个关键结论：

1. **「退出运维模式」enabled=True——菜单结构完好，业务上随时可退出**。漏收问题纯在框架侧检测/收集机制，与环境无关。
2. **「删除」disabled=True 是真实业务禁用**（09:28:41 DESTROY-FINAL dropdown-click `clicked=False, reason='button-disabled', hasDisabledClass=True` 实证——展开点击链工作正常）。删除失败 = 退出运维模式未执行的下游结果，无需独立修复。
3. **r12 报告 P1「12 项 pending 全 disabled」表述修正**：该判定来自 09:28:10+ 扫描失败（enabled 集读空）后的**框架侧误判**。按权威快照，pending 12 项中**开机、网络配置、修改别名实际 enabled**（重装操作系统已执行）。真实锁死的是 8 项 + 删除；若 reconcile 能读到状态，至少 3 项可执行。

### 1.2 r12 失败链实证时间线（日志原文行号）

- 09:19:20 进入运维模式验证成功（L775，捕获瞬态消息「已进入运维模式」）
- 09:19:27 STATE-CHANGE 发现 7 个新启用：`['冷迁移', '重装操作系统', '创建快照', '克隆', '创建镜像', '规格变更', '系统盘扩容']`（L777）——**全是运维态解锁项，不含退出运维模式**
- 09:19:34-09:28:02 级联批 9+ 轮 STATE-CHANGE 递归执行（每轮扫描都成功读到运维态菜单：L1006/L1065/L1170/L1231「行已稳态」+ 各轮「发现 N 个新启用」），**无一轮检出退出运维模式**
- 09:28:03 STATE-RECONCILE 启动 pending 12 项（L1455，**不含退出运维模式**）→ 09:28:10「无可用恢复边，结束」（L1456-1457，仅跳过网络配置伪边）
- 09:28:10 唯一一次运维态 should_scan（级联批最后项系统盘扩容成功触发，L1459）：hover fail → dispatchEvent → has_visible_menu=False（L1495/L1513/L1514）→ new_buttons=[]（L1519）
- 09:28:32 二次 reconcile 同样零成功；09:28:32 DESTROY-FINAL 执行删除 → 09:28:41 按钮被禁用真实失败（L1604-1611）→ marker 行残留

### 1.3 r11 成功链对照（为什么 r11 能捡到）

- 14:11:41 进入运维模式成功（L1445，与 r12 同链同消息）
- 14:12:00 STATE-RECONCILE 立即启动（**+5 秒**，r12 为 +8.7 分钟）——菜单展开链路新鲜（`__vue__` 可用窗内）
- 14:12:16 / 14:13:09 / 14:13:49 reconcile R1 成功执行开机/暂停/硬重启三项（dropdown-click 全 clicked=True）——**运维态下这三项 enabled，与 r12 权威快照一致**
- 14:14:21 **硬重启成功的 should_scan**（L1629）：vue_show + nth-child(1) + has_visible_menu=True（L1665/L1661）→ **发现 1 个新按钮：['退出运维模式']**（L1672）
- 14:14:25 [WRITEBACK] 退出运维模式 → ui_result.dropdowns，插入位置 26（L1683-1684）
- 14:14:26 验证执行 → 14:14:54 成功（「已退出运维模式」）

**本质差异**：r11 进入运维模式在 main 倒数位（第 26 位），其后 reconcile 在新鲜窗内执行了成功操作，**成功操作的 should_scan 落在 `__vue__` 可用窗**撞上黄金收集机会；r12 进入运维模式在第 24 位，其后 main 无成功操作 + 级联递归内部无 should_scan 钩子，唯一一次扫描落在批后失效窗。**r11 的成功是时序运气，r12 把运气耗尽**——批十把运气变成机制。

### 1.4 根因定案（四个结构性缺口）

**缺口① STATE-CHANGE diff 语义盲区**（crud_executor.py L1276-1281）：

```python
for text, is_enabled in current_states.items():
    if is_enabled and text in button_states and not button_states[text]:
        newly_enabled.append(text)
```

- 检测语义 =「已知按钮 disabled→enabled 翻转」。`text in button_states` 要求历史在册；「退出运维模式」在行正常态从未出现（STATE-INIT L171 仅 7 键：取消/确定/硬重启/修改别名/网络配置/Console/进入运维模式）→ 不在 button_states → **永不进 newly_enabled**。
- 09:19:27 首轮扫描后 `button_states.update(current_states)`（L1285）把它入册为 **True**（当前值），从此永无 False→True 翻转可检测。9+ 轮扫描每轮都读到它 enabled，每轮都跳过——**机制性盲区，与时间窗无关**。
- 互斥切换对（进入↔退出运维模式）的退出侧天然落入此盲区：正常态只有 A 侧，翻转后 B 侧是新菜单项。

**缺口② 黄金窗零 should_scan**：should_scan 是主循环 per-operation 钩子（操作成功后 `_validate_single_tab_crud` L2630-2655 判定），**级联批递归内部不触发**。r12 黄金窗（09:19:20-09:28:02，菜单可展开 8.7 分钟）内 main 无成功操作 → zero should_scan；唯一一次由级联批尾项系统盘扩容成功触发，但已排在其后且落入缺口③失效窗。

**缺口③ 扫描链三弱点**（09:28:10 失败窗解剖）：
- hover fail：`nth-child(2) .el-dropdown` element not visible——**位置失锚**：克隆产物行（09:22:51 创建，时间倒序排最前）使 marker 行 nth-child(1)→nth-child(2)；r11 同轮内位置也 1↔2 跳变（14:11:20 为 2 / 14:14:22 为 1），位置式选择对行序变化零免疫。
- `__vue__` 不可用：系统盘扩容/规格变更走 [F6-EDIT-FLOW] 导航型操作，09:27:5x 返回列表页，09:28:10 扫描**恰在批八定案的 ~15s 失效窗内** → dispatchEvent 兜底假展开。
- 定位不同源：`_scan_row_state_snapshot`（级联链，黄金窗内 9 轮全成功）用 **menuId 优先**（getElementById 精确）；`_scan_row_after_operation`（INCREMENTAL-SCAN 链）用 dropdownIndex（克隆行插入后全页 .el-dropdown 序号 2→3 移位）+「回退第一个可见 menu」（批五 Gap B 同病根）。**同页面同时刻一条链成功一条链失败**，证明是链自身弱点而非环境。
- 叠加因素：submitB 盲重试的残留弹窗（09:28:01 被强制隐藏）恰在扫描前 9 秒，弹窗清扫余波与 `__vue__` 失效叠加（见 §五）。

**缺口④ R1 兜底失效**：
- pending 12 项不含退出运维模式（从未入册，R1 无恢复对象）。
- reconcile 启动时行状态读取走 `_scan_row_state_snapshot` 同链（`__vue__` 展开）——失效窗内 status 不可读，R1 无「当前态」输入；EDGE-SKIP-NAV 跳过网络配置 Stopping→Stopped 伪边后即「无可用恢复边」。
- **运维是正交维度**（command.row.maintenance 布尔标志，09:19:42 快照可读 maintenance=True），不属 6 态运行状态机——即使状态可读，种子矩阵也没有「退出运维模式」这条边。
- r11 对照：reconcile 启动于 +5s 新鲜窗，读态走边成功执行 3 项。

### 1.5 删除失败与「异常退出」定性（用户问题闭环）

- **删除未执行**：根因链闭合 = 退出运维模式未执行（缺口①-④）→ 行锁运维态 → 删除按钮**真实业务 disabled**（权威快照 + dropdown-click button-disabled 双证）→ DESTROY-FINAL 诚实失败（[F2-3] 状态性首败不重试，交状态机窗口恢复——窗口恢复依赖的正是缺口④）。退出运维模式修复后**自愈，无需独立修复项**。
- **「异常退出」核实**：r12 无崩溃证据（输出目录无新文件、playbook 09:28:41 正常保存、无 Traceback/browser_closed 信号）——属**正常收尾退出**。用户观察到的「异常」实为终局状态：28 操作仅 15 成功、删除失败 marker 残留、大量 pending。方案修的是终局状态的根，不是不存在的进程异常。
- 观察项（不修，记录）：L1612「Stage 1 关键操作验证失败: ['delete-like']」后质量门控仍通过（L1616 Phase B ✅）。是否需要在门控中对关键操作失败加显式警示标记，交用户决策。

---

## 二 修复设计

### F1 STATE-CHANGE 新菜单项检测语义（缺口①，批十-A）

`_handle_state_change_cascade`（L1276 起）diff 循环扩展第二通道：

```python
newly_enabled = []
newly_discovered = []   # ★ 批十：新出现菜单项通道（text not in button_states）
for text, is_enabled in current_states.items():
    if text in executed_actions or _attempted_exhausted(text, attempted_actions):
        continue
    if text in button_states:
        if is_enabled and not button_states[text]:
            newly_enabled.append(text)
    else:
        # ★ 新键 + 当前可用 + 非已知 UI 噪声 → 新发现项
        if is_enabled and text not in KNOWN_UI_NOISE:
            newly_discovered.append(text)
```

- `KNOWN_UI_NOISE`：取消/确定等行内常驻基线与弹窗确认按钮（r12 实证它们恒在 direct_buttons 基线，正常不进 states，但防其他环境弹窗按钮混入）。**词表单源**：入 const.py，与既有 `STATE_PENDING_ERROR_TEXTS` 等同层管理。
- 新发现项处理与 newly_enabled **同链同构**：稳态等待、F2-预检（Vue disabled prop 权威）、is_destroy_text → deferred_destroy、`_do_generic_operation` 执行——零新增执行路径。
- **入册同构（关键）**：新发现项执行成功后走与 should_scan 相同的 WRITEBACK 语义（ordered_actions 插入 + ui_result.dropdowns + validated 链）。当前 should_scan 的入册是内联在 `_scan_row_after_operation` 调用点的；抽取共享 helper（增量入册函数），should_scan 与 STATE-CHANGE 两处复用——顺带统一批五定案的「双通道入册不同源」。
- 日志签名：`[STATE-CHANGE][NEW-MENU-ITEM] {text}（新出现菜单项，入执行链）`——r13 验收锚点。

反例论证：
- 正常态首次扫描（STATE-INIT 后第一轮级联）会不会把全菜单都当「新发现」？——STATE-INIT 已预扫描入册（L102 update_states），正常态级联轮次时 button_states 已含全部正常态菜单项，新键通道只在**翻转后新菜单项**出现时触发（退出运维模式/解冻/解锁侧）。
- 未知环境的新菜单项会不会误执行？——执行链有 F2-预检 + 消息验证 + _attempted_exhausted 预算，与既有 newly_enabled 同保护级；且新菜单项本身就是收集目标（发现即测），执行它正是流水线使命。

### F2 互斥切换对成功即定向扫描（缺口②，批十-A）

主循环成功验证后 should_scan 判定处（L2630-2655）增加定向分支：

```python
# ★ 批十 F2：切换对 A 侧成功 → 立即定向增量扫描（黄金窗保证）
#   切换对表来源：既有「已切换为新状态」检测的同构数据（INCREME NTAL-SCAN
#   切换对检测），对称文本表（进入↔退出运维模式、冻结↔启用、锁定↔解锁）。
if not should_scan and _is_state_toggle_action(action, validated_texts):
    should_scan = created_marker
    LOG.info(f"  [PAIR-SCAN] {action} 为状态切换对 A 侧，触发定向增量扫描")
```

- `_is_state_toggle_action`：操作文本命中切换对表 A 侧（切换对表从收集册既有切换对检测结果构建，运行期按「消失按钮清单 ↔ 新按钮清单」自动配对，不写死运维模式文本——满足「不写死」宪法原则）。
- **黄金窗保证**：进入运维模式验证成功（09:19:20）时 `__vue__` 必新鲜（刚完成点击+验证），定向扫描用 `_scan_row_after_operation` 现链即可展开成功（r11 14:14:22 同型成功实证）→ 发现退出运维模式 → **F1 的入册 helper 接力** → 级联批/reconcile 有对象可执行。
- 该扫描在级联批**之前**触发（操作成功后的主循环钩子位置先于 `_handle_state_change_cascade`），退出运维模式入册后可被当轮级联批执行，无需等 reconcile。
- 日志签名：`[PAIR-SCAN]`。

反例：切换对 B 侧不可见（某些系统的退出侧不在菜单而在确认弹窗）→ 定向扫描发现 0 项，无害（空发现=正常扫描结果）；r12 反证 P5 定案的窗口性项（标签管理）——扫描照常按 offsetWidth 过滤，不会把窗口外项误入册。

### F3 扫描链 menuId 统一 + 展开复核（缺口③定位部分，批十-B）

`_scan_row_after_operation`：
1. 展开 JS 增抓 menuId（`menu.id || menu.getAttribute('data-popper-id')`，与 `_scan_row_state_snapshot` L603-605 同源）。
2. targetMenu 定位优先级改：**menuId（getElementById）→ 行 scope 内 `.el-dropdown-menu`**（`row.querySelector`）→ dropdownIndex。**删除「回退第一个可见 menu」全页回退**（批五 Gap B 病根：捡到别的行的菜单）。
3. has_visible_menu=False 且 method=dispatchEvent 时有界复核：2 次 × 800ms 轮询 menuId 节点 offsetWidth——合成事件偶发生效的假阴性收窄；仍 False 才判零收集。
4. **评估项（实施时定）**：`_scan_row_after_operation` 的菜单发现职责改为消费 `_scan_row_state_snapshot`（两链合并，menuId/states/vue_states 一套数据）——若合并风险低则做，风险高则只统一定位口径留待批十一。

### F4 row_selector 文本锚定（缺口③失锚部分，批十-B）

`get_row_selector_by_marker`（row_interaction.py L284）现返回位置式 nth-child；改造为**文本锚定双形态**：

- Playwright locator 消费点（hover/点击）：`page.locator('.el-table__row', has_text=marker)`。
- page.evaluate 消费点（展开 JS / 扫描 JS）：JS 开头改文本找行 `const row = Array.from(document.querySelectorAll('.el-table__row')).find(r => r.textContent.includes('{marker}'))`——与生成侧找行逻辑**同域同构**，消除「scope 查找生成 / document 全局使用」的查询域断裂（批九前已定位的 L123 全局 querySelector 缺陷顺带根治）。
- 兼容：row_selector 字符串仍返回（既有消费点传参不变），evaluate 侧改为传 marker 由 JS 内定位；分两步走（先 evaluate 侧，locator 侧跟进）。
- 反例：marker 文本跨行重复（同名列多行）→ 找行取 first（与现行为一致）；marker 文本不在行内（行不存在）→ row=undefined 静默返回，与现状一致。

### F5 R1 运维维度恢复边（缺口④，批十-C）

- 数据通道：`_scan_row_state_snapshot` 顺带读 `command.row.maintenance`（与 status/qga_ready 同模式加一个字段，L678 附近 rowStatus 读取处并列）。
- 种子矩阵新增正交维度边：`(maintenance=True, any_status) --退出运维模式--> (maintenance=False, status 不变)`。settle 采集点：退出运维模式成功时记录（maintenance True→False）。
- R1 消费：pending 含运维切换项（**依赖 F1/F2 入册**）且行 maintenance=True 可读时，运维边优先（解锁其余 pending 的前置）；不可读时保持现降级语义（批五：vue_states 直接执行）。
- 不写死「运维模式」文本：转移边按 F2 的切换对表动态命名（A 侧成功 settle 时自动记录 B 侧边）。

### F9 submitB 重试判据收窄（新 P7，批十-A）

navigation_executor submitB 链：JS click 重试前置判据——**URL 已变 + 已捕获成功消息 → 禁重试**（提交已生效，重试=在错误页面上盲点 primary 按钮）。仅「URL 未变 + 无消息 + 0 API」三无时才允许 JS click 重试（真静默失败场景保留）。日志签名：`[submitB-SKIP-RETRY] URL 已变+成功消息已捕获（网络计数 0 为监听窗误判）`。
- 「捕获 0 个 API」监听器注册时序问题本身**不修**（F9 判据已覆盖其伤害面；监听器机理列探针④观察）。

### F6/F7/F8/F10（r12 遗留承接，批十-C）

- **F6（P2）**：TRANSIENT-WAIT 条件②从「status 不在矩阵 keys」改为数据驱动——「status 在矩阵已观测态但该态 pending 无出边记录且 pending 未清 → 再 settle 2 次」。Stopping 已入矩阵后不再被条件①②互斥卡死。
- **F7（P3）**：转移边候选加角色门——首次消费时无 validated 记录的边不消费（首碰拦截）；种子跨轮累积时保留 validated 标志位。
- **F8（P5）**：互斥态切换对装配序排尾（ordered_actions 生成处，切换对 A 侧操作排到非切换对操作之后）——缩小运维态/冻结态等正交维度的干扰半径。
- **F10（P4）**：R1 ready 判定补结构化日志（decided/undecided + 原因枚举），r13 真机探针后定案是否需要行为修复。

---

## 三 探针清单（不确定处，真机先行，只读不改数据）

1. **`__vue__` 失效窗定量探针**：F6 导航返回后 0-30s 内 0.5s 间隔探测 marker 行 dropdown `__vue__` 可用性落盘时间序列——定量复核批八 ~15s 定性结论，为 F3 复核轮询参数定参（当前取 2×800ms 是估值）。
2. **nth-child(2) not visible 结构探针**：扫描失败时 dump 该行 DOM（display/高度/viewport 坐标/固定列克隆区归属）——定 F4 evaluate 侧找行是否需要额外 scope 限定；同时定案「位置命中错误行」还是「正确行但视口外」。
3. **R1 边枚举 dump 探针**：reconcile R1 启动时 dump 种子矩阵全部边 + pending 需求态 + 行状态读取结果——定案 09:28:03「无可用恢复边」精确成因（矩阵无边 vs 状态不可读 vs pending 需求推导失败），并服务 F10。
4. **submitB 网络监听窗探针（可选）**：0-请求误判的监听器注册时序 dump——验证 F9 判据覆盖完整后此项可降级为观察。

探针纪律：独立 Playwright 只读脚本，不点提交、不产业务数据；有状态变更的探针结束态恢复常态入证据基线（宪法原则）。

---

## 四 实施顺序与批次划分

| 批次 | 修复项 | 耦合理由 | 验证 |
|------|--------|----------|------|
| 批十-A | F1 + F2 + F9 | F1/F2 共享「增量入册 helper」抽取（双通道入册单源化）；F9 独立小修顺手同批 | 单测 + 全量回归 exit 0 + 独立提交 |
| 批十-B | F3 + F4 | 扫描链健壮性双修（定位统一 + 文本锚定），同属 `_scan_row_after_operation`/`get_row_selector_by_marker` 邻域 | 同上 |
| 批十-C | F5 + F6 + F7 + F8 + F10 | R1/种子矩阵/装配序邻域（F5 依赖 A 批入册先行验证） | 同上 |
| r13 | 真机 Stage 1 复验 + 全链 Stage 2-5 | — | §五 验收口径 |

前置条件：环境残留人工清理（marker 行 test741982 + 克隆产物 test742556）——否则 marker 定位与配额继续受污染。
每批纪律：幂等、断言先行、CRLF 行尾保持、py_compile + 全量测试（969 基线）exit 0 才提交。

---

## 五 r13 验收口径总表

| # | 口径 | 判定信号（日志签名） |
|---|------|----------------------|
| 1 | 退出运维模式入册+执行成功 | `[PAIR-SCAN] 进入运维模式 为状态切换对 A 侧` 或 `[STATE-CHANGE][NEW-MENU-ITEM] 退出运维模式` + [WRITEBACK] + 「已退出运维模式」消息 |
| 2 | 删除收尾成功（自愈验证） | `[DESTROY-FINAL] ✓ 删除` + marker 行清理（列表无 test* 残留） |
| 3 | pending 大幅下降 | reconcile pending 12→≤4（开机/网络配置/修改别名可执行；窗口性项按 P5 短路） |
| 4 | disabled 判定对齐权威快照 | 运维态下 enabled 项判定与菜单快照一致（扫描失败误判清零：无「全消失」型 disappeared 清单） |
| 5 | submitB 误重试清零 | `[DIAG-submitB-js-retry]` 不出现于 URL 已变 + 消息已捕获场景；无 transition 强制隐藏由盲点击引入的残留弹窗 |
| 6 | 无回归 | estack-ecso-cz1 正常态操作全量不变；969 测试基线全绿；级联批/稳态等待/预检行为不变 |
| 7 | 判定=业务真实成功延续 | 双口径分离核验（判定成功 0 假成功），r11/r12/r13 三轮对比表 |
| 8 | 全链 Stage 2-5 | 回放/API/UI 门禁四口径（删除步骤入 execution_order 后回放链完整） |

---

## 附：本轮取证产物索引（监控端 tmp，不入库）

- r12_maint_window.txt — 09:19 窗口全行 + 全日志 STATE-CHANGE/运维模式相关行（真实文本）
- r11_r12_exitmaint_compare.txt — r11 退出运维模式成功链全行 + r12 收尾链 L1490-1580
- r12_final_chain.txt — 09:19:42 allItems 未能正则解析（修正版见 probe3）+ L1438-1600 收尾链 + 稳态扫描汇总
- r12_probe3.txt — 09:19:42 权威菜单快照精确解析（enabled/disabled 全清单）+ L1600-1700 删除执行细节 + 三窗展开方式汇总
- 前序提取：r12_win_enter_maint / r12_win_final_scan / r12_editflow_submit / r11_win_success_scan / r12_scan_vueshow（批九监控期产物）
