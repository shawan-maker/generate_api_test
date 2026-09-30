# Pre-API Tracer 测试覆盖总结

## 测试文件
`tests/test_pre_api_tracer.py` — 36 个测试用例，全部通过 ✓

## 测试覆盖范围

### 1. _topological_sort_pre_apis 拓扑排序（8 个测试）
- ✓ 线性依赖链 A→B→C
- ✓ 无依赖保持原始顺序
- ✓ 菱形依赖 D→B,C→A
- ✓ 空字典返回空列表
- ✓ 单条记录
- ✓ 完全循环依赖返回空结果
- ✓ 部分循环排除循环节点
- ✓ 依赖图外 API 引用被忽略

### 2. trace_pre_api_dependencies 前置 API 依赖追踪（10 个测试）
- ✓ 基础字段匹配包含 pre-API
- ✓ 无匹配字段返回空 pre_apis
- ✓ 空候选列表返回空结果
- ✓ 多操作不同依赖被收集
- ✓ context_fields 添加 extract
- ✓ 外部传入 value_index 直接使用
- ✓ 不传 value_index 内部构建
- ✓ field_resolutions 正确填充
- ✓ id_producer 排除 create_body 来源
- ✓ create_body/create 不作为前置 API

### 3. resolve_chain 链式依赖追踪（14 个测试）
- ✓ context 字段解析到 pre-API
- ✓ 链式依赖两者都被添加
- ✓ seen_apis 防止循环
- ✓ 深度限制停止递归
- ✓ 空 field_roles 无修改
- ✓ 非 context 字段被忽略
- ✓ create_body 来源被跳过
- ✓ create 来源被跳过
- ✓ 无 '.' 的 source 被跳过
- ✓ query_params 子依赖被添加
- ✓ 自引用子依赖被跳过
- ✓ 噪声 query_params 值被过滤
- ✓ query_params_samples 回退
- ✓ 多个 context 字段各自解析
- ✓ 嵌套字段 dot-separated key 正确取值

### 4. 集成场景测试（4 个测试）
- ✓ 完整追踪带拓扑序排列
- ✓ context_fields 追加缺失 extract
- ✓ id_producer 操作的 create_body 排除
- ✓ 空 core_apis 返回空结果

## 测试特点

1. **完整的边界覆盖**：空输入、单条、多条、循环依赖
2. **真实的业务场景**：多操作依赖不同 pre-API、链式依赖、嵌套字段
3. **防御性测试**：循环防止、深度限制、噪声值过滤
4. **向后兼容**：外部 value_index vs 内部构建
5. **清晰的中文注释**：每个测试都有明确的中文 docstring

## 运行结果

```bash
$ pytest tests/test_pre_api_tracer.py -v
36 passed in 0.17s
```

## 与项目测试风格一致

- ✓ 使用 class-based grouping（类分组）
- ✓ 中文 docstrings（中文注释）
- ✓ Plain assert（简单断言）
- ✓ Helper functions（辅助函数）
- ✓ Mock 对象使用（MagicMock）
- ✓ 与 test_module_discovery.py 风格一致
