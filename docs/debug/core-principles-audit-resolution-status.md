# 核心原则审计——解决状态逐条确认表

对照 `core-principles-audit-and-optimization-plan.md` 全部问题项，逐条核验解决状态。
核验日期：2026-11-19。行号以当前工作区实测为准（报告原行号有漂移）。

## 一、三阶段一致性（原则 1）

| 项 | 报告发现 | 结论 | 现场证据 |
|---|---|---|---|
| C-1 | switch 双实现同文件并存 | **已修（P0-1）** | `_do_toggle_switch` 353→65 行，执行体收敛 switch_ops 单源；关键词诊断/结果构造保留编排层 |
| C-2 | success_verifier 两套通知词表 | **已修（P0-2）** | 四策略 JS 全部改 const 18 词表参数注入（evaluate 参数传递，弃 json.dumps） |
| C-3 | 回放 IP/复合输入弱化版 | **已修（P0-7）** | `_fill_ip_input`/`_fill_composite_input` 删除，委托 form_filler 完整版；错误 IPv6 `::` 展开实现已删 |
| C-4 | 简单字段填充不对称 | **计划保留（P2-2）** | fill_create_form 是 Stage1 主链，重构风险高；interaction_steps 机制已保证新 playbook 不依赖探测版 locator |
| C-5 | strict 死参数 | **已修（P0-6）** | 参数删除，4 调用点清零 |
| C-6 | 行内按钮 JS 兜底双份 | **已修（P1-1）** | `js_click_row_button` 单源（shared/row_interaction.py）；button_driver 方法 3 与兜底改单源调用 |
| C-7 | 私有轻量断言不经 success_verifier | **遗留未修** | replay_engine:385-388/1947/1960 仍在；报告未列修复计划 |
| C-8 | 模板 `_cleanup_dialogs` 第三份清单 | **遗留未修（计划内不动）** | generate_ui_script.py:470 定义、:755/:794 调用仍在；报告 §5 明确"本轮不动（模板稳定性）" |

## 二、元素定位合规（原则 2）

| 项 | 报告发现 | 结论 | 现场证据 |
|---|---|---|---|
| L-1 | `#app` 9 层绝对路径生成源头 | **已修（P0-3）** | `_buildSelector` 深度 <4 + 语义容器截断 |
| L-2 | switch 全局序号 nth-of-type | **已修（P0-4）** | 错误分支删除，改文本锚点 + 空 locator 守卫 |
| L-3 | trigger 全局 `:has-text` 无 scope | **计划内延后（P2-1）** | crud_executor 现存 5 处（原报告 8 处，P1-2 删僵尸函数后自然减少）+ shared/button_clicker:269/273/321 仍在；触发点错对象案例即升级 |
| L-4 | xpath_index 编号落盘体系 | **计划保留（P2-2）** | 回放"优先 Stage1 记录成功路径"核心契约（replay_engine:441-444），有全量遍历降级兜底 |
| L-5 | `el-form-item:nth-child(N)` | **遗留未修** | element_scanner:1605-1606、form_filler:1920/2085 仍在；报告无修复计划 |
| L-6 | tab 无 id 时 nth-child 落盘 | **已修（P0-5）** | tab locator 仅 `#id` |
| L-7 | 坐标点击兜底 | **计划保留（P2-2）** | button_detector:619-686 第 3 兜底仍在；运行时即时计算不落盘、无漂移 |
| L-8 | radio 选项缺 form-item scope | **遗留未修** | form_filler:470/566/636 仍在；报告无修复计划 |
| L-9 | anchor `>> nth=` 受控编号 | **遗留建议** | ordinal 本体保留（validate_anchor_css 已作为 B-1 僵尸删除——其"唯一性校验"实为零调用死代码）；属性快照消歧建议未实施 |

## 三、API 捕获与脚本生成（原则 3、4）

| 项 | 报告发现 | 结论 | 现场证据 |
|---|---|---|---|
| A-1 | value_chain 三原则完整实现 | **符合 + 文档瑕疵已修** | 实现本就正确（L253 ts 升序取首个=首次产生者）；docstring L72/L219 与 request_interceptor:393 的「时间最近」错误表述已改「时间最早（首次产生者）」 |
| A-2 | lookup 评分未用 timestamp | **已修（P0-8）** | timestamp 最早作为平局打破项已加入 |
| A-3 | 前置 API 收集泛化 | **符合保留** | 报告原结论"低危，可保留" |
| A-4 | assert_mode 守卫等活逻辑 | **全部保留（按报告应保留）** | 8 处活逻辑清单逐项核验在位；estack-ceshiyu 触发源已随项目废弃消除 |
| A-5 | 测试数据 5 角色体系 | **符合** | 无需动作 |

## 四、清理项 A（旧格式兼容）

| 项 | 报告发现 | 结论 | 现场证据 |
|---|---|---|---|
| A-1 | 7 处旧 playbook/旧文件兼容 | **已删（P1-2）** | fallback_locator str 分支 / click_row_more 双格式 / version scripts+flows 回退 / modules.yaml group 循环 / required_flows 死参数 / flow_name 死参数 / field_states no-op 循环（含 processed_labels 死变量） |
| A-2 | get_module_group_index 旧函数 | **已删（P1-2）** | run.py:1623 改调 get_or_assign_module_index 后旧函数删除 |
| A-3 | 双字段名 + value_index 兼容 | **用户确认后全删（两部分）** | ①validate_stage3 权威名 crud_order/api_by_button + 测试 10 处对齐（117 passed，旧名数据被拒实证）②value_index 内建分支删除：manifest_builder:203 else 分支 + 审计漏报的 pre_api_tracer:76-83 同型分支，均改显式 ValueError fail-fast；value_index 必传（签名重排至默认参数前）；2 个刻意兼容用例删除、3 处测试调用点补传（全量 673 passed） |
| A-4 | 防误删清单 | **全部保留** | 逐项核验未误删 |

## 五、清理项 B（弃用方法/僵尸代码）

| 项 | 报告发现 | 结论 | 现场证据 |
|---|---|---|---|
| B-1 | 10 组高置信僵尸 | **已删（P1-2）** | 含 locator_helpers 四函数；**报告修正一处**：该文件"整文件可删"过判——safe_css 有 30+ 活跃调用点，文件保留 |
| B-2 | 6 组中置信僵尸 | **零消费者确认，未删（待用户决策）** | detect_overlay/confirm_if_present/detect_form_errors/has_form_errors_visible/evaluate_skip_rules/TREE_SELECT_SKIP_RULES/ensure_workspace/scripts_dir_for；假消费者已排除（gen_test.py:141 调的是 version.py 侧同名函数）；未入实施计划 |
| B-3 | feedback_loop + stage2_errors | **用户拍板 D 轮反馈环弃用，已删** | match_debug_strategy/save_fix_to_experience/_load_strategies 删除（core/config/debug_strategies.json 不存在，恒 None 死上死实证）；stage2_errors.py 整文件删；patch_ui_result 保留（stage1_rescue 主路径 7 调用点） |
| B-4 | 防误删清单 | **全部保留** | patch_ui_result / run_parallel / gen_* 系列 / ui_selectors / button_clicker / _check_* 系列均在位 |

## 六、专项与计划外

| 项 | 结论 | 说明 |
|---|---|---|
| estack-ceshiyu | **用户拍板项目废弃，全删** | workspace 侧 243.3MB + projects 侧 2.9MB；原"重跑 Stage1 刷新产物"建议被废弃决策取代，A-4 守卫触发源消除 |
| P2-1（L-3） | 计划内延后 | 报告原文"若后续出现点错对象案例立即升级" |
| P2-2 不实施项 | 五项中三项按计划保留（L-4/L-7/C-4），两项经用户决策后实施（A-3/B-3），estack-ceshiyu 被废弃决策取代 | 全部闭环 |
| §5 遗留建议 | run.py:1617 改调已完成；C-8/L-9 建议保留未实施 | 一致 |

## 七、验证状态与剩余事项

- 测试基线：tests 全量 **673 passed**（16 文件，2026-11-19）；py_compile 全部通过。
- git：HEAD 9f8bab7 覆盖 P0-1~8/P1-1/P1-2/list-entity；**A-3（两部分）/B-3/estack-ceshiyu 删除/value_index 必传/时间最早 docstring 修正未提交**（提交节奏听用户安排）。
- **待执行**：黑白名单 + 用户管理 Stage 1-5 全链回归（四验收口径：Stage1 操作数不减、Stage2 回放一致、API 通过数不降、UI 门禁通过）；回归重点 P0-3 表单 selector 产物、P0-2 词表误判率、P0-1 switch 双 Tab。
- **待用户决策**：B-2 六组中置信僵尸（零消费者已证，删除安全）。
