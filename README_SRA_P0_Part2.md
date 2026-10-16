# SRA P0 全量测试稳定性修复 · 第二部分

## 原因及修复

1. .github/workflows/release-desktop.yml
   原 Release 工作流提供 `lean` 选项，但脚本
   `scripts/build_backend.py` 的 POLICIES 仅为
   (`conservative`, `compatible`)；选 lean 会被构建器拒绝。
   统一 Release 的输入、预检和裁剪逻辑，只允许实际支持的策略。
   Git Tag 仍默认为 conservative；不会恢复未经离线验收的 lean。

2. tests/test_lean_collection.py
   保留原文件路径，改为检查当前支持的策略、Docling / Transformers /
   RapidOCR 核心依赖、禁用的危险裁剪、离线冻结探针、以及失败关闭。
   保留原先对合成文件树上的类型存根裁剪的安全测试。

3. tests/test_dependency_collection.py
   更新额外引用旧 lean 选项的关联回归测试。保留真实工作流 Bash
   参数仿真，验证 Windows/macOS × conservative/compatible 四种组合。
   这防止改好 5 个失败后产生额外的绿变红。

4. tests/test_region_review_v5_desktop_contract.py
   不再依赖根目录已不存在的 apply_region_review_v5.py 迁移脚本；
   直接检查实际 SraApiClient.cs 中的四个 v5 方法、
   API 路径、snapshot digest、外发同意和费用同意字段。
   其他三项区域复核安全测试保持原样。

## 安装前检查

- 此包是第二部分，假设已使用第一部分（Part1）的修复。
- 先执行 git status --short 保存未提交改动。
- 参考 BASELINE_SHA256.json 检查四个原文件是否发生额外修改。
- ZIP 按项目根目录的路径保存，解压时无需再套文件夹。

## Windows PowerShell

cd "D:\Social Science Research IDE"

Expand-Archive `
  -LiteralPath "$env:USERPROFILE\Downloads\SRA_Test_Stabilization_Part2_Full_Replacement.zip" `
  -DestinationPath . -Force

conda run -p .conda-env python -m pytest -q `
  tests/test_lean_collection.py `
  tests/test_dependency_collection.py `
  tests/test_region_review_v5_desktop_contract.py

conda run -p .conda-env python -m pytest -q tests

dotnet build desktop/SRA.Desktop/SRA.Desktop.csproj -c Release

## 验证记录（当前环境，非完整用户仓库）

- PyInstaller 策略及离线探针：6 tests passed
- Windows/macOS 四种 Release Bash 参数组合：4 tests passed
- v5 真实 C# 客户端字段对照公开 API 端点：通过，
  并通过故意移除费用同意字段的反向断言
- Python 语法与 YAML 解析：通过
- 原始文件、替换文件 SHA-256 与 ZIP CRC：通过

未执行（需在完整项目/Windows 验收）：
- 两项 prune_release_assets 的真实实现测试：精简源码包没有提供该脚本
- 区域复核完整四项测试：精简源码包缺少实际区域复核 UI/服务文件
- 完整 pytest 与 dotnet 编译
- Release 冻结安装包运行、Docling 离线 PDF/OCR 推理

## 后续

只有全部回归测试、Windows 编译、实际模型下载/离线 PDF
导入通过后，才创建新的 Release Tag。不要为了使测试变绿而
恢复 lean 模式、取消外发确认或忽略真实 PDF 运行失败。
