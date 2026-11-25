# EIS 云主机批六修改方案（r8 验收根因定论与源头修复设计）

> 状态：方案设计（不改代码）。上游输入：r8 真机验收（2026-11-25，09:21:20→10:02:14，41 分钟）+ 用户 4 个观察问题 + 13 项问题汇总（tmp/r8_ops_report.md）。
> 项目宪法：docs/CORE_PRINCIPLES.md（三阶段一致性）。所有修复不得违背——尤其 Stage 1 探测记录的行为必须可被 Stage 2 回放。
> 单一权威前序方案：docs/debug/eis云主机批五修改方案.md（本方案所有「批五 F1-F9」引用均指向该文档）。

---

## 一、r8 验收基线摘要

- Stage 1 验证通过：57 按钮 / 28 操作已验证，业务闭环 20/28 成功，质量门控通过，关键操作失败仅 `['delete-like']`（删除 confirm_word_missing）。
- 执行尝试 58 次（主循环 28 + STATE-CHANGE 批 15 + STATE-RECONCILE 15），总时长 41 分钟（r7b 基线约 16 分钟，膨胀主因 = 批/RECONCILE 对环境固有失败操作的反复完整重跑）。
- 批五验收实证：F2 收集缺项 4 项全收集全尝试（Console✓ 冷迁移✓）；F7 稳态等待生效；F8 三通道判定生效；RECONCILE 5 轮累计恢复 9 项。
- r8 新发现的三类缺陷（批五引入候选）：dialog_detected UnboundLocalError、terminal 失败弹窗残留串扰、级联批失败无记忆重跑——本方案全部定论并给出修复。

---

## 二、用户 4 问根因定论（含证据）

### Q1：单业务为何重复执行（修改Hostname/重置密码/克隆不应重复；重启/暂停界面无失败却记录多次失败）

**定论：四重机制叠加，其中机制 2/3 属缺陷，机制 1/4 属设计内行为。**

- **机制 1（设计内）**：`_error_driven_retry` 每操作最多 2 轮（「第 1 轮失败/第 2 轮失败」日志签名）。失败重试 1 次后收尾，是防御性设计。
- **机制 2（缺陷 A3）**：批五 F1 的 `executed_actions` 仅业务成功入册（crud_executor L948-955），失败不入册 → 每轮 STATE-CHANGE 批 `get_newly_enabled` 把终败操作判为「新启用」完整重跑；RECONCILE 的 `_collect_state_pending` 同样按业务成功排除。实证：修改Hostname 4 次尝试（主循环 1 + 批 3）、重置密码 4 次、克隆 6 次（主循环 2×2 轮 + 批 2×2 轮）——41 分钟时长膨胀的主因。
- **机制 3（缺陷 A2 串扰伪失败）**：批内「更新密钥对」required_field_empty 终态失败后弹窗残留（详见 Q1 时序铁证，见 §二-A2），下一批内操作「修改Hostname」点击后 observe 检测到的弹窗 title 是**残留的「更新密钥对」弹窗**（09:42:47，日志 L2801 actual_state='dialog: 更新密钥对'），generic 路径把残留弹窗当自己的弹窗做表单处理 → required_field_empty field=**密钥对** 错挂到修改Hostname 名下（09:42:53）→ 又是终态失败 → 下轮批再重跑。**修改Hostname 的 3 次批内尝试全部是伪失败，真实弹窗从未被打开验证过。**
- **机制 4（设计内，状态机感知调度）**：重启/暂停在主循环的失败是行状态过渡窗（Running→Stopping 菜单翻转期间按钮 disabled / 菜单项消失）的真实记录（09:26:50 重启 disabled skipped×2、09:27:10 暂停 click_failed×2），之后由 RECONCILE 恢复成功（重启 09:35 ✓ 暂停 09:35 ✓）。「界面无失败」是用户看最终态全部成功；过程失败记录是过渡窗口的真实 DOM 状态，属批一/批五状态机感知调度的设计行为，不修。

**时序铁证（日志行号 = eis_s1_vm_r8.err.log）**：

```
09:41:40 更新密钥对 required_field_empty is_terminal=True（密钥对无可选选项，环境固有）
09:41:40 [STATE-CHANGE] 执行新启用按钮: 修改Hostname   ← 0 秒间隔，同批立即开始
09:42:41 dropdown-click clicked=True text='修改Hostname'（菜单点击成功）
09:42:47 [observe] 检测到弹窗: title='更新密钥对'      ← 残留弹窗！
09:42:48 [generic-precheck] actual_state='dialog: 更新密钥对'  ← 直接把残留弹窗当自己的
09:42:52 ✗ 填充 密钥对 失败
09:42:53 第 1 轮失败: [required_field_empty] field=密钥对 text=修改Hostname  ← 错位归因
```

### Q2：进入运维模式成功后「退出运维模式」为何未收集未执行

**定论：三重根因叠加，全部在主循环成功后处理链（L1907→L1922→L1939→L2049）。**

- **根因 1（顺序）**：主循环操作成功后的顺序是 STATE-CHANGE 批（L1907）→ STATE-RECONCILE（L1922）→ INCREMENTAL-SCAN（L1939）→ transition_to_next 清扫（L2049）。进入运维模式 09:59:27 成功后，RECONCILE 一跑 2 分 20 秒（09:59:35→10:01:5x），扫描被推迟到 10:01:55，菜单翻转（含「退出运维模式」）的收集窗口被拖长且环境被 RECONCILE 副作用污染。
- **根因 2（扫描无前置清扫）**：10:01:55 扫描时 RECONCILE 期间重装/规格变更留下的弹窗仍开着（扫描 DEBUG：`direct_buttons_found=['取消','确定']`，说明弹窗 footer 按钮被扫进行内按钮；`has_visible_menu=False` 菜单被遮蔽）→ 收集 0。而含验证的清扫 `transition_to_next`（强制隐藏+验证，L108 签名「[transition] 强制隐藏了 N 个残留弹窗」）排在扫描**之后**（10:01:57 才隐藏）。
- **根因 3（切换对盲区）**：扫描对消失按钮的处理是「检测到状态切换按钮对: XXX → 已切换为新状态」（L1979，10:01:56 一次性把 22 个消失按钮全部标记消化）——**只消化消失侧，不把配对出现的新文本（退出运维模式）入册**。运维模式下菜单已翻转稳定（RECONCILE 期间 4 次 dropdown diag 均可见「退出运维模式」），但因为根因 1/2，扫描从未干净地看到它。

**后果**：退出运维模式从未入册 → 从未执行 → F13（VM test569722 终局留在运维模式未恢复）。

### Q3：删除为何在位置 1 提前执行（应最后、且须在退出运维模式之后）

**定论：delete_actions 陈旧快照 + `_infer_action_role` 精确匹配不做关键词兜底，delete-last 策略因此未触发。**

证据链：

1. `_delete_actions` 在主循环初始化时计算（crud_executor L1418 `_find_create_delete_actions(buttons_by_action)`），此时 Phase A 静态扫描未收集 hover 行级 dropdown 菜单项（批五 F2 已定案的静态收集恒 0）→ **快照不含「删除」**。
2. create 成功后 F2-RECOLLECT（`_rediscover_marker_dropdowns`）收集到「删除」时（L1316），`_infer_action_role(text, btn, delete_actions=陈旧快照)` 的 delete 分支是 `action in delete_actions` **精确成员匹配**（button_detector L776），不做关键词匹配 → 判 generic。日志实锤：`[F2-RECOLLECT] + 删除 (角色: generic, 插入位置: 1)`（09:23:51）。
3. delete-last 策略（L1319 `if role == "delete": ordered_actions.append(text)` 追加末尾）因 role=generic 未触发 → 走 else 分支插到 current_action（create）之后 → **位置 1，第二个执行**。
4. 同一缺陷模式也存在于 INCREMENTAL-SCAN 入册点（L2026）。
5. 单源关键词判定函数 `is_destroy_text`（shared/button_clicker L378，DESTROY_KEYWORDS=["删除","移除","delete","remove","清空","clear"]）**已存在但入册路径未使用**。

**危害定级 P0**：删除若在第 2 位执行成功，后续约 20 个行级操作全部失去目标行（row_not_found 连锁）。r8 因 B4 确认词填充失败（填了随机值 test569923 而非 DELETE，F3 守卫拦截提交）删除未成功——**属于被 B4 缺陷「侥幸」掩盖的 P0 数据生命周期风险**。

**顺序的完整语义**（用户口径正确）：删除 = 行生命周期终点，必须在所有行级操作（含进入/退出运维模式这类状态恢复操作）之后最后执行。

### Q4：重装操作系统 tooltip 二次确认（doubleConfirm）能否自动识别

**定论：可以通用识别，且当前被守卫 B 误杀——两步提交形态完全不可见。**

证据链（RECONCILE 段重装执行）：

```
10:00:42 弹窗 title='重装操作系统-云主机-test569722' 打开，扫描 8 字段
10:00:44 填充 3 字段（登录名/登录密码/确认密码，密码联动正确）
10:00:45 [click_page_button] KB XPath (overlay) 成功: '确定'  ← 主提交点击已生效
10:00:46 [submit_form_v2] 守卫 B: 提交点击完成但同指纹弹窗 500ms 后仍开启，判 dialog_still_open
```

- 业务真实行为：点「确定」后弹出 `div[@role="tooltip"]` 二次确认层，内含 `id="doubleConfirm-confirm"` 按钮，需再点一次才真正提交（用户口径，与日志吻合：主提交后弹窗「未关」正是因为业务在等二次确认）。
- 该层**不在任何容器检测白名单内**：`_check_precondition_state`/`observe`/守卫 B 指纹/`_close_dialog`/`_dismiss_all_overlays` 只认 el-dialog / el-message-box / popconfirm / drawer（r8 全日志 `doubleConfirm`=0、`role="tooltip"`=0 证实全程不可见）。
- 守卫 B 把「待二次确认」误判为「提交未生效」→ dialog_still_open（is_terminal=False）→ _error_driven_retry 第 2 轮完整重跑 → 再失败 → 批/RECONCILE 反复恢复重装（3 次尝试）。**重装在 r8 的全部失败都是这个误杀**，业务主提交从未被完成。

**通用识别方案**（不写死 EIS 私有 id）：见修复 2。

---

## 三、13 项问题与本方案的映射

| 编号 | 问题 | 定性 | 本方案归属 |
|---|---|---|---|
| A1 | `cannot access local variable 'dialog_detected'`（修改Hostname 主循环 _do_edit 两轮复现） | 批五 F3 引入缺陷 | 修复 4（P0） |
| A2 | terminal 失败弹窗残留 → 串扰错位归因 | 缺陷（三重缺口） | 修复 3（P0）+ Q1 机制 3 |
| A3 | 级联批对固有失败操作无记忆完整重跑（41 分钟主因） | 批五 F1 语义缺口 | 修复 5（P1）+ Q1 机制 2 |
| B4 | 删除确认词填随机值（test569923）非「DELETE」，F3 守卫拦截 | 既有缺口（dialog 表单路径绕过确认词提取） | 修复 7（P1） |
| C5 | 密钥对无可选选项（环境固有） | 环境 | 修复 5 后不再重跑 |
| C6 | QGA 未就绪（执行命令/系统盘扩容前置） | 环境 | 同上 |
| C7 | 存储配额不足（镜像类创建） | 环境 | 同上 |
| D8 | 标签管理 click_failed（菜单 21 项时点不到） | 既有遗留 | 观察项（修复 6 后复验） |
| D9 | 重装操作系统 dialog_still_open | = Q4 误杀 | 修复 2（P0） |
| D10 | 网络配置 click_failed | 既有遗留 | 观察项 |
| E11 | 创建 fill_rules 全错标 name_pattern；fill_data 数字字段（数量/CPU/内存/系统盘/登录名）全部污染为 marker 文本 test569722 | 产物质量缺陷（Stage 2 回放风险） | 修复 8（P2） |
| E12 | r8 创建段「编辑字段」日志消失（r7b 有） | 可观测性退化 | 修复 9（P2） |
| F13 | VM test569722 终局留在运维模式未恢复 | = Q2 后果 | 修复 6（P1）后自然解决 |

---

## 四、修复项设计（源头修复）

> 原则：每项修「生成链/调度链源头」，不修产物、不打补丁脚本；行为对无该场景的模块零变化（幂等）；遵守宪法三阶段一致性（探测记录的操作方式 = 回放执行方式）。

### 修复 1（P0，Q3）：入册点 delete 角色判定单源化

- **根因**：`_infer_action_role` delete 分支只做 `action in delete_actions` 精确匹配（button_detector L776），依赖调用方传入的快照；两个入册点（crud_executor L1316 F2-RECOLLECT、L2026 INCREMENTAL-SCAN）传入的是 Phase A 时点（L1418）的陈旧快照，晚收集的「删除」永不命中 → generic → delete-last 失效。
- **改动点**（单点，源内最小）：`core/discovery/ui_scanner/button_detector.py` `_infer_action_role` 的 delete 分支：

  ```python
  # 修改前
  if delete_actions and action in delete_actions:
      return "delete"
  # 修改后（保持快照匹配优先，加单源关键词兜底）
  if (delete_actions and action in delete_actions) or is_destroy_text(action):
      return "delete"
  ```

  `is_destroy_text` 从 `core.discovery.shared.button_clicker` 导入（ui_scanner → shared 无循环依赖；关键词与 `_find_create_delete_actions`（L822 同 import）同源，不会漂移）。
- **效果**：F2-RECOLLECT 收集「删除」→ role=delete → `ordered_actions.append` 末尾（L1319 策略自然生效）；INCREMENTAL-SCAN 同理（L2030 的 delete 分支同口径）。**主循环末尾的删除位置 = 所有行级操作之后**，满足用户顺序要求（退出运维模式在删除之前——修复 6 保证退出运维模式已入册，主循环序在其前）。
- **验证口径**：① 单测：`_infer_action_role('删除', btn, delete_actions=[])` → 'delete'；`('随机操作', btn, delete_actions=[])` → 不误判（'恢复' 不含破坏词 → generic 正常）；② r9 日志 `[F2-RECOLLECT] + 删除 (角色: delete, 插入位置: <末尾>)`；③ 全量测试回归。
- **回归风险**：低。is_destroy_text 与 `_find_create_delete_actions` 关键词同源，仅把「漏判」收敛为正确判定；唯一行为变化是破坏性操作从 generic 路径转 delete 路径（删除专有路径本就是批一/宪法设计目标）。

### 修复 2（P0，Q4/D9）：提交后二次确认层通用识别 + 守卫 B 豁免

- **根因**：两步提交的二次确认层（`div[role="tooltip"]` + `#doubleConfirm-confirm`，EIS doubleConfirm 私有形态）不在容器白名单，守卫 B 500ms 同指纹检查把它当「弹窗未关 = 提交未生效」误杀。
- **方案（结构判据，零硬编码）**：
  1. **识别**：提交点击后 500ms，JS 扫描新出现的可见确认浮层：候选集 = `[role="tooltip"], [role="alertdialog"], .el-popper, [role="dialog"]:not(.el-dialog):not(.el-message-box)` 中 offsetWidth>0 且含可点击按钮（文本 ∈ 确认词表：确认/确定/继续/是/重试/confirm/ok/yes，单源进 const.py 词表区）的层。禁用（Vue prop / is-disabled）跳过。
  2. **执行**：检测到 → 点击层内确认按钮（文本精确优先）→ 回到原验证链（弹窗关闭检测 + 成功消息判定）。
  3. **守卫 B 豁免**：`form_filler.submit_form_v2` 判 dialog_still_open **之前**先做上述检测——有二次确认层则点掉后重新评估弹窗状态（弹窗关闭 → 走成功验证，不再落 blocked）；检测不到浮层才维持原判定。守卫 B 的判据与日志不变。
  4. **落点**：检测函数放 shared（Stage 1 submit_form_v2 与既有 chain-confirm Phase 2b 共用），`_do_generic_operation` 提交后段（crud_executor L5504 附近）扩展 chain-confirm 支持浮层形态；Stage 2 回放引擎（replay/button_driver confirm_dialog）同步——**宪法要求**：Stage 1 自动识别的行为必须落进 playbook steps（fill_form 后追加 `click_double_confirm` 步骤，playwright_locator 按实际层结构生成），Stage 2 按步骤回放，不是 Stage 2 再探测一次。
- **前置探针（r9 前真机，P-D1）**：重装弹窗打开 → 点确定 → dump 二次确认层完整 DOM（tag/role/class/id/按钮文本/可见性/与主弹窗的 DOM 关系）——确认识别候选集覆盖、确认按钮文本命中词表、层是「新出现」还是「主弹窗内嵌替换」。探针只读取证，不点 doubleConfirm-confirm（不产业务数据）。
- **验证口径**：① r9 重装操作系统：主提交 → 二次确认自动点击 → 成功消息判✓，dialog_still_open 清零；② 单测：mock 浮层结构（role=tooltip + 确认按钮）识别与点击；无浮层页面零变化；③ playbook 含 click_double_confirm 步骤。
- **回归风险**：中低。误点防护 = 确认词表 + 仅在「提交点击刚发生」上下文内检测 + disabled 跳过；对无二次确认的业务，扫描不到浮层走原路径零变化。**不写死 doubleConfirm-confirm / role=tooltip 任何单点形态为业务规则**——role/class/文本全是候选集成员而非充分条件组合。

### 修复 3（P0，A2/Q1-机制3）：弹窗归属校验 + 关闭验证强化 + 批内清扫

- **根因（三重缺口）**：
  - 缺口 1：`_close_dialog`（button_detector L337）点关闭 → 500ms → Escape → 返回，**无关闭验证、无重试、无失败告警**——required_field_empty 终态路径（L5354）调用了它但弹窗实际未关上（required_field_empty 两个产生点 L5354/L5888 都有调用，问题在关闭不保证生效）。
  - 缺口 2：`_do_generic_operation` 的 precheck/observe 只要看到「有弹窗」就当作当前操作的弹窗，**无归属校验**（title 与 action 不比对）——串扰直接错位。
  - 缺口 3：`transition_to_next`（含 `_dismiss_all_overlays` 强制隐藏 + 验证，transition_helpers L20）只在主循环操作后（crud_executor L2049）与 create 成功后（L3131）调用——**STATE-CHANGE 批与 RECONCILE 的操作间无清扫兜底**。
- **方案**：
  - **3a 归属校验**：`_do_generic_operation` 拿到 `actual_state='dialog: <title>'` 后校验归属：规范化 btn 文本是 title 的子串（EIS 实证 4 例全满足：'删除'⊂'删除云主机test569722'、'修改Hostname'⊂'修改Hostname'、'重装操作系统'⊂'重装操作系统-云主机-test569722'）。不匹配 → LOG.warning「弹窗归属不匹配」→ `_dismiss_all_overlays` 清残留 → **重新触发一次**（重点击菜单项）→ 仍不匹配 → 落新错误类型 `dialog_mismatch`（终态：残留弹窗属其他操作，本操作从未真正打开）。防串扰归因错位。
  - **3b 关闭验证强化**：`_close_dialog` 追加：关闭动作后 500ms **验证可见弹窗清零**；未清零 → 二轮（关闭按钮 + 取消按钮 + Escape）→ 仍未清 → LOG.warning「弹窗关闭失败」+ 返回 False。调用方（L5354/L5888 等终态路径）在关闭失败时补 `_dismiss_all_overlays` 兜底。
  - **3c 批/reconcile 清扫**：`_check_and_execute_newly_enabled`（L836）与 `_state_aware_reconcile`（L1038）每操作**结束后**调 `_dismiss_all_overlays`（幂等、无弹窗时 no-op）——复用既有函数，不新增机制。
- **验证口径**：① r9 无「field=密钥对 text=修改Hostname」类错位失败；② 批内连续失败操作后无弹窗残留传递（「弹窗归属不匹配」日志 0 次，因 3c 已前置清掉）；③ 单测：mock 残留弹窗 + 不同 title → 触发归属校验告警路径。
- **回归风险**：低。3a 校验只在「title 与 action 完全无关」时触发（子串判据宽松，正常操作全过）；3c 用的 `_dismiss_all_overlays` 带验证且幂等。

### 修复 4（P0，A1）：`_do_edit` dialog_detected 未绑定修复

- **根因**：`_do_edit` 内 `dialog_detected` 唯一赋值在 L3886（守卫出口之后的成功段），批五 F3 新增的守卫 B 分支 return dict（L3794）与 no-locator 分支（L3808）**纯引用**未绑定变量 → 守卫 B 判 dialog_still_open 时必抛 `UnboundLocalError`。修改Hostname 主循环两轮复现。
- **方案**：`_do_edit` 函数体开头（try 之前）初始化 `dialog_detected = False`；成功路径 L3886 正常覆盖。**顺带审计**：rg `_do_create`/`_do_delete`/`_do_toggle_switch`/`_do_save_or_confirm` 全部 dialog_detected 引用-赋值对，确认无同模式（L3146 在 _do_create 内——其引用点若在赋值前同样修）。
- **验证口径**：① 单测：mock submit_blocked=dialog_still_open 的 edit 路径不抛异常且返回 dict 含 dialog_detected=False；② r9 无 `cannot access local variable` 日志；③ 全量回归。
- **回归风险**：极低。

### 修复 5（P1，A3/Q1-机制2）：终态失败跨域记忆（attempted_actions）

- **根因**：批五 F1 `executed_actions` 仅业务成功入册（L948-955），终败操作每轮批/RECONCILE 被当「新启用」完整重跑——r8 41 分钟 vs r7b 16 分钟的主因，且死循环风险靠挂死保险兜底。批五定案已指出「executed_actions 双语义反模式，修复须语义分离」——本修复即该定案的落地。
- **方案（语义分离，不动 executed_actions 既有语义）**：
  - 新增 `attempted_actions: dict[str, int]`（操作名 → 终态失败次数），与 executed_actions 平行维护。
  - **入册规则（保守，只收「不可自愈」终态）**：error_type ∈ {required_field_empty, form_validation, no_confirm_button, confirm_word_missing, no_fields, dialog_mismatch} 且 is_terminal=True（或 2 轮 retry 耗尽）→ 计数 +1。
  - **不入册（状态机域管理，保持恢复能力）**：click_failed、skipped（disabled）、dialog_still_open（修复 2 落地后此类大幅减少，暂不入册防误杀）、no_signal（消息误判风险留观察）。
  - **消费点三处**：`get_newly_enabled`（L105-120）排除 attempted；`_collect_state_pending`（L798）排除 attempted；`_state_aware_reconcile` 恢复转移目标排除 attempted。
  - **重试预算**：单操作全生命周期（主循环+批+RECONCILE 跨域合计）MAX_LIFETIME_ATTEMPTS=3；耗尽后若行状态矩阵记录该操作依赖的行状态发生变化（状态翻转可能解锁自愈），预算清零一次重新放行——第一版先实现「终败即跳过」，预算重置留 r9 观察数据后再决定（防止过度设计）。
  - **产物层**：attempted_actions 落 ui.json `terminal_failed_operations`（探针报告用，非回放契约）。
- **验证口径**：① r9 重置密码/更新密钥对/克隆只在主循环尝试 2 轮后不再入批/RECONCILE（日志无第 3+ 次「执行新启用按钮」）；② 总时长回归 ≤ 25 分钟；③ 成功操作跳过收敛行为不变；④ 全量测试回归。
- **回归风险**：中。核心是终态失败判定准确性——入册 error_type 白名单保守（全部是「环境不可自愈」实证类），click_failed/过渡窗类全部保留给状态机域；r9 若发现误杀（某操作状态翻转后可自愈但被 attempted 拦截），按预算重置机制补。

### 修复 6（P1，Q2/F13）：INCREMENTAL-SCAN 前置清扫 + 切换对新文本入册

- **根因**：见 §二-Q2 三重。主循环成功链顺序（L1907 批 → L1922 RECONCILE → L1939 扫描 → L2049 清扫）把扫描暴露在 RECONCILE 残留弹窗遮蔽下。
- **方案**：
  - **6a 前置清扫**：`should_scan` 成立后、`_scan_row_after_operation`（L1967）调用前插入 `_dismiss_all_overlays(page)`——复用 transition_helpers 既有函数。扫描环境干净后，运维模式菜单的 22 项（含「退出运维模式」）可正常渲染收集（RECONCILE 期间 4 次 dropdown diag 证明菜单翻转稳定可见）。
  - **6b 切换对新文本入册**：扫描结果处理（L1971-2045）中，disappeared_buttons 判定为「状态切换按钮对」时，同步检查 new_buttons/扫描快照：存在与消失按钮互斥配对的新文本（如 进入运维模式↔退出运维模式）→ 走既有入册链（L2026 `_infer_action_role`，修复 1 后角色正确）+ 插入位置与消失按钮原位对齐（级联对邻接语义，与 builder.py Cascade-Order 同口径）。互斥对判定用结构信号（同 command/同 DOM 槽位/文本前缀互补词表：进入↔退出、启用↔停用、开启↔关闭——词表单源进 const.py），不写死具体操作名。
  - **顺序不重排**（保守）：扫描保持在批/RECONCILE 之后（6a 清扫已解决遮挡问题；重排会影响「批看到最新按钮集合」的既有成功链，不做）。
  - **风险点记录**：扫描 row_selector 曾返回 `nth-child(1)`（L5192 DEBUG）——`get_row_selector_by_marker` 返回第 1 行依赖 marker 在首行，多行环境下可能扫错行（批五已知「互斥对文本取自错误行状态」同源）。r9 探针确认 marker 行定位正确性；错行场景由修复 6b 的互斥对词表兜底文本正确性（运维模式是全局行状态，退出运维模式文本与行无关）。
- **验证口径**：① r9 进入运维模式成功后 INCREMENTAL-SCAN 日志 `new_buttons` 含「退出运维模式」，角色判定正确（含「退出」非破坏词 → generic，不误入 delete）；② 退出运维模式被执行（主循环或批）✓；③ VM 终态恢复正常态（F13 清零）；④ 单测：清扫函数在扫描前调用（mock 验证调用序）。
- **回归风险**：低。6a 幂等；6b 只在「消失+新出现」同帧发生时入册，无切换对场景零变化。

### 修复 7（P1，B4）：删除确认词 dialog 表单路径提取

- **根因**（既有定案复核成立）：删除弹窗（el-dialog 表单形态）的字段填充走 dialog 表单路径，绕过 `_preprocess_confirm_form`；`generate_fill_value` 签名无 placeholder 参数 → 「删除确认」字段按 name_pattern 填随机值 test569923；F3 守卫正确拦截 disabled 确认按钮落 confirm_word_missing（守卫本身工作正常）。确认词提取正则须兼容中文引号（既有批三结论）。
- **方案**：
  - 表单扫描层（scan_form_fields_v2）把字段 `placeholder` 属性收进 field dict（r8 ui.json `edit_field_details` 有 selector 无 placeholder，实证采集缺口）。
  - 填充决策层：label 命中确认类关键词（确认/删除确认/输入…确认——词表单源）且 placeholder 含引号词（中文引号「」『』与英文 "" '' 都兼容）→ 提取引号内词作为填充值（「DELETE」）；提取不到 → 维持 name_pattern 现状（不阻塞）。
  - `_do_delete` 与 generic dialog 表单路径同源生效（删除弹窗走 dialog 表单，方案落在共享填充层而非 delete 专有路径）。
- **验证口径**：① 单测：`placeholder='输入 "DELETE" 确认'` 与中文引号变体提取出 DELETE；② r9 删除操作「删除确认」字段值=DELETE、提交按钮 enabled、删除业务成功——**八轮验收口径④（删除填 DELETE）首次真实通过**；③ 宪法一致性：playbook fill_form fields 里该字段值=提取的确认词（Stage 2 按记录回放）。
- **回归风险**：低。新增分支，仅确认类字段+有引号词 placeholder 触发。

### 修复 8（P2，E11）：创建表单 fill_rule 按字段类型纠正

- **现状实证**：ui.json 创建云主机 `fill_data`：数量/CPU/内存/系统盘/登录名 **全部 = marker 文本 test569722**（数字字段填文本）；playbook `fill_rule` 全标 `name_pattern prefix=test`（名称/别名正确，数字/密码/登录名字段错标）。r8 创建成功属 EIS 前端容错侥幸，Stage 2 回放按 fill_rule 重建必向数字框填文本。
- **方案**：fill_rule 推断按字段特征分支（源头在 fill_rule 生成点——**实施前 rg `fill_rule` 赋值链定位**（form_filler 扫描标注或 crud_executor 落库标注），修源头单点）：
  - `input[type=number]` / kb_category=el-input-number / label 命中数字词（数量/CPU/内存/容量/盘/规格）→ `number` 规则（默认最小合法值或保留表单默认值）
  - type=password / label 含密码 → 既有 password 规则（r8 登录密码 Test@688564 已正确）
  - label 含登录名/用户名 → username 规则
  - **仅名称/别名/描述类文本字段维持 name_pattern**（marker 提取链不动——名称字段是 marker 唯一来源，宪法要求保持）
- **验证口径**：① r9 创建 fill_data 数字字段为数字、playbook fill_rule 按类型正确；② 创建业务成功不回归；③ marker 提取链不变（名称仍 test 前缀时间戳）。
- **回归风险**：中。改创建值生成影响业务提交内容——EIS 数字字段有表单默认值，number 规则优先「保留默认值」策略降低偏差；r9 首跑验证。

### 修复 9（P2，E12）：创建段填充可观测性恢复

- **现状**：r7b 创建段有「编辑字段: 名称」日志，r8 消失（generic dialog 路径的「编辑字段」日志正常，如重装段 10:00:44「编辑字段: 登录名」——缺口仅在 create 填充路径）。
- **方案**：对比 2e8a340 前后 create 填充路径的日志调用点（批五某改动把 create 段的 fill 日志吞了或改了路径），恢复 LOG.info（与 generic 路径同签名「编辑字段: <label> = <value>」）。
- **验证口径**：r9 创建段日志含「编辑字段: 名称 = test…」。
- **回归风险**：极低。

---

## 五、探针计划（r9 前置，全部只读）

| 编号 | 目标 | 方法 | 输出 |
|---|---|---|---|
| P-D1 | 重装二次确认层 DOM 结构（修复 2 前置） | 独立 Playwright 脚本：登录→打开重装弹窗→填字段→点确定→**dump** 新浮层结构（tag/role/class/id/按钮文本/可见性/DOM 关系），**不点 doubleConfirm-confirm**（不产业务数据），dump 完 Escape 退出 | 浮层结构清单 → 修复 2 候选集覆盖性核验 |
| P-D2 | 删除确认字段 placeholder 属性（修复 7 前置） | Playwright 打开删除弹窗 → dump「删除确认」input 的 placeholder 原文（含引号形态）、radio 删除方式选项、提交按钮 disabled 态 | placeholder 原文 → 引号正则用例 |
| P-D3 | marker 行定位正确性（修复 6 风险点） | 列表多行场景 dump `get_row_selector_by_marker` 返回值与 marker 实际行号 | nth-child(1) 依赖确认 |
| P-D4 | fill_rule 生成链定位（修复 8 前置） | rg `fill_rule` 赋值链（静态，无需真机） | 源头单点清单 |

P-D1/P-D2 真机探针与 r9 合并执行（r9 启动前先跑两个只读探针脚本，取证后立即进入 Stage 1，避免重复登录与滑块挑战）。

---

## 六、r9 验收口径

修复 1-7 落地后重跑 r9（同环境同模块「云主机-云主机」，--stage 1 --force）：

1. **顺序**：删除操作位于 ordered_actions 末尾（所有行级操作之后）；「进入运维模式 → 退出运维模式」级联对完整执行且退出在删除之前。
2. **删除**：确认词填 DELETE（非随机值）→ 提交按钮 enabled → 删除业务成功——口径④首次通过。
3. **重装操作系统**：主提交 → 二次确认自动识别点击 → 成功判✓；dialog_still_open 类失败清零。
4. **串扰**：无「field=<他操作字段> text=<本操作>」错位失败；批内操作间无弹窗残留传递（「弹窗归属不匹配」0 次、「强制隐藏」仅出现在主循环 transition 正常位置）。
5. **重复执行**：修改Hostname 1 次成功；固有失败操作（重置密码/更新密钥对/克隆）跨域总尝试 ≤ 3 次；执行尝试总数 ≤ 45（r8 为 58）；总时长 ≤ 25 分钟（r7b 基线 16 分钟 + 新增退出运维模式/删除成功等操作的自然增量）。
6. **终态**：VM 终态恢复正常态（非运维模式）——F13 清零；ui.json `terminal_failed_operations` 字段存在且与环境固有失败清单一致。
7. **产物**：创建 fill_data 数字字段为数字、密码字段为密码规则、playbook fill_rule 按类型正确；重装操作 playbook 含 click_double_confirm 步骤（宪法三阶段一致性）；删除操作 fill_form fields 确认词字段值=DELETE。
8. **不回归**：r8 已成功的 20 个操作全部保持成功；已成功跳过收敛、F7 稳态、F8 三通道、RECONCILE 恢复能力（click_failed 过渡窗恢复照常）全部不回归；全量测试 exit 0。

观察项（非门禁）：D8 标签管理 / D10 网络配置的 click_failed 在修复 6a 清扫后是否自愈（菜单渲染完整）；E12 日志恢复。

---

## 七、实施顺序与排期建议

- **顺序**：修复 4（极低风险热修）→ 修复 1（P0 顺序安全）→ 修复 3（P0 串扰）→ 修复 2（P0 二次确认，含探针 P-D1 前置）→ 修复 5（P1 失败记忆）→ 修复 6（P1 扫描清扫+切换对）→ 修复 7（P1 确认词，含 P-D2）→ 修复 8/9（P2，含 P-D4）。
- **理由**：4/1/3 是独立小改，先行合入降低后续大改的回归面；2 与 5 有耦合（守卫 B 豁免后 dialog_still_open 减少，失败记忆白名单更干净），2 先 5 后；6 依赖 1（新收集项角色判定正确）；7 独立；8/9 收尾。
- **一批实施 + r9 一次验收**（同批五节奏）；修复 8 的 number 规则若 r9 首跑暴露 EIS 数字字段兼容问题，允许单独回退该项不阻塞整批。
- **建议提交拆分**：修复 4+1+3 一个提交（执行正确性）；修复 2+7 一个提交（提交链完整性，均含词表单源）；修复 5+6 一个提交（调度完整性）；修复 8+9 一个提交（产物质量）。每提交附单测与 rg 特征清单，沿用批五监控/接管工作流。

---

## 附：本方案证据索引（供实施与 r9 对账）

- r8 日志：`workspace/eis/output/eis_s1_vm_r8.err.log`（关键行：L167 F2-RECOLLECT 删除 generic 位置1、L285/2775 更新密钥对 required_field_empty、L2801-2852 串扰错位、L4939-4978 重装二次确认误杀、L5191-5231 扫描遮蔽+切换对盲区、L5287 Cascade-Order）
- r8 产物：`kb/module_discovered/云主机-云主机_ui.json`（删除 confirm_word_missing 详情、fill_data 污染）、`云主机-云主机_playbook.json`（fill_rule 错标）
- 代码定位：crud_executor L1418/L1316/L2026（delete 角色链）、L3783-3797+L3886（dialog_detected）、L5354/L5888（required_field_empty 清理点）、L836/L1038（批/reconcile 无清扫）、L1907→L1922→L1939→L2049（成功链顺序）；button_detector L718-784（_infer_action_role）、L337（_close_dialog）；shared/button_clicker L375-385（DESTROY_KEYWORDS/is_destroy_text 单源）；transition_helpers L20-115（_dismiss_all_overlays 带验证）
- r8 报告：`tmp/r8_ops_report.md`（13 项问题分类）；r8 汇总统计：`tmp/analyze_r8.py` / `r8_analysis.txt`
