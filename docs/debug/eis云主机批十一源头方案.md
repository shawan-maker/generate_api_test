# EIS 云主机批十一源头修复方案

- 依据：r13 复验日志（eis_s1_vm_r13.err.log）三轮对照（r10/r11/r12）+ 批十 A/B/C 源码（HEAD 820bf46，5 提交已推送 origin/main=60a0889）。
- 用户定案范围：**重点解决 r13 中 11 项 skipped(disabled) + 2 项 click_failed 的回归**（这些操作 r10/r11 曾成功）；
  从源头修复，**不打补丁、不靠 fallback 兜底、不破坏既有成功功能**；本轮只出方案文档，不直接改代码。
- 用户定案排除项（不浪费时间）：克隆「前端报克隆失败但后端实际受理、其他操作报正在克隆中」属**系统 bug**，
  框架侧不处理（r13 报告 P2 定案作废，见 §六勘误）。

## 〇 问题总表与批次归属

| 编号 | 回归项（13 项） | r10/r11 | r12 | r13 | 本方案修复项 |
|---|---|---|---|---|---|
| 1-11 | 硬关机/重启/硬重启/热迁移/冷迁移/重置密码/更新密钥对/修改Hostname/重装操作系统/Console/执行命令 | ✓ | ✓(c)/持平 | ✗ skipped(disabled) | F1+F3（R1 恢复转移复活）|
| 12 | 暂停（op7） | ✓(r) | ✗ | ✗ click_failed | F1+F3（R1 业务执行补位）|
| 13 | 恢复（op24） | ✓(r) | — | ✗ click_failed | F1+F3（Paused 窗口重建）|
| 伴生 | pending 终局 14（>4 口径） | — | 12 | 14 | F1+F3 |
| 伴生 | [STATE-CHANGE] 批量执行零触发（P4） | — | 6 次 | 0 次 | F1+F3（检测输入恢复）|
| 观察项 | F7 伪边拦截 44 处 / P3 扩容假成功 / P-3 el-select / 克隆正路径 | — | — | — | F4（r14 观察，不改码）|

批次划分：批十一-A = 探针 P1 → F1（菜单锚定权威化）+ F3（有界复核扩 + R1 单轮重读）；批十一-B = F2（扫描 hover 与 click 路径同源，批八设计兑现）。

## 一 失败模式定案（13 项全部同根）

### 1.1 13 项的下游性质

- **11 项 skipped(disabled)**：main 阶段尝试时行处于 Stopped 态，业务真实禁用（r13 权威菜单快照已证 disabled 判定对齐）——
  main 阶段跳过本身**正确**；回归在于 r10/r11 时代这些项随后由 **R1 恢复转移/业务执行**在行状态匹配时消费成功
  （r11 14:12 轮实证：pending 11 项 → R1 业务执行 开机(Stopped)→✓ → 暂停(Running)→✓ → 硬重启(Running)→✓ 三连成功，L1509/1564/1619）。
- **2 项 click_failed（暂停 op7 / 恢复 op24）**：主执行序窗口问题（暂停在开机成功前尝试、恢复在 Paused 未建立时尝试）；
  r11 由 R1 在状态匹配后补位执行成功。同属 R1 通道下游。
- **R1 死因**：r13 全程 10 轮 [R1-READY-DECISION] 中 status 可读轮（14:47:14，status=Stopped）判定完全正确
  （10 项全 state_disabled，逐项原因枚举），而 menuId=None 轮 status=None → 无 ready → 零消费 → pending 终局 14。
  **R1 机制本身健康，死于上游状态读取断链。**

### 1.2 断链的完整机制（十轮 DEBUG 实证）

r13 十轮增量扫描（INCREMENTAL-SCAN-DEBUG）全序列：

| 轮次 | menuId | has_visible_menu | method | 真hover | 前置事件 |
|---|---|---|---|---|---|
| 14:41:01 / 14:43:24 / 14:44:24 / 14:46:24 | None | False | vue_show | fail | 关机成功 14:40:53 后行重渲染 |
| 14:47:15 / 14:48:05 / 14:48:41 | dropdown-menu-4485 / 6756 / 8523 | True | vue_show | fail | 规格变更 click 路径真交互留下活菜单窗 |
| 14:49:49 / 14:52:10 / 14:53:40 | None | False | vue_show | fail | 开机成功 14:49:34 后行重渲染 |

三个关键事实：

1. **真 hover 十轮全败且三轮从未成功**：`dropdown_real_hover=ok` 在 r11/r12/r13 三份日志零命中
   （r12 09:16:52/09:18:46/09:28:12 三败、r13 十败；r11 无此路径）。批八「真实 hover 先行」设计目标自引入以来从未兑现过。
2. **menuId 有值与 None 的分界 = 行重渲染**：行状态翻转（关机/开机成功）与表格刷新都会销毁并惰性重建菜单元素，
   id 每次动态生成（40 秒内三值 4485→6756→8523，id 非稳定锚）。菜单只有被 click 路径真交互（或近期 vm.show）打开过才存活于 DOM。
3. **r12 同场景对照（旧代码）**：09:16:52 同样 hover fail + vue_show 假展开，但 `has_visible_menu=True`、
   [STATE-CHANGE] 行已稳态（status=Stopped）读取成功——批十-B 之前的旧链含「全页回退找第一个可见 menu」，
   捡到近期真交互留下的活菜单兜住了读取。**r11/r12 的成功是「恰好有活菜单」的运气窗，非机制保证。**

### 1.3 三个下游通道同时死亡（r12 收益全部回落的原因）

| 通道 | r12 | r13 | 断链输入 |
|---|---|---|---|
| R1 恢复转移/业务执行 | ✓（黄金窗 9 轮 snapshot 全成功） | 10 轮零消费 | snapshot 三链 targetMenu=None → status=None |
| [STATE-CHANGE] 稳态批量执行 | 6 次（09:19:34 Stopped、09:20:21 Starting、09:21:34/09:22:13/09:26:08/09:26:48） | 0 次 | 增量收集面崩塌（has_visible_menu=False → 零收集）→ 无状态翻转可检测 |
| 增量收集（新菜单/切换对） | 有效 | 采集面仅 {'取消','确定'} | 同上 |

r13 报告 P4「F8 排尾致运维态级联批窗口消失」在本次定案中**降级为伴生表象**：级联批的触发输入
（增量扫描收集）本身为空，窗口消失首先是断链的下游；F1 修复后 r14 观察 F8 排尾是否仍构成二次约束。

## 二 根因定案（四个结构性缺口，全部机制级）

行号基准 HEAD 820bf46（批十-C）。

### 缺口A：扫描路径真 hover 与 click 路径定位不同源（批八设计从未兑现）

- 扫描 hover 目标：`{row_selector} .el-dropdown`，row_selector 为 `.el-table__body-wrapper .el-table__row:nth-child(1)`
  （get_row_selector_by_marker 产物，位置式 nth-child，★crud_executor L277-287 消费）。
- click 路径（button_driver click_row_more_item）：SEARCH-FIRST 过滤 + **固定列**行解析 + marker 文本锚定
  （r11 日志实证「在固定列找到行(索引0)，回主体取对应行」）——三轮全成功。
- `_ROW_BY_MARKER_JS`（L132-149）本身已是 body-wrapper 文本锚定且能找到 dropdown（expand JS L294 命中、dropdownIndex=2），
  而 hover 用 nth-child CSS 三轮 0 成功——两条链**同函数内不同源**，批八的「真实交互先行」从未真正发生。
- 后果：__vue__ 不可用窗口（F6 导航往返后 ~15s，批八/r10 定案 dispatchEvent 亦不可用的窗口）内，
  本应由真 hover 提供的唯一可靠真展开从未存在过。

### 缺口B：menuId 优先定位的隐含假设错误（批十-B 设计偏差）

- 批十-B（4fb56c1）menuId 抓取（expand JS L304-306）：`dropdown.querySelector('.el-dropdown-menu') || dropdown.parentElement?.querySelector('.el-dropdown-menu')`，
  隐含假设「菜单元素在 dropdown 查询域内且 id 稳定」。
- 实测证伪（§1.2 事实 2）：菜单元素**惰性创建、随行重渲染销毁重建、id 每次动态生成、
  期间在 dropdown/行查询域内时有时无**（3/10 轮命中恰是 click 路径留下的存活窗）。
- 批十-B 同时删除「全页回退找第一个可见 menu」（正当：批五 Gap B 捡他行菜单污染收集、r12 09:28 命中弹层容器），
  **但未提供等价且行锚定的新锚**——删了坏锚没立好锚，r11/r12 靠运气窗的成功链失去支撑，构成结构性 menuId=None（7/10 轮）。
- 伴生排序缺陷：增量扫描 expand JS 的 menuId 抓取在 `vm.show()` **之前**执行（L304-306 先于 L309-313），
  本轮 show() 才创建的菜单永远抓不到；snapshot（L670-693）是 show 后抓取——两处「同源」注释名不副实，排序也不一致。

### 缺口C：三链全部行内查询，R1 status=None 即零消费

- snapshot 定位链（L701-732）与增量 _menu_visible_js（L342-363）同构：menuId → 行 scope `.el-dropdown-menu` → dropdownIndex，
  全部要求菜单元素已在 DOM 且落在行内查询域 → 菜单 body 挂载/惰性未建时全空 → status=None。
- R1 对 status=None 的处理是逐轮「无 ready 且状态不可读，结束（降级）」（L2013）——**无单轮内重读**，
  轮询边界恰好落在「行重渲染后、菜单重建前」的评估窗时，整轮被判 None（14:49:49 即开机成功后 15s，行已 Running 仍读 None）。

### 缺口D：dispatchEvent 有界复核的门条件不含 vue_show（批十-B 注释勘误）

- 增量扫描有界复核（L372-386）仅对 `method == 'dispatchEvent'` 触发 2×800ms 复核；
  批十-B 注释「vue_show 路径 9 轮实证可靠，不复核不加延迟」——r13 证伪（vue_show 轮 7/10 menuId=None），
  复核门条件用错了维度：不可靠的判据是「menuId 空」而非「method=dispatchEvent」。

### 定性勘误（r13 报告 P1 表述）

- 「真 hover 失败系 v-modal+el-dialog__wrapper 残留遮罩拦截 hit-test」**不成立**：
  SCAN-SWEEP（L246-265，hover 前探测 `.el-dialog__wrapper/.el-drawer/.el-message-box` offsetWidth>0）在 r13 全程**零触发**
  =页面确实无可见残留弹窗，探测正确。hover 失败是目标定位结构性失效（缺口A），非遮挡。
- 「直接按钮采集面 {'取消','确定'} 为污染」**不成立**：direct_buttons_found=['取消','确定'] 是三轮恒定良性签名
  （r11 14:01/14:03/14:11/14:14、r12 09:16:50/09:18:44/09:28:10、r13 全部十轮一致，含 R1 工作的 r11 成功窗），
  与回归无关；真正的崩塌只在 dropdown 菜单收集链。

> ★ 探针后机制深化（§四 定案 1-3）：「假展开」定性修正为「展开成功但定位丢失」——vm.show()
> 十轮全部真实生效（菜单在 body 上可见 500×448），三链失效的机制是 Element UI popper.js
> appendToBody（首次 show 把菜单容器移出 cell，行内查询域恒空）+ 双形态组件（SEARCH-FIRST
> 重渲染形态才有 vm.show/popperElm，r13 运行时行全程原生形态）；缺口 A 的 hover 从「未兑现」
> 深化为「结构性死路」（actionability not visible）；缺口 B 的「惰性创建/id 漂移」深化为
> 「容器移动 + 重渲染重建」。修复设计按 §三 修订版（F1 扩源极简 + F2 作废）。

## 三 修复设计（源头级，无兜底补丁）

> ★ 探针 P1 三连（2026-11-27 执行完毕，§四）实证后本节重写：原 F1「消费段 popperElm
> 分支」简化为「menuId 抓取扩源」，原 F2「hover 同源」作废（真 hover 死路实证）。

### F1 menuId 抓取扩源——vm.popperElm.id 兜底（批十一-A，核心，极简）

**机制定案（探针 P1c 实证）**：Element UI 原生 dropdown（SEARCH-FIRST 重渲染形态）的
VuePopper mixin 实例属性 `vm.popperElm` **全程恒指本行菜单容器**——show 前指向 cell 内
容器、vm.show() 后 popper.js 把容器**移动**到 document.body（首次 show 起容器永久驻留
body，cell 域恒空）。菜单项 `__vue__.command.row` 绑定随 DOM 移动不丢失（r13 menuId 有值
轮 status 可读已实证）。

- **两处展开段 menuId 抓取扩源**（消费端零改动）：
  - 增量扫描展开 JS（crud_executor L304-306）：`dropdown.querySelector('.el-dropdown-menu') ||
    dropdown.parentElement?.querySelector(...)` 之后追加兜底
    `dropdown.__vue__?.popperElm?.id`——三源取首个非空（cell 域容器 / popperElm 引用）。
  - snapshot 展开段（crud_executor L687-689）同款扩源（show 后读，与既有序一致）。
- **消费端零改动**：menuId 语义不变（全页唯一 id），既有 `getElementById(menuId) ||
  querySelector([data-popper-id=...])` 链①直接命中 body 上的菜单——has_visible_menu 判定、
  dropdown_buttons 收集、snapshot 的 states/vue_states/status/qga_ready/maintenance 读取全部
  经此链自动恢复，**单点扩源全链复活**。
- **r13 十轮 None 全部可修复**：None 轮=容器已在 body（此前 show 过），popperElm.id 恒可取
  （P1c V4 二次 show 形态实证）；有值轮=行重渲染后新容器在 cell（popperElm.id 同值，
  两形态统一）。
- **零 DOM 改写、零跨行污染**：popperElm 是组件实例自身引用（天然行关联），id 是 popper
  分配的全页唯一标识；menuId 有值轮行为零变化（同 id 同链）。
- **estack 等其他环境**：无 popperElm 的形态（自研组件/初始形态）返回 undefined，扩源
  静默不生效，回落既有链与现状等价（P1b 实证 EIS 初始 custom 形态 hasPopperElm=false）。
- **排序修正**（原 F1 保留项）：增量扫描展开段 menuId 抓取在 vm.show() **之前**执行——
  popperElm.id 不依赖 show（show 前已可读），维持现序即可，但注释同步勘误「show 前抓取
  仅对 cell 域有效；popperElm 源不依赖时序」。

### F2 作废勘误（原「hover 同源」设计）

探针 P1/P1b 实证 Playwright 真 hover 对 EIS 行 dropdown 触发器**结构性死路**：
actionability 判定 `element is not visible`（十轮运行 + 探针 nth-child CSS 与文本锚定
两种形态复刻均超时；dropdown 有 bbox 但 Playwright 可见性判定不过——EIS cell 裁剪/visibility
继承形态）。**批八「真实 hover 先行」自设计起从未成功过且永不可能成功**，hover 尝试块
在扫描路径是无害死代码（失败不阻断、日志 reason 已有），本批**移除 F2 修复项**，
扫描路径 hover 尝试块保持现状（清理属未来批次，不在本批破坏面内）。

原 F2 目标的真实形态已由 click 路径既有机制保证：click 路径每操作 SEARCH-FIRST 过滤
→ 行重渲染为原生 el-dropdown（vm.show 有效形态）→ 扫描路径发生在操作成功后（行保持
原生形态）→ vm.show() 十轮全部真实生效（r13 method=vue_show 全过）。**展开从来不是
问题，菜单定位才是**（F1 已修）。

### F3 复核门条件修正 + R1 单轮内有界重读（批十一-A）

- **有界复核条件**：`method == 'dispatchEvent'` → `not expand_result.get('menuId')`
  （menuId 空即复核，2×800ms 不变）。批十-B 注释同步勘误为「vue_show 路径 r13 证伪
  7/10 轮 menuId=None（探针 P1c 定案：popper.js appendToBody 后 cell 域恒空，非展开失败），
  复核按 menuId 空判定」。F1 落地后 popperElm 扩源使 menuId 恒非空（组件异常除外），
  复核门趋零触发——从「盲等」变为「组件异常兜底」。
- **R1 单轮内有界重读**：reconcile settle 得 status=None 时，本轮内**一次**重扫 snapshot
  （单次，有界，不进循环），仍 None 才按既有降级语义结束（「无 ready 且状态不可读」
  路径零变化）。覆盖「轮询边界落在行重渲染后、菜单重建前」的评估窗（14:49:49 型：行已
  Running 却读 None 拖整轮）。12 轮挂死保险、R3 降级语义、estack 零降级等价全部不动。
- **破坏面评估**：两处均为既有机制的条件泛化/单次重读，无新增循环、无新增等待上界外
  延迟（重读 ≈1 次扫描耗时）。

### F4 观察项（不改代码，r14 复验定案）

| 项 | 内容 | r14 口径 |
|---|---|---|
| F7 收敛 | r13 拦 44 处系 r12 时代无标志种子边；r14 装载 r13 种子（记边带 validated 标志）应自动收敛 | 拦截计数 → 近 0 |
| P4 二次约束 | F1 修复后 STATE-CHANGE 检测输入恢复；观察 F8 排尾是否仍构成级联批窗口约束 | [STATE-CHANGE] 恢复触发 |
| P3 扩容假成功 | className(el-message--error) 未贯通 message_capture 捕获分类通道 | 仅记录，另批处理 |
| P-3 el-select 值同步 | 需行 Running 态走更新密钥对填充路径（F1 修复后应自然触达） | 更新密钥对成功即触达 |
| 克隆正路径 | 系统 bug（用户定案）；环境残留 test761891 清理后仅记录正路径表现 | 只记录不修 |

## 四 探针结论（P1/P1b/P1c 三连已执行，2026-11-27）

独立 Playwright 只读探针（tmp/diag_b11_menu_topology.py / diag_b11_p1b_divergence.py /
diag_b11_p1c_popperelm.py，产物 tmp/b11_*_probe.json），核心定案六条：

1. **EIS 行 dropdown 双组件形态**：初始加载/清空搜索后=自研 custom-el-dropdown
   （方法面 click/command/visibleChange，无 show/hide/popperElm，dispatchEvent 合成事件
   无效，菜单三副本容器 inline 挂 cell 内 display:none）；**SEARCH-FIRST 过滤触发行
   重渲染后=Element UI 原生 el-dropdown**（方法面 dispatch/broadcast/show/hide/
   initDomOperation 完整，菜单单容器，vm.show 真实有效）。click 路径每操作 SEARCH-FIRST
   → r13 运行时 marker 行全程原生形态（十轮 vue_show 全过即证）。
2. **菜单 appendToBody 机制**：原生形态首次 vm.show() 时 popper.js 把菜单容器从 cell
   **移动**到 document.body（非克隆，cell 域此后恒空）；菜单项 `__vue__.command.row`
   随 DOM 移动不丢失（22 项菜单全部可读，r13 有值轮 status 可读同源实证）。
3. **menuId 有值/None 的二元性定案**：有值=行重渲染后首次 show 前扫（cell 内有新容器，
   id 动态新值——40 秒三值漂移=三次重渲染）；None=此前 show 过（容器已移 body，cell 域
   查不到）。**vm.show() 十轮全部真实生效（菜单可见 500×448），「假展开」定性错误，
   真根因=「展开成功但三链定位丢失」**。
4. **vm.popperElm 全程恒指本行菜单**（show 前 cell 形态/show 后 body 形态/二次 show
   前后均可用，matchesVisibleMenu=true）——F1 扩源的权威锚实证成立。
5. **真 hover 结构性死路**：Playwright actionability `element is not visible`（十轮
   运行+探针两种定位形态复刻均超时）——原 F2 设计作废（§三 F2 勘误）。
6. **r12 运气窗机制精确复现**：旧代码全页回退找到的「第一个可见菜单」=本行刚 show 的
   菜单（body 上唯一可见）；show 过=成功窗，dispatchEvent=失败窗（09:28）——批十-B
   删除全页回退后运气窗消失，F1 popperElm 扩源把运气窗变机制保证。

## 五 实施顺序与批次划分（探针后修订）

```
批十一-A（单批收口，原 B 批随 F2 作废并入）：
  1. ✅ 探针 P1 三连执行完毕（§四）
  2. F1 两处展开段 menuId 抓取扩源 popperElm.id（增量扫描 L304-306 + snapshot L687-689）
  3. F3 复核门条件扩（menuId 空）+ B 批注释勘误 + R1 单轮有界重读
  4. 测试：tests/test_batch11_fixes.py（参照 batch9/batch10 基建：
     FakePage/ScriptedScan/_src 源码守卫风格）
     - F1 单测：popperElm 扩源注入断言（menuId 空时 popperElm.id 兜底）、
       有值轮零变化断言（DOM 域先命中）、无 popperElm 形态静默回落断言
     - F3 单测：vue_show + menuId=None 触发复核、dispatchEvent 复核保持、
       R1 status=None 单次重读后仍 None 按降级语义
  5. py_compile 改动文件 → 全量回归 1022 基线 exit 0 → 独立提交
真机复验（r14）：
  1. 前置：环境残留克隆产物 test761891 人工清理（用户侧）
  2. 全量按钮验证 Stage 1 后按 §六 口径逐项对账
  3. 收尾：全链 Stage 2-5（r13/r14 ui.json 首次具备完整回放条件：删除入序、运维对入册）
```

## 六 r14 验收口径总表

| # | 口径 | r13 基线 | 判定标准 |
|---|---|---|---|
| 1 | 13 项回归恢复 | 11 skipped + 2 click_failed | 11 项由 R1/STATE-CHANGE 通道按行状态消费成功（r11 先例：R1 业务执行三连成功）；暂停/恢复经 R1 补位成功建立 Paused 窗口 |
| 2 | R1 status 可读性 | 7/10 轮 None | 行状态可读窗内 status=None 轮趋零；偶发重渲染窗值 ≤1 轮且经单轮重读恢复，不拖轮 |
| 3 | pending 终局 | 14 | ≤4（批十口径） |
| 4 | [STATE-CHANGE] 批量执行 | 0 次 | 恢复触发（r12 基线 6 次）；P4 是否残留 F8 二次约束同时定案 |
| 5 | F7 伪边拦截收敛 | 44 处 | 装载 r13 种子后趋近 0（新记边自带 validated 标志） |
| 6 | estack-ecso-cz1 回归 | 1022 exit 0 | 全量测试 1022+ 基线 exit 0 |
| 7 | 判定成功=业务真实成功 | 1 假成功（扩容，P3 系统侧） | 按系统 bug 结论记录，框架口径不新增假成功 |
| 8 | 全链 Stage 2-5 | 未跑 | ui.json 回放 + capture + 脚本生成 + UI 门禁 |

## 七 勘误与既往结论修订（对 r13 报告）

1. **P1 表述勘误**：「残留遮罩拦截 hit-test」→「hover 目标定位结构性失效」（SCAN-SWEEP 零触发为证伪证据，§二）；
   「menuId 采集 6/9 None 为 vue_show 路径问题」保留但机制上移——根因是锚设计错误（缺口B），vue_show 只是暴露面。
2. **P2 克隆定案作废**：克隆假失败（前端报失败、后端受理、「正在克隆中」互斥）= 系统 bug（用户定案），框架不处理；
   「创建云主机失败」error 来源不追查。r13 报告该项从批十一候选中移除，环境残留 test761891 清理后仅记录正路径表现。
3. **批十-B 注释勘误**：「vue_show 路径 9 轮实证可靠，不复核不加延迟」→ r13 证伪（7/10 轮 menuId=None），
   F3 按 menuId 空泛化复核门（源码守卫断言同步，遵守「断代码形态而非注释文本」纪律）。
4. **批十-B 删除全页回退的再评价**：删除方向正确（批五 Gap B 污染实证成立），错在未立新锚——
   本方案 F1 以组件实例权威引用补位，不复活污染性全页回退（B 批防污染初衷完整保留）。
5. **r13 报告批十一候选 5 项映射**：menuId 行内兜底→F1；className 贯通→F4 观察另批；
   守卫 B 克隆豁免复核→作废（系统 bug）；运维态解锁项定向批量执行→F1+F3 通道恢复替代（R1/STATE-CHANGE 复活后
   不再需要专项批量通道）；r14 观察 F7 收敛→F4 不变。

## 附：本轮取证产物索引（监控端 tmp，不入库）

- `tmp\rootcause_probe.txt` — 五段根因取证（r13 十轮 DEBUG 全序列 / r12 09:16 同场景 / r12 STATE-CHANGE 6 次 /
  r11 14:12 R1 业务执行三连成功段 / 14:47:14 R1-READY-DECISION status=Stopped 可读判定）
- `tmp\r13_parse_result.txt` / `r13_sig_result.txt` / `r13_probe2.txt` — r13 解析/签名/对账产物（既有）
- 日志：`workspace\eis\output\eis_s1_vm_r13.err.log`（L266-2419 十轮扫描、L1383 可读轮判定、L2181-2357 PAIR-SCAN）
  / `eis_s1_vm_r12.err.log`（L373-392/1495-1514 旧链对照）/ `eis_s1_vm_r11.err.log`（L1457-1619 R1 成功机制基线）
- 源码（HEAD 820bf46）：crud_executor.py L132-149（_ROW_BY_MARKER_JS）/ L152-478（增量扫描展开与三链复核）/
  L609-797（snapshot 展开与三链）/ L1717+（reconcile）/ shared\row_interaction.py（get_row_selector_by_marker）
