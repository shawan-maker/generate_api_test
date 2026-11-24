# EIS 云主机 第 7 轮 Stage 1 逐操作根因分析（r7b）

> 范围：r7b 运行（`workspace/eis/output/eis_s1_vm_r7b.err.log`）37 个 validated 操作的逐项根因与源头修复方案。
> 性质：只读分析报告，未改任何生产代码；修复项待项目所有者确认批次后实施。
> 证据口径：日志行号 = err.log 行号；代码行号 = core/discovery/operation_executor/crud_executor.py（另注明者除外）。

## 一、总览

| 指标 | 值 | 对照第 6 轮 |
| --- | --- | --- |
| 操作总数 | 37 | 33 |
| 成功 | 11 | 5 |
| 整轮耗时 | ~16 分钟 | ~50 分钟（-68%，指标⑥✓） |
| 状态矩阵 | 6 态 + 7 转移边（指标②✓） | 3 态 |
| STATE-RECONCILE | 26 行、4 轮触发、恢复 6 操作、无乒乓（指标③✓） | — |
| 删除 | ✗（指标④✗） | ✗ |
| navigation | 10 个秒败（指标⑤✗） | 同 |
| 环境自清洁 | ✗（指标⑦✗，test500629 留存） | — |

成功 11 项的来源分解（状态机三通道全跑通）：

| 通道 | 操作 | 证据 |
| --- | --- | --- |
| 主循环 | 创建云主机（marker=test500629）、query、创建快照、创建镜像、修改别名 | 各主窗 success |
| 级联 [STATE-CHANGE] | 关机 | 日志 L637「✓ 关机 执行成功」14:13:28（主循环 14:12:11 失败后由级联恢复） |
| reconcile #3 | 暂停、硬重启 | L1989/L2036、L2039/L2087（Running 态 R1 直接执行） |
| reconcile #4 | 硬关机、开机、恢复（+恢复转移 暂停 Running→Paused） | L2718/L2767、L2770/L2816、L2869/L2916 |

## 二、状态机运行轨迹（时间线重建）

- 14:11:40 [STATE-INIT] 预扫描；14:12:05-14:12:39 主循环第一批：开机/关机/重启/暂停/恢复/重装操作系统/重置密码 全部撞「按钮被禁用」（skipped，行刚创建的状态窗口）；创建快照 ✓（14:12:39）。
- 14:13:08 级联 #1 检测 11 个新启用按钮，**关机 ✓（14:13:28）后行进入关机过渡窗，其余 10 个级联尝试全部撞「按钮被禁用」**（重启/热迁移/重置密码/更新密钥对/修改Hostname/克隆/创建镜像/系统盘扩容/Console/执行命令，L640-L1108）。
- 关机过渡窗持续约 3 分钟（全菜单禁用）：reconcile #1（14:14:04）、#2（14:16:09）各 6 秒空转——pending 里开机/恢复/暂停均未启用、无可用恢复边即断（L1111/L1631）。
- ~14:16:20 行状态恢复（旁证：创建镜像 14:16:19 仍禁用、14:16:21 弹窗成功；此后 14:17:41 reconcile 读数 Running——环境侧行为：关机未持续/回退，状态机如实跟踪）。
- reconcile #3（14:17:34，触发=创建镜像）：暂停 ✓、硬重启 ✓、硬关机 ✗（重启过渡窗禁用）；reconcile #4（14:23:15，主循环收尾）：硬关机 ✓（Running→Stopped）→ 开机 ✓ → 恢复转移 暂停 ✓（Running→Paused）→ 恢复 ✓；剩余 pending 2 个（标签管理/进入运维模式）。

## 三、失败 26 项逐类根因

### 3.1 navigation 10 项（click_failed，秒败，~0.5s/项）

- 操作：wt-test5/wt-test4/wt-test3/test-wt2/test-vm/wmk-nc/p6cvm120608/shif/vmauto7829/test-source（全部为**存量 VM 行名链接**）。
- 证据：`[button_driver] 未找到按钮 'wt-test5' (行索引=0)`——button_driver 按 marker 定位新建行后，在**该行内**找其他 VM 名的按钮文本，语义必然失败。
- 定性：既有收集语义缺陷（非本轮回归）；名链接点击跳详情的能力用 marker 行自己的名链接验证一次即可，10 个存量行操作是重复冗余。
- 源头方案 **F4（收集期收敛）**：名链接类 navigation 按钮仅收集 1 个代表（或仅 marker 行名链接），存量行不生成操作。预期主循环操作数 37→~28，整轮耗时再降；不采用执行期全局定位方案（会真跳存量 VM 详情页，污染大）。

### 3.2 skipped 10 项 —— 本轮最重要发现：pending 覆盖缺口两层根因

10 项（重启/重装操作系统/重置密码/热迁移/更新密钥对/克隆/系统盘扩容/Console/执行命令/规格变更）全部因「按钮被禁用（cmss-disabled/is-disabled）」跳过（button_driver L997-999 disabled 真值口径），本身判定**正确**；问题在于失败后**没有任何恢复通道**。

**Gap A（6+1 项：级联失败占用 executed_actions）**

- 机制链：级联扫描 `_check_and_execute_newly_enabled` 对新启用按钮逐个尝试后，**无论成败都 `executed_actions.add(text)`（L835）**；而 `_collect_state_pending`（L712-742）把 executed_actions 当作「已以业务身份成功过」的排除条件（L724-725，docstring L717 口径即「未以业务身份成功过」）。一个集合两种语义，级联失败操作两头落空：主循环槽位已过（重启/重置密码 14:12 已试）或从未执行，reconcile 又因在册而排除。
- 实证时间线（r7b）：级联 #1 在 14:13:33-14:14:04 失败 10 项 → 全部入 executed_actions → reconcile #1（14:14:04）pending 仅剩未被级联碰过的开机/恢复/暂停/重装操作系统 4 项。重装操作系统后又在级联 #2（14:17:31）失败入册 → 从 #3/#4 pending 消失。
- 对照案例：**创建镜像级联 #1 也失败（14:13:54）但最终成功**——因其主循环尝试（14:16:17）在级联失败**之后**才到来（主循环不受 executed_actions 门控，L1363 注释明确只跳过级联先行**成功**项）。这证明管线对「主循环槽位还在」的级联失败操作能自愈；9 个出局项的问题全在「槽位已过 + reconcile 被排除」。
- 反事实推演：L835 改为仅成功入册后，重启/热迁移/重置密码/克隆/系统盘扩容/执行命令 6 项会进 pending；reconcile #4 开机 ✓（行 Running）后 R1 会逐个执行（均为 Running 态操作，多数应可成功）。无乒乓风险：reconcile 自身 R3（每调用每业务一次）/R4（转移操作失败封边）已有界。
- 源头方案 **F1**：L835 改为 `if result and result.get("success"): executed_actions.add(text)`，与主循环（L1673/L1681）、reconcile（L1033/L1123）的「成功才入册」口径统一。级联去重门（L795/L808）语义随之变为「已成功不重复」，级联失败项在**真实 disabled→enabled 翻转**时可再次级联尝试——期望行为（与 reconcile 对主循环失败的 R1 重试等价）。需单测覆盖：级联失败不入册、状态再翻转再级联、成功后入册去重。

**Gap B（3 项：更新密钥对/Console/规格变更 + 冷迁移，收集侧缺位）**

- 证据：ui.json 收集清单 button_labels(35)/dropdowns(29)/row_actions(11)/toolbar_buttons(4) **均无这 4 项**；主循环 ordered_actions（L1257-1286，buttons_by_action 全集）从未尝试它们（r7b 无 main_op 窗口）；它们的 validated 结果全部来自级联合成 btn 兜底（L817-823 `buttons_by_action.get(text, {…合成…})`）+ L1712 级联结果 merge。execution_order 中 4 项 phase=cascade（triggered_by=创建快照/创建镜像）。
- 即便修了 Gap A，这 3 项仍进不了 pending：`_collect_state_pending` L726-727 要求 `action in buttons_by_action`。
- 源头方案 **F2（前置探针 P1）**：只读 DOM 探针 dump marker 行「更多」菜单全量项 vs 收集清单，定性缺位机制（疑点：菜单可视区滚动截断 / 嵌套子菜单 / 渲染时序 / 收集规则过滤）。按结论二选一：element_scanner 行级菜单枚举补全（滚动/等渲染后再枚举），或运行期兜底——级联实时菜单发现的未入册项回灌 buttons_by_action（增量收集），使主循环后续轮次与 reconcile 均可覆盖。

**逐项明细（skipped 10）**

| 操作 | 首次尝试 | 后续 | 出局原因 |
| --- | --- | --- | --- |
| 重启 | 主循环 14:12:15 skipped | 级联 #1 14:13:33 失败 | Gap A |
| 重置密码 | 主循环 14:12:34 skipped | 级联 #1 14:13:40 失败 | Gap A |
| 热迁移 | 级联 #1 14:13:37 失败 | 主循环 14:15:26 skipped | Gap A |
| 克隆 | 级联 #1 14:13:50 失败 | 主循环 14:15:11 skipped | Gap A |
| 执行命令 | 级联 #1 14:14:04 失败 | 主循环 14:15:06 skipped | Gap A |
| 系统盘扩容 | 级联 #1 14:13:57 失败 | 主循环 14:18:52 skipped | Gap A |
| 重装操作系统 | 主循环 14:12:29 skipped | 曾进 pending #1/#2；级联 #2 14:17:31 失败入册 → 出局 | Gap A |
| 更新密钥对 | 级联 #1 14:13:44 失败（唯一尝试） | 无 | Gap A + B |
| Console | 级联 #1 14:14:00 失败（唯一尝试） | 无 | Gap A + B |
| 规格变更 | 级联 #2 14:17:34 失败（唯一尝试） | 无 | Gap A + B |

### 3.3 删除（operation_failed，指标④）

- 证据（删除窗 L1180-1269）：弹窗「删除云主机test500629」正常打开，扫描 3 字段「删除方式/删除数据盘（选填）/删除确认」；两轮分别把**删除确认填成随机值 test500861/test500885**（L1215/L1256「编辑字段: 删除确认 = …」），提交『确定』（js）后弹窗滞留；success_verifier 把滞留弹窗的正文警示文案判为错误（L1221「✗ 弹窗中检测到错误: 删除云主机test5006291、云主机直接删除后…」）。
- 根因：**确认输入框未按其 placeholder 要求的内容填写**——批三 placeholder 引号关键词提取机制只挂 message-box 确认路径，删除是 dialog 表单路径未挂载；generate_fill_data 把「删除确认」当普通文本字段生成随机值，表单校验拒绝。
- 源头方案 **F3（项目所有者已定口径）**：dialog 表单路径挂载 placeholder 提示读取——扫描到确认类字段时读 input placeholder，按其要求内容生成填写值（如要求输入确认词则填确认词、要求输入名称则填 marker）；placeholder 无明确要求时保守留空并把字段标记为 required_placeholder_confirm，不得回退随机值。验证靶子：test500629（第 8 轮删除验证）。
- 附带（低优先级 **F9**）：弹窗滞留判失败的**结论正确**，但 error_text 取的是弹窗正文 info 文案（误导诊断）；收紧为优先取表单校验错误文本（输入框旁红色提示）。

### 3.4 修改Hostname（no_dialog → click_failed，update 角色）

- 证据（窗 L1546-1589）：第 1 轮「点击编辑后未出现编辑弹窗」（no_dialog），第 2/3 轮「无法点击编辑按钮」（click_failed）。
- 根因：尝试时点 14:15:31 处于关机过渡窗（同期 执行命令/克隆/热迁移 均报按钮被禁用），修改Hostname 菜单项不可用——点击无响应 → update 路径报 no_dialog；**update 路径点击前不读 disabled 真值**（generic 路径有 button-disabled 检测出口 L4565-4570，update 路径没有）；且 `_collect_state_pending` L728 只放行 generic 角色，update 角色的状态受阻操作**永远没有 reconcile 通道**。
- 源头方案 **F5**：① update 路径点击前读 disabled 真值（对齐 generic 的 vueDisabled 口径），disabled → skipped(按钮被禁用)；② `_collect_state_pending` 角色条件放宽到 update（location 条件已覆盖）。

### 3.5 网络配置（no_submit_button）

- 证据（窗 L2248-2625）：点击后无弹窗，14:19:11「创建按钮后状态: **URL变化=是**, DOM检测=none」；随后扫描器在详情页 tab（云硬盘/USB管理/性能监控/快照/远程命令）上空转 5×5s 超时。
- 根因：**网络配置是 URL 跳转型操作**（点击即跳页面/抽屉，无 dialog），被按 generic 弹窗语义处理——等弹窗超时；批三 3.1 的 navigation 截流只识别收集期判定为 navigation 角色的操作，网络配置被判定 generic。
- 源头方案 **F6**：generic 路径在预期弹窗未出现但 URL 已变化时按 navigation 口径收尾（跳转发生即验证成功，go_back 回列表继续）；或前置探针 P1 顺带核对该按钮真实形态后，在角色判定阶段归 navigation。

### 3.6 标签管理 / 进入运维模式 / 冷迁移（个案）

**标签管理（click_failed，pending 常驻从未 ready）**

- 证据（窗 L1271-1315）：click_row_more_item 展开菜单成功（visibleCount=21, totalItems=21, scopedToRow=True），但 `option_text='标签管理', visible=not_found`（L1290）——**marker 行实时菜单不含该项**；两轮同态。
- 它因 click_failed 签名进了 pending（#2/#3/#4 常驻）但 R1 永不 ready（states 扫描读实时菜单，21/22 项快照均无它）。retry 时点 14:23:12 菜单 22 项仍 not_found。
- 疑点（待探针 P1 定案）：该项从哪收集来的（dropdowns 有 trigger='标签管理'）——跨行菜单差异（收集自其他行的菜单）或状态隐藏或嵌套子菜单。
- 源头方案 **F2 附属**：探针 P1 dump marker 行菜单全量项 vs 收集清单后定案；若属跨行差异，行级菜单登记需绑行（按 marker 行枚举）或执行期对「在册但 marker 行菜单无此项」的操作返回 skipped(菜单项不适用) 而非 click_failed。

**进入运维模式（疑似假失败：no_signal → click_failed）**

- 证据（窗 L2626-2716）：点击成功（L2646 clicked=True）→ 出现空标题无表单 dialog（L2651/L2654）→ 回退确认框处理，confirm_dialog 点『确定』（L2668）→ 等待 16s 无任何 toast/信号 → no_signal 判失败（L2674-2676）；retry 时菜单已变（visibleCount 21→22）且「进入运维模式」not_found → click_failed ×2。
- 旁证：确认后菜单项集变化（21→22、进入运维模式消失）说明**操作很可能实际生效**（运维模式进入后菜单形态改变），与第 6 轮更新密钥对同类但机制不同（这次不是误点取消，是确认后无信号）。
- 源头方案 **F8（前置探针 P2）**：P2 实机确认进入运维模式的真实效果信号（行上标记/菜单项变化/接口调用）；确认后扩 no_signal 成功口径（候选：提交/确认后弹窗关闭、无错误信号、且行/菜单发生对应变化 → 判成功）。

**冷迁移（operation_failed/no_signal，级联 #2 唯一尝试，收集缺位 Gap B）**

- 证据：级联 #2 14:17:27「冷迁移 操作未成功: 未检测到明确的成功/失败信号」。冷迁移为长耗时异步操作，提交后无即时信号属预期形态；同 F8 的 no_signal 口径问题，P2 一并核实。

### 3.7 附带发现：reconcile #1/#2 空转（Stopping 过渡窗 + 无边即断）

- 证据：#1/#2 各 6 秒即「无可用恢复边，结束」（L1111/L1631）；期间行处关机过渡窗（全菜单禁用 ~3 分钟），pending 里开机等均未启用。
- 双条件稳态（L858-871）在「全禁用集合」上两连读相同即判稳——过渡期假稳；且矩阵无边以 Stopping 为 from（Stopping 期间无成功操作），立即 break。
- 定性：非正确性缺陷（#4 最终补上），效率/覆盖优化点。源头方案 **F7**：no-ready 且 no-edge 时若 status 属已观测过渡态（矩阵在册的 Stopping/Starting）或 states 全禁用，追加一至两轮 settle（有界等待）再判，而非立即 break。不动 settle 判稳本身。

## 四、源头修复方案汇总（批五候选，待确认）

| 编号 | 修复 | 位置 | 优先级 | 依赖 |
| --- | --- | --- | --- | --- |
| F1 | 级联仅成功入 executed_actions（Gap A 根治） | crud_executor L835 | P0 | 无 |
| F2 | 收集缺位补全（Gap B；含标签管理菜单项定性） | element_scanner / 级联回灌 buttons_by_action | P0 | 探针 P1 |
| F3 | 删除确认按 placeholder 提示填值（dialog 表单路径挂载） | form_filler/scan_fields | P0 | 无（P1 顺带取 placeholder 原文） |
| F4 | navigation 名链接收集期收敛（仅代表行） | 收集期 | P1 | 无 |
| F5 | update 路径 disabled 检测 + pending 角色放宽 | update 路径 + _collect_state_pending L728 | P1 | 无 |
| F6 | generic URL 跳转口径（网络配置类） | _do_generic_operation 无弹窗分支 | P1 | P1 探针形态确认 |
| F7 | reconcile 过渡窗追加有界等待 | _state_aware_reconcile break 分支 | P2 | 无 |
| F8 | no_signal 成功口径扩展（进入运维模式/冷迁移） | success_verifier | P2 | 探针 P2 |
| F9 | verifier 弹窗滞留错误取文本口径收紧 | success_verifier | P3 | 无 |

## 五、第 8 轮验证计划

1. **前置探针 P1（只读 DOM）**：marker 行「更多」菜单全量项 vs 收集清单（定性 Gap B 缺位机制 + 标签管理归属）；删除弹窗「删除确认」input 的 placeholder 原文；网络配置按钮点击后的真实形态（跳转目标/有无 drawer）。
2. **前置探针 P2（只读 DOM）**：进入运维模式确认后的真实效果信号（行标记/菜单变化/接口）；冷迁移提交后的信号形态。
3. 修复实施（按上表批次，等确认）→ 单测（级联入册语义/placeholder 确认/update disabled/navigation 收敛）→ 全量回归。
4. 第 8 轮 Stage 1 重跑：指标 ④删除（test500629 靶子）⑤navigation（应只剩 0-1 个代表项）⑦自清洁；重点观察 9 个 skipped 操作的恢复面（F1/F2 生效后应多数可成功）与 reconcile pending 覆盖（skipped/update 角色操作应入 pending）。
5. estack 四口径回归维持暂缓口径（静态影响论证在修改方案文档）。

## 六、产物与证据索引

- 运行日志：`workspace/eis/output/eis_s1_vm_r7b.err.log`（行号引用见正文）
- kb 产物：`workspace/eis/kb/module_discovered/云主机-云主机_ui.json`（validated_operations 37 / execution_order 26 / state_button_matrix 6 / state_transitions 7）
- 分析脚本（tmp/，可复跑）：`probe_r7_timeline.py`（事件时间线）、`probe_pending_gaps.py`（pending 条件复算）、`probe_four_gaps.py`（收集缺位核对）、`probe_collect_structure.py`（收集结构）、`probe_delete_window.py`（删除窗证据）、`probe_two_ops.py`（标签管理/进入运维模式窗）、`probe_final_matrix.py`（37 操作判定矩阵）
- 结构化产物（tmp/）：`eis_r7_timeline.json`、`eis_r7_op_analysis.json`、`eis_r7_fail_evidence.json`
