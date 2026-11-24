# EIS 云主机 批五修复方案（第 7 轮根因 → 源头修复设计）

- 基线：r7b 日志（eis_s1_vm_r7b.err.log）逐操作根因分析 + 探针 v1/v2/v3/v4/v5 实证；git 22cbc68（批一~四）+ 5bf019c（优先选项）。
- 性质：**只做方案不改代码**，用户确认后实施批五。
- 目标：第 8 轮验收指标 ④删除 ⑤navigation ⑦自清洁 + 4 项收集缺项操作覆盖 + 级联过渡窗 + 断言口径回正。
- **全部待定案点已定案**（v4：tmp/eis_probe_live4.json、v5：tmp/eis_probe_live5.json）：P1b/P3b/P3c 清零，结论已并入各 F 项。
- **环境现状**：test500629 已由 v4 探针恢复正常态（删除菜单项 enabled）——第 8 轮删除靶点直接可用。

---

## 1. 证据基线（全部已定案）

| 编号 | 结论 | 证据 |
|---|---|---|
| A | 级联扫描成败都写 executed_actions，而 `_collect_state_pending` 把 executed_actions 当「已业务成功」排除——一个集合两种语义 | crud_executor L835 无条件 `executed_actions.add(text)`；L724 排除；时间线：级联#1 关机✓(L637) 后 Stopping 窗内 10 项级联连败(L640-L1108)全入册 → reconcile#1(L1110) pending 只剩 4 项 |
| B | 4 项缺失是**三重复合机制**：①收集展开全页第一个可见 dd（永远第一行，marker 行轮不到）②第一行是他人 VM 状态随机，4 项「需开机」操作（更新密钥对/Console/规格变更/冷迁移）在非 Running 态全 disabled 被 L1310-1314 过滤 ③DOM click 不触发 hover 菜单，vue_show 兜底渲染不完整 | v5 真实收集路径复现：展开的是第一行 ppp 的菜单仅收 6 项（marker test500629 在 idx 3），r7b 缺的 4 项在 6 项中 4/4 全缺（完美吻合）；「等待下拉菜单超时」实证 DOM click 失败走兜底；v4 hover 手势 marker 行 22 项全量（Running 态 3 项 DIS 均可见 offW=127） |
| C | 「已进入运维模式」消息 DOM 类是 `el-message el-message--success`（样式类携带语义，可作补充标记），但 message_capture JS 仅按文本词表分类且**忽略 className**（无「成功」词 → 误判 info）；verifier 再按 const 词表判 neutral → no_signal 判败 | v4 探针 DOM dump 实证 success 样式类；message_capture L87-89（isSuccess 仅查文本词表，忽略 className；L95 其实已捕获 className 字段）；日志 L2669-2676 type=info；success_verifier L93/132/168/196 `.el-message` 选择器齐全（选择器无缺陷） |
| D | 删除确认输入框被填随机值（placeholder 未读取）；「确定」按钮 disabled=true 被 js 点击静默跳过；弹窗滞留正文警示被 observer 捕获当错误信号 | 日志 L1215/L1256 删除确认=test500861（name_pattern 值）；v2 探针实证 placeholder=「请输入“DELETE”确认删除」+ 确定 disabled=true 直到填词；日志 L1218 `submit_form_v2 成功:'确定'(strategy=js)`；message_capture L141-145 Dialog 节点文本入捕获通道 + const L310「不可」命中警示文 |
| E | 10 个「navigation」操作 = 10 台存量 VM 的名称链接，在 marker 行内找「别的 VM 名」按钮——语义互斥必然失败 | 失败证据 JSON：wt-test5/test-vm/shif 等 10 个存量 VM 名 + `未找到按钮 'wt-test5' (行索引=0)` |
| F | 修改Hostname 走 dropdown 位置但 `_do_edit` 只有 row_action/toolbar 两分支，dropdown 落入全局按钮点击 | crud_executor L3268/L3287（else 分支 `click_page_button`） |
| G | 行定位全程「固定列索引 0」是环境巧合（test500629 恰排第一行），多用户列表漂移即跨行错点 | 日志 158 次行定位全为索引 0；v2 探针两次点到 clone01/clone02 行；v3 探针实证搜索框 fill+Enter 可过滤至单行 |
| H | 断言逻辑现状「无信号→失败」是 d2f777e（2026-10-14「stage1结果误判」）矫枉过正，用户口径四规则中「中性→等待→默认成功」被丢 | success_verifier L509-516；git 考古 d2f777e |
| I | 级联批量执行发生在 Stopping 过渡窗（关机✓后 1.5s 递归），未做稳态等待 | L841 `wait_for_timeout(1500)` 后直接递归执行 |
| J | 进入/退出运维模式的确认框是 el-dialog（无表单、title 为空、footer 取消/确定），确认点击实际成功、业务实际成功——r7b 判失败纯因消息误分类 | v4 探针：msgBox=0、dialog=1、dialogHasForm=false、cands=['取消','确定']；探针全程业务成功 |
| K | 菜单互斥对文本随行状态翻转：正常态「进入运维模式」/运维态「退出运维模式」；删除在运维态 disabled、正常态 enabled；菜单恒 22 项 | v4 探针翻转前后 dump；「收集不锁行」下互斥对文本必然取自错误行（Gap B 复合机制第 ①③ 环的语义后果） |

---

## 2. 修复设计

### F1 级联仅成功入册（Gap A 主修复）

**现状**：`_check_and_execute_newly_enabled` L826-835 —— `_error_driven_retry` 返回后**无条件** `executed_actions.add(text)`，成败都入册。`_collect_state_pending` L712-742 把 executed_actions 视作「已业务成功」（L724 排除）。

**修改设计**：L835 移入成功分支——

```python
if result and result.get("success"):
    executed_actions.add(text)          # 仅业务成功占用“业务身份”
    # （递归级联子检查保持原位）
```

失败级联不再占用业务身份 → 下一轮 reconcile 的 `_collect_state_pending` 可回收（前提其失败签名属 state_blocked：skipped/禁用、click_failed——L735-738 口径不变，非状态类失败仍不回收，防无限重试噪音）。

**防回归**：
- L808 递归防重复守卫语义不变（成功项仍跳过；失败项在**同一轮递归**中不会重复执行——递归仅发生在成功路径 L842，失败项当轮自然终止）。
- 跨轮重试上限由既有 R2（边耗尽）/R3（业务唯一无进展终止）/12 轮挂死保险兜底，无新风险。
- 反证用例保护：创建镜像类级联失败但主循环槽位在后的操作最终成功的既有行为不变（主循环不查 executed_actions）。

**单测**（tests/test_batch5_fixes.py，下同）：
- 级联失败后 `_collect_state_pending` 能回收该项（mock validated 含 state_blocked 失败签名 + executed_actions 不含）。
- 级联成功后仍被排除（回归既有语义）。
- 级联失败项不进入下一轮 cascade 去重跳过（L808 断言不 skip）。

**第 8 轮靶点**：关机✓后的 Stopped 态级联项（重启/暂停等）经 reconcile 轮恢复执行成功；r7b 的 reconcile#1 pending=4 → 期望 ≥10。

### F2 收集锁定 marker 行 + 全量化 + 搜索先行行定位（Gap B 主修复）

**现状（三重复合机制，v5 真实收集路径复现定案）**：
1. **收集不锁行**：`_discover_dropdowns`（element_scanner L1219）触发器按文本匹配**全页面第一个可见 .el-dropdown**（L1342 candidates 全页扫描，精确 === + 前缀 startsWith）——EIS 每行都有「更多」dd → **永远展开第一行**。v5 实证：展开的是第一行 ppp 的菜单，marker 行 test500629 在 idx 3 根本轮不到。多用户环境第一行是谁的 VM、什么状态完全随机。
2. **disabled 过滤**：`_collect_js` L1300-1320 可见 + **Vue disabled prop 过滤**（L1310-1314）。第一行处于非 Running 态时，4 项「需开机」操作（更新密钥对/Console/规格变更/冷迁移）全部 disabled → 全被滤掉。v5 收集到的 6 项中，r7b 缺失的 4 项 **4/4 全缺**——完美吻合。
3. **展开手势缺陷**：DOM click（L1272 el.click()）不触发 hover 菜单（v5「等待下拉菜单超时」实证），vue_show 兜底展开的菜单渲染不完整（6 项 vs v4 hover 手势 marker 行真实 22 项）——收集路径在 hover 系统上先天不可靠。
4. **互斥对语义后果**（证据 K）：「进入/退出运维模式」文本随行状态翻转，从第一行收集到的互斥对文本不反映 marker 行真实状态——未来重跑若 marker 行处于非正常态（上轮残留），将收集到错误行状态的菜单文本，执行时菜单项找不到。

**修改设计**（四个子件）：
1. **收集锁 marker 行**：`_discover_dropdowns` 增加 `marker: str = None` 参数——marker 存在时，dd 候选限定在 marker 行内（先按 F2-4 搜索锁定 marker 行，再在该行 DOM 内 `row.querySelectorAll('.el-dropdown')`），展开手势改为**行内「更多」坐标 hover 点击**（与运行时 click_row_more_item 同源手势，v4 实证 hover 手势下 22 项全量渲染）；无 marker 时（非模块化场景）保持旧行为不变。调用点（crud_executor 收集链）传入 created_marker。
2. **收集删 disabled 过滤**：删除 L1310-1314 过滤分支，保留可见性（offW>0）与导航区过滤。**收集层只回答「页面上有什么」，不回答「现在能不能点」**——可用性判定归执行层（点击前 disabled 检测 skip + 状态矩阵 + reconcile/cascade 状态翻转恢复）。v4 实证 disabled 项 offW=127 可见可收。
3. **搜索先行行定位**（button_driver `find_data_row` 前置）——用 v3/v5 对照定案的**可靠链路**：placeholder 关键词（搜索/关键字/属性值）定位搜索框 → **Playwright locator click 聚焦** → `fill(marker)` → **在 input 上** `press("Enter")`（v5 失败教训：JS setter 无焦点 + keyboard Enter 落 body 无效；v3 成功链路实证）→ 等表格刷新（行数变化）→ 过滤后行集合中含 marker → 用过滤后行。搜索未生效/无搜索框 → 走原扫描路径（完整保留，非兜底补丁而是条件化主路径）。模块收尾清空搜索恢复列表（自清洁）。
4. **删除验证复用**：删除后以 marker 搜索 → 0 行 = 删除成功实证（⑦自清洁验证口径）。

**estack 回归评估**：黑白名单可操作互斥项（Vue prop false + cmss-disabled class）本就因 prop 过滤被保留，删过滤后行为不变；hover 菜单隐藏项 offW=0 仍被可见性过滤；estack 收集多收若干 disabled 项 → 执行期 skip → cascade/reconcile 状态翻转后恢复——正是状态机设计意图。锁 marker 行在单用户单行场景等价（marker 行即第一行）。

**单测**：
- `_discover_dropdowns` marker 锁行：mock 页面多行，收集只展开 marker 行 dd；无 marker 走全页旧逻辑。
- `_collect_js` 删过滤：disabled+可见项入册。
- `find_data_row` 搜索先行：有搜索框先过滤再定位；搜索无效回退原扫描；无 marker 零行为变化（query 类）。

**第 8 轮靶点**：4 项（更新密钥对/Console/规格变更/冷迁移）入册被主循环尝试（成功或禁用 skip 后 reconcile 恢复）；行定位日志不再出现「固定列索引 0」依赖；收集日志显示展开 marker 行菜单 22 项。**实施后须用真实收集路径复验 22/22**（vue_show 渲染不完整之谜的修复验证）。

### F3 删除确认按 placeholder 填值 + disabled 提交守卫 + 弹窗关闭验证

**现状**：删除弹窗带表单走 `scan_form_fields → generate_fill_data → submit_form_v2`，绕过 `_preprocess_confirm_form`（批三只挂在 confirm_dialog 路径，button_driver L1525-1537 调用点）；`generate_fill_value`（fill_helpers L22）签名无 placeholder 参数；「删除确认」落 name_pattern 随机值。且「确定」disabled=true 时 js 点击无异常返回 → 记「提交成功」但弹窗未关（证据 D 全链）。

**修改设计**（三个子件，fill_helpers/button_driver 单源共享，Stage 1/2 自动一致）：
1. **placeholder 语义进值生成器**：const.py 新增单源正则 `PLACEHOLDER_CONFIRM_PATTERN`（与 `_preprocess_confirm_form` JS L1399 同构：引号包裹 1-40 字符词）；`generate_fill_value` 增加 `placeholder: str = ""` 参数，**优先级最高**（先于 input_type 分支）：命中 → 返回 `(提取词, {"rule": "placeholder_confirm", "params": {"placeholder_hint": placeholder}})`；`generate_fill_data` L231 调用处传 `field.get("placeholder","")`（scan_form_fields 已采集 placeholder——element_scanner L1530/1537 实证字段带该键）。判据即「placeholder 含引号包裹词」，无需 label 含「确认」的启发式。
2. **Stage 2 回放同源**：`apply_fill_rule` / `generate_fill_rules` 增加 `placeholder_confirm` 分支——回放时从同一弹窗的 placeholder 重新提取（弹窗同源必得同一词），fill_rule 落盘保三阶段一致性。
3. **提交守卫（submit_form_v2）**：点击前检查目标按钮 disabled（Vue prop 权威）→ disabled 则不点击，落终态 `confirm_word_missing`（与批四 required_field_empty 同型）；点击后 500ms 复查弹窗是否关闭 → 仍开则判失败 `dialog_still_open`（禁止「js 点 disabled 静默成功」假象）。

**防回归**：estack 纯文本 message-box 路径不走本函数（其确认流在 `_preprocess_confirm_form`，无表单字段）；普通表单字段 placeholder 无引号包裹词 → 行为逐字节不变。

**单测**：placeholder 命中/不命中两分支值生成；apply_fill_rule placeholder_confirm 重建；submit 守卫 disabled 不点击 + 弹窗未关判败。

**第 8 轮靶点**：删除 test500629 —— 「删除确认」填 DELETE（日志实证非随机值）、确定按钮 enabled 后真点击、弹窗关闭、成功 toast、行消失（搜索复核 0 行）。

### F4 名称链接 navigation 收敛（10 条 → 1 条语义操作）

**现状**：存量 VM 名称单元格链接被逐行收成 10 个操作（label=各 VM 名，role=navigation，location=row_action）；执行时 `click_row_button_v2(marker行, 'wt-test5')` 在 marker 行内找别的 VM 名 → 必然 not_found（证据 E）。

**修改设计**：
1. **收集收敛**：buttons_by_action 构建后（crud_executor L1176 处）归并——`_infer_action_role(text)==navigation` 且 text ∈ 当页各行名称列值集合 → 丢弃逐行条目，注册**单条**语义操作：action=「查看详情（名称链接）」，location=row_action，附 `row_link: true` 标记 + 语义定位策略（目标行名称单元格）。
2. **执行路由**：`_do_generic_operation` row_action 分支识别 `row_link` → 点击 marker 行名称单元格链接（driver 新助手 `click_row_name_link(row)`，取行内第一个可点名称元素——v2 探针实证名称链接点击跳 vmDetail）→ 交给 F6 URL 判定成功。
3. **操作数对账口径**：本轮基线 37 操作中 10 条将收敛为 1 条（37→28），第 8 轮操作数对账按此修正，**不得**按「操作数减少」误判回归。

**单测**：归并判据（navigation+行名→单条；toolbar 通用按钮不动）；`row_link` 执行路由。

**第 8 轮靶点**：原 10 条秒败操作收敛为 1 条且执行成功（URL 跳转 vmDetail 判定成功）。

### F5 `_do_edit` dropdown 路由 + pending 角色放宽

**现状**：`_do_edit`（crud_executor L3240）只有 row_action（L3268）/toolbar（L3287 两分支），修改Hostname location=dropdown 落入 else 全局 `click_page_button` → 必然失败；且 `_collect_state_pending` L728 只放 generic 角色，update 角色永无 reconcile 恢复机会。

**修改设计**：
1. `_do_edit` 增加 dropdown 分支：`btn_location == "dropdown"` 且 marker → `ButtonDriver.click_row_more_item`（打开 marker 行「更多」菜单点该项，内含 Vue disabled 检测——disabled 时返回 button-disabled skip，交状态机恢复）→ 弹出抽屉/弹窗后走既有编辑填充链（fill_edit_form→submit→verify，后半段复用现状代码）。
2. `_collect_state_pending` L728 放宽：`{generic, update}` 角色（update 仅限 row_action/dropdown 行级位置，L731 位置约束不变）。

**单测**：dropdown 分支路由（mock driver 调用链）；update 角色 pending 回收；toolbar 角色 update 仍不回收（既有语义）。

**第 8 轮靶点**：修改Hostname 成功（dropdown 菜单点开→抽屉表单→提交→成功消息）。

### F6 URL 跳转判成功（navigation/generic 统一口径）

**现状**：网络配置等点击后跳 vmDetail 详情页、无消息无抽屉 → no_signal 判败（v2 探针实证跳转形态）。

**修改设计**：`_verify_operation_success` 判定梯新增一层（挂 L509 无信号分支之前）：操作执行记录点击前 URL（`_do_generic_operation` 既有 url_before 透传），无任何消息信号但 `page.url != url_before` → 成功，`matched_method="navigation_url_change"`。与 F4 的 row_link、网络配置类 dropdown 项共用此口径。

**防回归**：仅当**完全无消息信号**时才看 URL（有失败/成功消息永远优先）；查询类 read_complete 既有成功口径不变。

**单测**：无消息+URL 变 → success；无消息+URL 不变+非查询 → no_signal 失败（保留）；有失败消息+URL 变 → 失败（消息优先）。

**第 8 轮靶点**：网络配置类操作成功（原 10 条 navigation 收敛后由 F4+F6 双口径覆盖）。

### F7 级联批量执行前稳态等待

**现状**：级联成功后 1.5s 即递归扫描执行（L841），关机✓后行处 Stopping 过渡窗，10 项级联对过渡态执行连败（证据 I）——失败本身被 F1 修复兜住，但会产生噪音轮 + 污染级联结果。

**修改设计**：`_check_and_execute_newly_enabled` 进入执行前调用既有 `_wait_row_states_stable`（双条件稳态：status 连续两次相同 + 菜单 enabled 集两次一致；超时 timed_out 不硬失败）：稳态 → 执行 newly_enabled；timed_out → **本轮放弃级联执行**（defer 给 reconcile，其自身已有 settle 逻辑），不执行任何项。

**防回归**：`_wait_row_states_stable` 为现成函数（reconcile L1001 已在用），无新等待逻辑；estack 零降级等价行为不受影响（其级联在稳态下秒过）。

**单测**：timed_out 时级联零执行；稳态时正常执行；无 state_ctx 时行为回退现状（不等待直接执行——兼容无状态上下文模块）。

**第 8 轮靶点**：关机✓后级联日志不再出现 Stopping 窗内连败 10 项的形态；转移边记录集中在稳态采集点。

### F8 断言逻辑回正四规则 + 词表覆盖 + 词表单源

**现状**：success_verifier L509-516 完全无信号一律判败（d2f777e 矫枉过正，证据 H）；const L301-304 成功词表无「已+动词」完成态措辞；message_capture JS **忽略 className 语义**（L87-89 仅文本词表分类，「已进入运维模式」DOM 明明是 el-message--success 却被判 info，证据 C）；词表双源——message_capture L81 JS 硬编码 5 词 vs const 12 词，语义漂移。

**修改设计**（判定梯重排，用户口径四规则为唯一权威——**文本词表保持主判定**，className 作为补充标记层，不取代规则 1/2）：
1. **规则 1（文本主判定）**：消息文本命中成功词表 → success（现状保留）。
2. **规则 2（文本主判定）**：消息文本命中失败词表 / 表单校验错误 → failure（现状保留，d2f777e 意图「填非法值不得标成功」由此层与规则 4 非查询失败侧共同保留）。**文本有结论时 className 不参与**——前端样式类存在误用可能（如把失败消息渲成 success 类），「失败/无法」等明确业务语言权威性高于样式类，冲突时文本赢。
3. **规则 3a（className 补充标记，v4 定案新增）**：文本中性（无成败词）但消息 className 带语义——`el-message--success` → success；`el-message--error` / `danger` → error → 直接判定。className 是前端显式声明的消息类型，本身就是一种「标记」（规则 3 的「有标记」范畴）。「已进入/已退出运维模式」在此层命中，无需等待窗。现状 message_capture 已捕获 className 字段（L95）但分类忽略它（L87-89 仅查文本词表）——修 classify 为 `(text, className)` 双输入。
4. **规则 3b（中性等待）**：文本中性 + className 中性（info/warning/纯 el-message）→ **等待 N ms（默认 2000，可配）后重读捕获**：出现成败信号（文本或 className）按上判；仍中性/无 → **默认成功**（`matched_method="neutral_wait_default_success"`）。
5. **规则 4（完全无消息）**：read_complete/查询类 → success（现状 L500 保留）；非查询类：URL 变化 → success（F6）；否则 → failure `no_signal`（现状保留，防填非法值假成功）。
- **词表扩充**（规则 1 成功词表增补）：已进入/已退出/已开启/已关闭/已停止/已启动/已切换/已挂载/已卸载/已绑定/已解绑/已下发——覆盖完成态措辞；className 与扩充词表双层兜住完成态消息。
- **词表单源**：const.py 为唯一权威；message_capture JS 的 `SUCCESS_KEYWORDS/ERROR_KEYWORDS` 改为构建时注入（模板占位符 `__SUCCESS_KEYWORDS_JSON__` 替换为 `json.dumps(sorted(const.NOTIFICATION_SUCCESS_KEYWORDS))`，避免整段 JS 转 f-string 的花括号转义风险），classify_notification 与 JS 分类同源。
- **风险声明**（用户已拍板接受）：规则 3b 等待窗内迟到错误 toast（>2s 异步失败）可能被默认成功；等待窗参数进 profile 可调。

**单测**：判定梯分层命中——文本成功词+className error 冲突时文本赢（成功）；文本失败词+className success 冲突时文本赢（失败）；文本中性+className success → 成功（「已进入运维模式」用例）；文本中性+className error → 失败；双中性 → 等待默认成功；等待后复查有失败消息按失败判；完全无消息按查询/非查询分判。

**第 8 轮靶点**：进入/退出运维模式在规则 3a 直接判成功；全轮 no_signal 假失败清零（真实无信号非查询操作仍按规则 4 正确判败）。

### F9 消息源收紧：dialog 正文出判定通道

**现状**：message_capture observer **有意**捕获 Dialog 节点文本入消息通道（L141-145「Dialog 节点：只捕获文本」）→ 删除弹窗滞留时正文警示文（含「不可恢复」命中 const L310「不可」）被当失败信号——判定碰巧对、错误文本误导诊断；更危险的是**前序弹窗滞留会污染后续操作的判定**（证据 D 第三环）。

**修改设计**：
1. `_pushCaptured` 对 dialog 来源文本改标 `type='dialog_context'`（与 toast 的 success/error/info 分离）；`_verify_operation_success` / `assert_success` 判定**只消费 toast/notification 分类消息**，dialog_context 仅落 debug 字段不进判定。
2. 弹窗滞留检测独立成信号（F3 子件 3 的 `dialog_still_open`）——「弹窗还在」本身就是提交未生效的**正面失败证据**，不再需要从正文文本猜。
3. estack message-box 纯确认路径回归评估：其确认框正文「此操作将删除…」本不该参与判定（判定靠后续成功/失败 toast），移出判定通道后行为更正确而非退化。

**单测**：dialog_context 不影响判定（有 dialog_context + 无 toast → 按规则 4 而非失败）；dialog_still_open 判败（与 F3 联测）。

**第 8 轮靶点**：删除操作失败场景（若再发生）错误文本不再是弹窗警示文；无前序弹窗污染导致的假失败。

---

## 3. 探针定案结论（原待定案点，全部清零）

| 项 | 原问题 | 定案结论（产物） |
|---|---|---|
| P1b | 未搜索首行菜单 vs marker 行菜单（4 项缺失机制） | **三重复合机制**（证据 B）：收集不锁行（展开第一行 ppp，marker 在 idx 3 轮不到）+ 第一行非 Running 态致 4 项全 disabled 被过滤（v5 收集 6 项中 4/4 全缺）+ DOM click 不触发 hover 菜单、vue_show 渲染不完整（6 vs 22）。产物 eis_probe_live5.json |
| P3b | 进入运维模式 message DOM 形态与确认框形态 | 消息 = `.el-message.el-message--success`（**样式类即权威 success 信号**，r7b 误判 info 是 JS 文本词表分类忽略 className）；确认框 = el-dialog 无表单（非 message-box），取消/确定可点，业务实际成功。产物 eis_probe_live4.json |
| P3c | 退出运维模式恢复与菜单翻转 | 「已退出运维模式」同 success 样式类；运维态 = 退出项可见 + 删除 disabled，正常态 = 进入项 + 删除 enabled，菜单恒 22 项（互斥对文本随行状态翻转，证据 K）。**探针结束已恢复正常态** |
| 附 | 搜索链路可靠性 | v3 成功 = placeholder 定位 + **locator click 聚焦** + Playwright fill + **input 上** press Enter；v5 失败 = JS setter 无焦点 + Enter 落 body。F2-3 实现按 v3 链路 |

遗留观察项（不阻塞实施）：vue_show 兜底展开后菜单仅渲染 6 项的精确成因（渲染时机 vs 行状态）——F2 锁行 + hover 手势后收集不再依赖 vue_show 路径，实施后以真实收集路径复验 22/22 即终裁。

---

## 4. 实施顺序与回归计划

**实施顺序**（每步单测绿后进下一步，最后一次性全量回归）：
1. F8（判定核心，其余项的成败判定都依赖判定梯）+ F9（消息源，F8 的输入侧）
2. F3（删除链路：placeholder + 守卫 + 弹窗关闭）
3. F2（收集全量 + 搜索先行）
4. F1 + F7（级联语义 + 稳态等待，同域同批）
5. F4 + F6（navigation 收敛 + URL 口径，同域同批）
6. F5（dropdown 编辑路由 + pending 放宽）

**回归链**：
- 单测：tests/test_batch5_fixes.py 新增（各 F 的单测清单见上）+ 既有 737 用例全量回归 exit 0（含 estack 状态机域、io_helpers、词表相关）。
- ui/lib 同步副本随 `_sync_ui_runtime_lib` 常规同步（button_driver/form_filler/success_verifier 改动涉及）。
- git 提交：仅本会话批五改动；沿用 `git commit -F tmp/commitmsg.txt`（python 读入防 PowerShell 编码污染）。

**第 8 轮执行前置**：
1. ~~退出运维模式~~ **已完成**（v4 探针结束态即正常态，删除菜单项 enabled）——test500629 删除靶点直接可用。
2. 清点存量 VM 数量（影响 F4 收敛后操作数对账：本轮 10 条→1 条；v5 观测环境仍在多用户共用中，行列表持续变化）。

**第 8 轮验收口径**（对齐七项指标中缺口项）：
- ④删除：test500629 删除成功（placeholder 填 DELETE + 弹窗关 + 行消失搜索复核）。
- ⑤navigation：收敛后单条名称链接操作成功（URL 跳转判定）；网络配置类操作成功。
- ⑦自清洁：删除后环境零残留（搜索 0 行）；搜索框清空。
- 新增：4 项收集缺项操作被尝试且 reconcile 可恢复；进入/退出运维模式判成功；级联过渡窗连败形态消失；操作数按 37→28 口径对账；no_signal 假失败清零。

---

## 5. 与第 7 轮初版报告的差异说明

docs/debug/eis云主机第7轮逐操作根因分析.md 的 F1-F9 初稿中，以下结论已被探针/代码核实**修正**，本文档为准：
- 删除方案：初稿按「message-box 引号提取扩到 dialog 路径」笼统表述 → 实为 `generate_fill_value` 值生成层缺 placeholder 语义 + js 点 disabled 按钮静默成功 + 弹窗正文污染判定三层叠加（证据 D）。
- navigation 方案：初稿未定位「10 条=存量 VM 名称链接」的收集层根因 → F4 收敛为语义操作（证据 E）。
- 断言方案：初稿笼统「恢复无信号成功」→ 按用户四规则重排判定梯并保留 d2f777e 意图（非查询无信号仍失败，防填非法值假成功）。
- F7 级联稳态：初稿未定位 L841 递归时序 → 现明确为级联批量执行前缺 `_wait_row_states_stable`（证据 I）。
- Gap B 机制：初稿（含本文档第一版）按「disabled 过滤单一机制」推 → v5 真实收集路径复现定案为**三重复合机制**（不锁行 + 过滤 + hover 手势缺陷），F2 设计由「删过滤+搜索」升级为「锁 marker 行 + 删过滤 + hover 手势 + 搜索先行」四子件。
- F8 权威信号：初稿按「文本词表扩充」为主 → v4 探针发现样式类带语义（el-message--success）；第一版曾误设为 className 优先、文本降兜底，经用户纠正回正：**文本词表保持规则 1/2 主判定**，className 仅作为规则 3 内的补充标记层（文本中性时才参考，冲突时文本赢）。
