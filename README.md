# buspeeler

跨车型 CAN/CAN FD 信号逆向分析与 DBC 自动生成系统。

项目采用“被动采集 → 已有定义匹配 → 候选发现 → 引导标定 → 独立验证 → DBC 发布”的路线，目标是输出带适用范围和验证证据的解码规则。

产品目标：**Windows/Linux 一键启动，浏览器完成全部核心操作，支持离线使用**。发布包随附应用运行环境与页面资源，用户无需安装 Python、Node.js 或数据库服务；实机采集仍需受支持的 CAN 设备与驱动。

首版设备适配范围为周立功和创芯科技，按具体型号、系统与驱动组合验收，不代表两家所有产品均已支持。

## 当前状态

已实现首版离线工作台、EST 参考解析、候选分析、验证发布门槛与只读采集器代码。真实设备/驱动组合及捷达 VA3 2023 手动挡尚未实机验收，当前没有已验证车型协议。

运行发行包中的 Buspeeler.exe（Windows）或 Buspeeler（Linux）；开发启动、数据格式与打包见[运行、开发与验收](docs/development.md)。

## 一键构建

准备 Python 3.12、Node.js 22、CMake 和 C++17 编译器后，在仓库根目录运行：

```powershell
.\build.cmd
```

Linux 使用 `bash ./build.sh`。脚本自动安装锁定依赖、运行测试、构建页面与采集器、打包应用并生成压缩包及 SHA-256 文件，结果位于 `dist/windows` 或 `dist/linux`。首次构建需要网络，发行包运行可离线。使用 `--check` 仅检查工具，`--cache <目录>` 指定可复用缓存，`--dist <目录>` 指定产物目录；详细要求见[开发与打包](docs/development.md#开发与打包)。

## GitHub 自动构建与下载

推送代码、创建/更新 PR 时，`Build Windows and Linux packages` 流水线分别在 Windows Server 2022 和 Ubuntu 22.04 上构建 x64 包。也可在仓库 **Actions → Build Windows and Linux packages → Run workflow** 手动触发（工作流须先存在于默认分支）。

构建成功后，从运行摘要的下载链接或 **Artifacts** 获取 `Buspeeler-windows-x64`、`Buspeeler-linux-x64`。每份包含平台压缩包和 `.sha256` 校验文件，保留 14 天。GitHub 下载的外层 ZIP 解开后，Windows 再解压应用 ZIP；Linux 再解压应用 tar.gz，以保留可执行权限。

流水线复用本地一键构建入口，运行自动测试，并检查压缩包、无开发环境路径下的启动、页面资源和 DBC 发布门槛；检查失败的该平台不会上传产物。详细流程与边界见[GitHub Actions](docs/development.md#github-actions)。

## 文档

- [运行、开发与验收](docs/development.md)：启动、输入格式、设备配置、开发构建及尚未完成的实机验收。

- [项目架构与实施路线](docs/architecture.md)：能力边界、采集和数据模型、候选分析与标定、验证发布、DBC 导出、技术栈及分阶段路线。
- [极简部署、技术选型与可视化工作台](docs/deployment-and-ui.md)：跨平台发布形态、技术取舍、页面导航与草图、首版范围、故障恢复及验收标准。
- [EST 串口参考标注与固件分析](docs/est580-reference-and-firmware.md)：手册与固件协议差异、自动参考标注、VA3 样本静态候选及独立验证边界。

## 基本原则

- 默认只读，实车采集必须确认控制器处于 `listen-only` 模式。
- 候选不等于已验证；没有参考信息时，不承诺恢复任意车型的完整原厂 DBC。
- 安全相关控制只能使用独立验证通过且适用条件满足的信号；验证通过也不替代控制系统自身的安全验证。
- 原始数据与验证证据可追溯，未知、失效和超时必须明确表达。

## 许可证

见 [LICENSE](LICENSE)。第三方协议、日志和工具遵循各自的授权与许可证。
