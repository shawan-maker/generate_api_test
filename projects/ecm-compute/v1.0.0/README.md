# ecm-compute - 自动化测试脚本包

> 版本: v1.0.0
> 生成时间: 2026-09-23 12:28:39
> 说明: 本目录包含完整的 API + UI 自动化测试脚本，可直接拷贝到其他机器运行。

## 📦 目录结构

```
v1.0.0/
├── api/                    # API 自动化测试
│   ├── config/            # 配置文件（cookie 等）
│   ├── lib/               # 运行时库
│   ├── helpers.py         # 辅助函数
│   └── *_API测试.py       # API 测试脚本
├── ui/                     # UI 自动化测试
│   ├── config/            # 配置文件（cookie 等）
│   ├── lib/               # 运行时库
│   └── *.py               # UI 测试脚本
├── export/                 # 导出的测试资产
│   └── <模块>/
│       ├── *.postman_collection.json
│       ├── helpers.py
│       └── *_params.xlsx
└── README.md              # 本文件
```

## 🚀 快速开始

### 1. 环境准备

```bash
# 安装 Python 3.8+
python --version

# 安装依赖
pip install requests playwright openpyxl
playwright install chromium
```

### 2. 配置认证

**方式 A：使用现有 Cookie（推荐）**

1. 将已登录的浏览器 cookie 导出为 JSON 格式
2. 保存到以下位置：
   - API 测试: `api/config/cookies.json`
   - UI 测试: `ui/config/cookies.json`

**方式 B：从原始目录同步**

如果从生成脚本的原始目录拷贝，cookie 已自动同步到 `api/config/` 和 `ui/config/`。

### 3. 运行测试

#### API 测试

```bash
# 进入 API 目录
cd api

# 运行单个模块测试
python 角色管理_API测试.py

# 运行所有 API 测试
for f in *_API测试.py; do python "$f"; done
```

**报告生成位置**: `api/reports/`

#### UI 测试

```bash
# 进入 UI 目录
cd ui

# 运行单个模块测试（有界面）
python 角色管理.py

# 运行单个模块测试（无头模式）
python 角色管理.py --headless

# 运行所有 UI 测试
for f in *.py; do [[ "$f" != "__"* ]] && python "$f" --headless; done
```

**报告生成位置**: `ui/reports/`

#### 批量运行

```bash
# 返回上级目录（包含 run_suite.py 的目录）
cd ..

# 运行所有 API 测试
python run_suite.py --project ecm-compute --version v1.0.0 --type api

# 运行所有 UI 测试
python run_suite.py --project ecm-compute --version v1.0.0 --type ui

# 运行所有测试（API + UI）
python run_suite.py --project ecm-compute --version v1.0.0 --type all
```

## 📋 已生成的测试脚本

### API 测试脚本
  - `角色管理_API测试.py`

### UI 测试脚本
  - `角色管理.py`

## 🔧 常见问题

### Q1: 运行时提示 "Cookie 过期" 或 "401 Unauthorized"

**原因**: Cookie 已过期

**解决方法**:
1. 重新登录系统，获取新的 cookie
2. 更新 `api/config/cookies.json` 和 `ui/config/cookies.json`
3. 或者从原始目录重新拷贝整个 `v1.0.0/` 目录

### Q2: UI 测试找不到元素

**原因**: 页面结构可能已变更

**解决方法**:
1. 检查目标系统是否更新过界面
2. 联系开发人员确认元素定位器是否需要更新
3. 重新运行发现流程生成新的脚本

### Q3: 如何添加新模块的测试？

**方法**: 在原始目录重新运行发现流程：

```bash
# 在原始目录（包含 core/discovery/run.py 的目录）
python -m core.discovery.run --project ecm-compute --module "新模块名称" --url "/path/to/module"

# 然后将生成的脚本拷贝到本目录
```

### Q4: 如何修改测试数据？

**方法**: 编辑脚本中的 `test_data` 部分：

```python
# 打开 *_API测试.py 或 *.py
# 找到 test_data 字典
test_data = {
    "角色名称": "AT_test_xxx",  # 修改这里
    "描述": "测试角色",          # 修改这里
}
```

## 📊 测试报告

- **API 报告**: `api/reports/<模块>/` - Postman 风格 HTML 报告
- **UI 报告**: `ui/reports/` - 包含截图的 HTML 报告
- **原始日志**: 生成在原始目录的 `projects/ecm-compute/output/` 下

## 🔗 相关资源

- **Postman Collection**: `export/<模块>/*.postman_collection.json`
  - 可导入 Postman 进行手动测试
- **Excel 参数文件**: `export/<模块>/*_params.xlsx`
  - 包含所有测试数据，可用于数据驱动测试
- **Helpers 函数**: `api/helpers.py`
  - 包含数据生成辅助函数，可在其他脚本中复用

## 📝 注意事项

1. **Cookie 有效期**: Cookie 通常有时效，过期后需要重新获取
2. **环境一致性**: 确保测试环境的接口地址与生产环境一致
3. **数据清理**: 测试产生的数据（如创建的角色）需要在测试后手动清理，或运行删除脚本
4. **并发问题**: 避免多人同时运行相同模块的测试，可能产生数据冲突

## 🆘 技术支持

如遇到问题，请联系：
- 开发人员: [填写开发人员联系方式]
- 测试框架维护: [填写框架维护者联系方式]

---

*本文档由自动化测试框架自动生成*
