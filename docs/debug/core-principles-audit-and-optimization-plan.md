# 核心原则合规审计与优化方案（全链扫描报告）

- 扫描日期：2026-11-18
- 扫描范围：core/discovery 全部 46 个 py 文件 + projects/estack-10.22.187.121/v1.0.0/{ui,api}/lib 运行时副本
- 扫描方法：4 路并行静态扫描（三阶段一致性对比 / 定位器生成链审查 / 兼容回退与僵尸代码梳理 / API 捕获匹配原则核验），全部结论附文件:行号证据
- 产物状态基线：黑白名单 fix7 后 Stage1-5 全链通过（14/14、22/22、UI 14/14）

---

## 一、总体结论

| 核心原则 | 符合度 | 一句话结论 |
|---|---|---|
| 三阶段操作方法/断言/输入数据一致 | ★★★★☆ | 架构高度共享（shared/ + replay/ 单源，ui/lib 为同步副本零逻辑漂移），剩余 6 处双实现债务 |
| 元素定位（文本+相对路径+属性） | ★★☆☆☆ | 定位器生成源头存在 6 类违规，其中 `_buildSelector` 无深度上限 DOM path 是 `#app > ...` 9 层绝对路径的直接源头 |
| Stage2 只回放不做业务判断 | ★★★★★ | 严格回放模式 + interaction_steps 契约纪律良好，无记录时显式失败不重新探索 |
| API 捕获值匹配三原则 | ★★★★☆ | value_chain._trace_value 三原则完整（值匹配/ts 排序最早/递归 max_depth=6）；ValueIndex.lookup 评分制未用 timestamp 打破平局 |
| API 脚本依赖分析+数据生成区分 | ★★★★★ | 5 角色体系完整（name/mutable/context/generate/static），RSA 加密链路（manifest 公钥注入→helpers._rsa_encrypt→PKCS1v15）闭环 |

**最重要的架构事实**：Stage1（crud_executor）与 Stage2（replay_engine）是「共享层 + 两个编排层」而非平行两套；`shared/__init__.py:2-7` 明文声明「方法只定义一次，Stage 1/2 同时调用」并实际成立。core↔ui/lib 哈希差异经 git 规范化 diff 实证 **100% 为 import 前缀重写**（`from .. import` → `from . import`），零逻辑漂移——同步机制健康，**不存在 core↔ui/lib 双写风险**；真正的双写风险全部在 core 内部副本之间。

---

## 二、发现清单（按原则分组）

### 2.1 三阶段一致性（原则 1）

| 编号 | 级别 | 发现 | 证据 |
|---|---|---|---|
| C-1 | 高 | **switch 双实现且同文件内两种口径并存**：`crud_executor._do_toggle_switch`（L3009-3361）全程内联 JS（状态检测/点击/确认弹框/轮询），与 `shared/switch_ops.py`（get_switch_state L27 / toggle_switch L140）逐行雷同；`switch_ops.py:16` 注释声称「调用方: crud_executor._do_toggle_switch」但实际未 import——同文件 `_do_generic_operation`（L3832-3838）走共享版、主 switch 走内联复制版 | 一处改动两处维护的实锤；改 switch_ops 不会生效到主 switch 探测路径 |
| C-2 | 高 | **success_verifier 内两套通知关键词表**：策略 0 走 `const.py:283-299`（18 失败词+「0个失败」豁免正则），策略 0.5/1/3/F 用 JS 内嵌硬编码 6 词小词表（success_verifier.py:124/:156/:287/:350） | 同一操作换策略链路得到不同语义分类；const 词表更新不跟随 JS 侧 |
| C-3 | 中 | **回放兜底弱化版与探测完整版并存**：`replay_engine._fill_ip_input`（L816-885，仅支持 4 段 v4、IPv6 `replace("::",":")` 近似实现错误、区间只取第一段）vs `form_filler._parse_ip_segments`（L3980，v4/v6×4/8/16 段+区间）+ `_expand_ipv6`（L4028，zfill(4)）；`_fill_composite_input`（L888-963）同为简化版 | 正常路径走 interaction_steps 记录（`op=fill`）不受影响，兜底版只影响无记录旧 playbook——但两版 IPv6 语义冲突 |
| C-4 | 中 | **简单字段填充不对称**：探测 `form_filler.fill_create_form`（L410，无候选遍历/无 js_index/无填充验证）vs 回放 `_step_fill_form` simple 分支（replay_engine.py:1231-1399，fallback_locators 三重容错+填充后验证） | 回放版远强于探测版；修复只做一侧会导致「探测成功但记录的 locator 回放必失败」 |
| C-5 | 低 | **strict 死参数**：`success_verifier.verify_operation_success(strict: bool = False)`（L39）函数体从未使用；Stage1 四处传 `strict=True`（crud_executor.py:4973/:5099、navigation_executor.py:669/:1380）实为无效参数 | 假接口参数，制造「有严格模式」假象 |
| C-6 | 低 | **行内按钮 JS 兜底双份**：`row_interaction.py:199-226` 与 `button_driver.py:448-461` 的 btn.click() JS 兜底代码重复 | 同源双写 |
| C-7 | 低 | 回放端私有轻量断言 `_step_assert_table_has_data`（replay_engine.py:2024）/`_step_assert_content_visible`（:2037）不经 success_verifier | 步骤级断言未入共享链 |
| C-8 | 结构 | UI 脚本模板内嵌 `_cleanup_dialogs` 关闭选择器清单（generate_ui_script.py:487-493）与 dialog_helpers/button_driver 的关闭逻辑是独立第三份 | 模板渲染后每个 UI 脚本各自携带，无法靠 ui/lib 同步覆盖 |

**符合项**（不再赘述）：click_button/find_row/confirm_dialog/click_row_more/select_row_checkbox/等待策略均为单源共享；success_verifier 三方共用（UI 副本 diff=0）；mode 集合 `{message, read_complete, expected_failure}` 三阶段一致；TTL 30s 与 B2 残留基线机制单点实现；FormFiller 自古只有一份（Stage1 crud_executor.py:20-22 与 Stage2 replay_engine.py:21 同 import）；interaction_steps 契约（radio/tree-select/select/cascader/date-picker/transfer-box）双侧对齐且无记录时严格判失败。

### 2.2 元素定位合规（原则 2）

| 编号 | 级别 | 发现 | 证据 |
|---|---|---|---|
| L-1 | 🔴 | **`#app > ...` 9 层绝对路径的生成源头**：`form_filler.scan_form_fields` 的 JS `_buildSelector`（L1992-2035）while 循环**无深度上限**逐级爬 DOM 至 `#app`（Vue 页面中间节点无 id 不 break）；ip-input 的 subSelectors 同型（L1737-1762）。落盘链：`fields[].selector` → builder.py:1631-1637 "original" 候选 → playbook fallback_locators（builder.py:1619 注释自认「跨会话不稳定」仍落盘）；无 placeholder 且 label 为 fallback 时**直接成为主 locator**（builder.py:1660-1666） | 黑白名单编辑 op 实锤：`#app > div:nth-of-type(1) > section > ... > span:nth-of-type(1) > div` |
| L-2 | 🔴 | **switch 全局序号 + 语义错误**：element_scanner.py:928-936 用全页面级 `visibleSwitches.indexOf(sw)` 填充 `.el-switch:nth-of-type(N)`——nth-of-type 是父内序号，全局计数导致**多匹配**（每个父元素的第一个 switch 都命中）。落盘为 toggle_switch 主 locator | 生成即错误选择器 |
| L-3 | 🔴 | **`button:has-text('编辑')` 全局无 scope**：生成源头 crud_executor 8 处 `trigger_locator = f"{trigger_tag}:has-text('{text}')"`（L1792/1927/2257/2489/2957/3609/4001/5026）+ button_clicker.py:273/:321；builder.py:190/:349/:431/:493/:1834/:1850/:2010 的无 scope 回退落盘 | 页面多个同名按钮（行内/工具栏/弹窗）时回放点错对象 |
| L-4 | 🔴 | **xpath_index 编号落盘体系**：button_clicker.py:181-221 记录 KB XPath 模板序号 → builder 8 处落盘 trigger/submit_xpath_index → replay_engine.py:424-452 回放优先消费。KB patterns 列表顺序变化即失效；KB XPath 是全局文本匹配 | 编号定位+全局匹配双重问题 |
| L-5 | 🔴 | **`_buildComponentSelector` 的 `.el-form-item:nth-child(N)`**：element_scanner.py:1612/:1636 与 form_filler.py:2053 三处同型 | 父容器内位置编号，弹窗结构调整即失效 |
| L-6 | 🔴 | **tab 无 id 时 `[role="tab"]:nth-child(N)` 落盘**：element_scanner.py:1929-1932；回放端 replay_engine.py:1831 其实已有 tab_name 文本定位逻辑，nth-child 落盘是冗余的坏 fallback | 落盘坏 locator |
| L-7 | 🟡 | **全项目唯一坐标点击**：button_detector.py:619-686 `_retry_precondition` 第 3 兜底 `page.mouse.click(rect.x, rect.y)`。运行时即时计算不落盘、无漂移问题，但违反字面原则且与 JS click 兜底能力重叠 | 运行时兜底 |
| L-8 | 🟡 | radio 选项定位 `.el-radio-group .el-radio:has-text(...)` 缺 form-item scope（form_filler.py:470/566/636） | 两表单项相同选项文本时选错 |
| L-9 | 🟡 | anchor_helpers.py:164-166 `>> nth=` 是受控显式编号（经 validate_anchor_css 唯一性校验、降级链最后一环） | 设计最完善的违规形态，建议增补属性快照消歧 |

**合规参照**（改造模板）：anchor_helpers 体系（锚点容器内相对 text=、显式降级链、无全局兜底）、builder.py:1645-1648 的 dialog scope 候选、base_executor.py:511 的 title 约束。

### 2.3 API 捕获与脚本生成（原则 3、4）

| 编号 | 级别 | 发现 | 证据 |
|---|---|---|---|
| A-1 | 符合 | **value_chain._trace_value 三原则完整实现**：原则1 值匹配（L233-246 递归搜索所有前置响应）；原则2 时间最早（L253 `candidates.sort(key=lambda c: c["ts"])` 取首次产生者）；原则3 多级递归（L264-297 提取响应对应请求中的动态值递归追溯，max_depth=6，visited 防环） | 与用户三原则逐字对应；仅 docstring L219 写「时间最近」与实现矛盾（文档瑕疵） |
| A-2 | 偏差 | **ValueIndex.lookup（body 字段分类路径）评分制未用 timestamp**：score = 同操作窗 +100 / init +50 / 其他 +10 + 字段名 +30 + 深度 -10（field_classifier.py:261-278）；timestamp 已存入 entry 但不参与评分，同分平局按构建顺序取首 | 与「时间最早原则」不完全对齐——应将 timestamp 最早作为平局打破项 |
| A-3 | 符合 | 前置 API 收集泛化：非业务 API → 候选 → Stage3 `trace_pre_api_dependencies` 按业务请求数据反向追踪筛选（capture_apis.py:421-498）；`_generate_api_name` mapping（L548-558）硬编码仅影响展示名（fallback `前置 API: xxx`），不影响匹配逻辑 | 低危，可保留 |
| A-4 | 符合 | 依赖注入链完整（id / _path_xxx / blackWhiteListIds 数组 / __array_item__——含本轮 list-entity 修复）+ create-like 源排除防响应回显误判 | manifest steps+requires 闭环 |
| A-5 | 符合 | 测试数据生成区分完整：**随机值** gen_uuid/gen_phone/gen_email/gen_timestamp/gen_random_int/gen_text；**加密值** _rsa_encrypt（export_artifacts.py:797-809，manifest rsa_public_key 注入 L653、PKCS1v15）+ gen_encrypted_phone/email/password/text（按值 pattern 数据驱动识别：hex_hash/encrypted_*，field_classifier.py:21-51）；**固定值** static 原样保留；**关联值** context 从 state 解析 | 5 角色体系闭环 |

### 2.4 清理项 A：旧 playbook/旧文件兼容回退逻辑

**A-1 可直接删除（产物已验证全部为新格式，兼容分支永不触发）**：

| 位置 | 分支 | 证据 |
|---|---|---|
| replay/replay_engine.py:1241-1245 | fallback_locator 旧 str 格式兼容 | 全项目 playbook 均为 list 新格式（builder 只写 fallback_locators） |
| replay/replay_engine.py:1539-1544 | click_row_more_item 返回 dict/bool 双格式 | button_driver 签名即 dict，三个调用方均收 dict |
| version.py:59-67 | available_versions 的 scripts/、flows/ 目录回退 | 6 个项目均无此目录 |
| io_helpers.py:186-195 | modules.yaml group 缺失回退 | 3 份 modules.yaml 均含 group |
| stage_validators.py:29 | validate_stage1 required_flows 死参数 | 全部调用点不传 |
| required_elements.py:35 | check_required_elements flow_name 死参数（注释自标「保留参数以兼容旧接口」） | 唯一调用点传 None |
| playbook_builder/builder.py:1986-1994 | field_states 旧 playbook 补充循环 | 循环体仅 LOG.debug，纯 no-op |

**A-2 可删但需一行配套**：io_helpers.py:380-410 `get_module_group_index`（docstring 自标「已废弃：请优先使用 get_or_assign_module_index」）——内部已代理新函数，但 run.py:1617 仍调旧名。

**A-3 需用户确认**（涉及测试用例）：stage_validators.py:259-260/:279-280 的 crud_order/order、api_by_button/api_mapping 双字段名兼容（tests/test_module_discovery.py 用旧名构造数据）；manifest_builder.py:208 的无 value_index 内部构建兼容（2 个测试用例刻意覆盖）。

**A-4 应保留**（活逻辑非死兼容）：replay_engine.py:1754-1759 assert_mode 缺失显式报错守卫（estack-ceshiyu 旧 playbook 真实缺失该字段，属设计意图）；replay_engine.py:729 旧全局 KB 拒绝分支；base_executor 代理层 6 函数；element_scanner.py:1784-1807 JS 返回 list/dict 内部契约兼容；io_helpers.py:61 credentials 合并；capture_apis.py:43 ui_result 参数；generate_ui_script.py:695 旧 playbook 判定矩阵；manifest_builder.py:1762 双来源收集。

### 2.5 清理项 B：弃用方法/僵尸代码

**B-1 高置信僵尸（全项目零调用，已排除 __init__ 导出与动态调用）**：

| 位置 | 函数 |
|---|---|
| capture_apis.py:104 | `_cleanup_after_operation`（docstring 自标 Deprecated） |
| io_helpers.py:77 | `save_credentials` |
| skip_reason.py:45 | `classify_stage1_skip_reason`（同文件 classify_skip_reason 活跃） |
| ai_debug_assistant.py:413 | `clear_vision_cache` |
| const.py:371 | `get_button_selectors_str` |
| replay/locator_helpers.py:33/50/65/80 | `safe_xpath`/`click_safe`/`fill_safe`/`locator_safe` 四函数全零调用——**整文件可删** |
| replay/button_driver.py:1267/1659 | `expand_dropdown`/`wait_for_table_load` |
| replay/form_filler.py:85/2126/2237 | `probe_transfer_box_state`/`select_dropdown_option`/`get_form_errors`（旧版，新版 read_form_errors 活跃） |
| replay/wait_helpers.py:164 | `wait_for_api_response` |
| replay/anchor_helpers.py:54/170 | `strip_label_decorations`/`validate_anchor_css` |

**B-2 中置信僵尸（仅 shared/__init__ 导出，无消费者）**：dialog_helpers 的 detect_overlay/confirm_if_present；form_validation 的 detect_form_errors/has_form_errors_visible；fill_helpers 的 evaluate_skip_rules/TREE_SELECT_SKIP_RULES；path_mapper 的 ensure_workspace/scripts_dir_for。

**B-3 需用户确认**：feedback_loop.py 的 match_debug_strategy/save_fix_to_experience + stage2_errors.py 四异常类（唯一消费者是前者；若「D 轮反馈环」仍是计划则保留）。

**B-4 防误删清单**（易误判，实际活跃）：feedback_loop.patch_ui_result（stage1_rescue 补救链主路径）；run_parallel.py/shared_runner.py（文档化命令入口）；export_artifacts 的 gen_* 系列（helpers_ns 动态按名解析，静态计数低但必须保留）；ui_selectors 目录（const.get_ui_selectors 加载+同步进产物）；shared/button_clicker（委托层非弃用副本）；base_executor 的 _check_* 系列（_observe_post_click 内部链）。

---

## 三、优化方案（源头修复，非补丁兜底）

### P0-1 switch 统一（消除 C-1 双实现）

**源头修复**：`_do_toggle_switch` 内联 JS 全部删除，改调 `switch_ops.get_switch_state/toggle_switch`；Stage1 特有的关键词诊断（switch_keywords 校验）与 make_error/make_success 结果结构保留在 crud_executor 编排层（包装共享函数返回值）。若 switch_ops 缺少内联版的某信号源（对齐后 diff 确认），把缺失能力补进 switch_ops 单源。
**保留分支**：关键词校验、截图、playbook 结果构造全部保留；仅执行体（状态检测 JS/点击 JS/确认弹框/轮询）收敛为单源。
**风险**：低——switch_ops 与内联版 JS 几乎一字不差（审查实证），行为等价。

### P0-2 通知关键词表统一（消除 C-2 影子词表）

**源头修复**：success_verifier 策略 0.5/1/3/F 的 JS 内嵌词表改为运行时注入 `json.dumps(list(const.NOTIFICATION_FAILURE_KEYWORDS))` 与成功词表；JS 侧从注入常量遍历，不再维护字面量数组。保持 const 词表单源。
**保留分支**：各策略的判定结构不变（只换词表来源）；「0个失败」豁免正则仅存在于 const 侧，注入后 JS 侧同步获得。
**风险**：低——词表扩大只会使判定更准；需跑黑白名单+用户管理全链验证无 false positive。

### P0-3 `_buildSelector` 深度上限 + 语义容器截断（消除 L-1）

**源头修复**：form_filler.py:1992-2035 与:1737-1762 的 while 循环加两条终止规则：① 深度 ≥4 层即停；② 遇到语义容器类名（el-form-item / el-tab-pane / el-dialog / el-drawer / el-table__row）时以该容器为根截断。产物 selector 从 `#app > ... 9 层` 变为 `.el-form-item ... ≤4 层`。
**保留分支**：label 式 playwright_locator 生成（L2059 合规主选）不变；original 候选仍落盘（作为最后回退），只是不再爬到 #app。
**风险**：中——直接改变 Stage1 表单扫描产物（编辑 op 的 fallback_locators），需黑白名单+用户管理 Stage1 全链验证。

### P0-4 删除 switch `:nth-of-type` 错误分支（消除 L-2）

**源头修复**：element_scanner.py:928-936 的 visibleIdx 分支删除；locator 优先 `#${paneId} .el-switch`（已有），无 paneId 时用 form-item label 锚点（` .el-form-item:has(.el-form-item__label:has-text("X")) .el-switch`）或保留 `role=switch` 属性约束。
**风险**：低——nth-of-type 全局序号生成的选择器本身就是多匹配错误选择器，删除只会变对。

### P0-5 tab 无 id 时落盘文本定位（消除 L-6）

**源头修复**：element_scanner.py:1929-1932 的 `:nth-child` fallback 改为不落盘 locator（回放端 replay_engine.py:1831 已按 tab_name 文本定位）。
**风险**：低——回放已有等价逻辑。

### P0-6 strict 死参数删除（消除 C-5）

**源头修复**：success_verifier.verify_operation_success 签名删 strict；4 处调用点同步删实参。
**风险**：零——参数从未生效。

### P0-7 回放兜底委托完整版（消除 C-3）

**源头修复**：replay_engine._fill_ip_input 改调 form_filler 的 _parse_ip_segments/_expand_ipv6（或抽 shared）；_fill_composite_input 委托 FormFiller 对应实现。兜底路径与探测路径同源后，IPv6/区间能力自然对齐。
**保留分支**：正常 interaction_steps 回放路径不变，仅无记录兜底改道。
**风险**：低——兜底路径当前只影响旧 playbook（新 playbook 均有记录）。

### P0-8 ValueIndex.lookup 时间最早平局打破（修复 A-2）

**源头修复**：score 函数加入 timestamp 维度——同分平局时取 ts 更早的 entry（`s -= entry.timestamp * 0.001` 或 max(key=(score, -timestamp))）。时间最早原则在该路径补齐。
**风险**：低——仅影响多候选同分场景，方向与原则一致。

### P1-1 row_interaction JS 兜底与 button_driver 合并（消除 C-6）

**源头修复**：row_interaction.py:199-226 与 button_driver.py:448-461 的 JS btn.click() 兜底收敛为 shared 单函数，两调用方引用。
**风险**：低。

### P1-2 僵尸代码与死兼容直删（B-1 全部 + A-1 全部 + A-2 配套）

按 2.4/2.5 清单执行：B-1 十组函数直删（含 locator_helpers 整文件+导出清理）；A-1 七处兼容分支删；A-2 改 run.py:1617 调用点后删旧函数。
**不删**：A-3（需改测试，待用户确认）、B-3（反馈环计划待确认）、L-7 坐标兜底（运行时不落盘且与 JS click 重叠但删除收益低、改动风险存在）、A-4/B-4 全部保留。

### P2-1 trigger_locator 全局 has-text 落盘前唯一性校验（L-3，谨慎项）

**建议方案**：落盘前 `count==1` 校验——全局 locator 匹配数 >1 时升级 scope（dialog 场景加 dialog wrapper 前缀、行场景以行锚点前缀），仍 >1 保留原样打日志。
**定级 P2 原因**：影响所有按钮落盘产物，黑白名单/用户管理的按钮场景当前回放全通过（click_row_button 行作用域正确、trigger 全局但页面无同名冲突），改动需大范围验证。**本轮不实施**，列入后续迭代（若后续出现点错对象案例立即升级）。

### P2-2 不实施项（文档记录）

- **L-4 xpath_index 体系**：涉及回放「优先 Stage1 记录成功路径」的核心机制（replay_engine.py:441-444 契约），改动面大；当前有全量遍历降级兜底。保留现状。
- **L-7 坐标兜底**：运行时即时计算不落盘，无漂移；与原则冲突但风险为零。
- **C-4 填充不对称**：fill_create_form 是 Stage1 主链，重构风险高；interaction_steps 记录机制已保证新 playbook 回放不依赖探测版 locator。
- **A-3/B-3**：需用户决策（改测试用例 / 反馈环计划）。
- **estack-ceshiyu 旧 playbook**（无 assert_mode）：重跑其 Stage1 即消除对 A-4 守卫的触发，属数据迁移非代码改动。

### 逻辑分支保持性分析（实施安全论证）

| 实施项 | 删除的分支 | 论证 |
|---|---|---|
| P0-1 | 内联 switch JS（状态/点击/确认/轮询四段） | switch_ops 同逻辑已存在且被 generic 分支+Stage2 实际调用验证过；关键词诊断/make_error 编排保留 |
| P0-2 | JS 内嵌 6 词字面量 | const 18 词表为其超集（含全部 6 词），只增不减 |
| P0-3 | DOM path 无限爬升行为 | label 式主 locator 不变；截断只影响 original 回退候选质量 |
| P0-4 | switch 全局序号选择器 | 该选择器语义错误（多匹配），无有效行为可保留 |
| P0-5 | tab nth-child 落盘 | 回放 tab_name 文本定位已存在 |
| P0-7 | 弱化版 ip/composite 解析 | 完整版是探测主链在用实现，能力超集 |
| P0-8 | 无（只加平局打破） | 新增维度不删除现有分层 |
| P1-2 | B-1 僵尸（零调用已证）+ A-1 兼容（触发条件已证不存在） | 动态调用（helpers_ns/getattr）已逐一排除；产物格式已全量核验 |

---

## 四、实施计划与验证

1. 实施顺序：P0-6 → P0-4 → P0-5 → P0-2 → P0-1 → P0-7 → P0-8 → P1-1 → P1-2（先零风险项，后影响产物项）
2. 每步 py_compile + 局部验证；全部完成后全链验证
3. **验证口径**：黑白名单 Stage1→5 全链 + 用户管理 Stage1→5 全链；验收标准——Stage1 操作数不减少、Stage2 回放一致、API 脚本通过数不降、UI 脚本门禁通过
4. 回归重点：P0-3 改变表单 selector 产物 → 编辑 op 回放；P0-2 词表扩大 → 断言误判率；P0-1 → switch 双 Tab 操作

---

## 五、遗留与建议

- run.py:1617 的 get_module_group_index 改调 get_or_assign_module_index（P1-2 配套）
- generate_ui_script 模板的 _cleanup_dialogs 独立第三份选择器清单（C-8）：建议未来下放 shared，本轮不动（模板稳定性考虑）
- anchor ordinal（L-9）：建议记录属性快照消歧，列入后续
- estack-ceshiyu 项目 playbook 为旧格式（无 assert_mode），建议重跑 Stage1 刷新产物
