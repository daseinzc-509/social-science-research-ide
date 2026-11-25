# SRA Desktop — CI 与 Release 自动化流水线

项目包含两条职责清晰的 GitHub Actions 流水线，兼顾日常迭代的快速反馈与发布前的真机严苛验收。

## 1. 日常持续集成（`.github/workflows/ci.yml`）

触发条件：任意分支 `push`、`pull_request` 或手动 `workflow_dispatch`（耗时约 3–5 分钟）。

1. **`python-tests`（`ubuntu-latest`）**
   - 运行 `scripts/check_release_safety.py source .` 源码隐私安全门禁；
   - 字节码编译检查 `python -m compileall -q src scripts installer/macos`；
   - 运行 19 份离线跨平台回归测试（涵盖 API 契约、P0 并发与防孤儿加固、P1 服务分层、P2 FTS5 检索 / Projects Matrix / SSE 任务流、MD3 桌面端与证据跳页契约、安全分发、裁剪与 DMG 打包策略）。
2. **`desktop-compile`（`windows-latest` `win-x64` + `macos-26` `osx-arm64`）**
   - 使用 .NET 10 SDK 编译并自包含发布 `net8.0` / Avalonia 12 桌面端，确保双端代码零错误编译并产出轻量前端 Artifact。
   - 不在每次普通 push 中执行重型 PyInstaller / Docling 后端打包，保持反馈敏捷。

## 2. 双端完整打包与发布（`.github/workflows/release-desktop.yml`）

触发条件：
- **手动 `workflow_dispatch`**：仅在 Actions 内部产出 Windows + macOS 预览包与体积审计报告（保留 21 天），**不创建公开 GitHub Release**，适合发版前验证。
- **推送 `v*` 标签**（例如 `git tag v0.2.2 && git push origin v0.2.2`）：在双端打包与全部真机自检通过后，自动创建一个包含 Windows 与 macOS 全套产物的 GitHub Release：
  - 标签包含预发布后缀（如 `v0.2.2-rc.1`、`v0.2.2-beta.1`）→ 自动附加 `--prerelease` 标记为 Pre-release；
  - 正式语义化标签（如 `v0.2.2`）→ 自动从根目录 `CHANGELOG.md` 提取对应版本说明，发布为正式 Release。

### 打包阶段内置的真机自检
1. **冻结后端离线探针**：直接运行打包产出的 `sra-backend(.exe) --self-test-bundle --profile full`，验证合成 PDF 解析、PDFium 渲染、OpenCV 解码、Docling Heron 处理器与 RapidOCR 清单完整无缺。
2. **模型工作进程分发校验**：执行 `sra-backend(.exe) --sra-model-worker --help`，确认不误启 API 或联网下载权重。
3. **真实后端启动与鉴权冒烟**：运行 `scripts/smoke_backend.py`，验证随机回环端口就绪、隔离临时数据目录、以及无令牌/错令牌请求一律返回 `401`。
4. **Windows 真机安装/卸载保数据验证**：真实静默运行 Inno Setup 安装器 → 验证后端可执行文件已安装 → 静默运行 `unins000.exe` → 验证 `%LOCALAPPDATA%\SRA` 哨兵文件未被删除。
5. **macOS `.app` 签名与 `.dmg` 校验**：组装 `SRA.app` → `codesign --verify --deep --strict` → `create_macos_dmg.py` 创建并通过 `hdiutil verify` 校验。
6. **体积审计与 `SHA256SUMS`**：生成文件级体积报告并校验单文件不超 GitHub Release 上限。

## 本地验证命令

```powershell
# 全量测试（含所有后端、打包与前端契约回归）
conda run -p .conda-env python -m pytest -q tests

# Release 编译（要求 0 警告 0 错误）
dotnet build .\desktop\SRA.Desktop\SRA.Desktop.csproj -c Release

# 源码隐私门禁
conda run -p .conda-env python scripts\check_release_safety.py source .
```
