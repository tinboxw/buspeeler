# 运行、开发与验收

当前版本：0.1.0。首版研究工作台，不能视为已通过实车验收的控制协议产品。

## 用户运行

Windows 发行目录中双击 `Buspeeler.exe`；Linux 运行目录中的 `Buspeeler`。两者会启动本机服务并打开浏览器。不要单独移动可执行文件，必须保留同目录的 `_internal`。

- 默认地址 `http://127.0.0.1:8765`；启动入口自动提供本机访问凭据。直接输入地址没有凭据时，请重新使用启动入口。
- 默认数据目录：Windows `%LOCALAPPDATA%/Buspeeler`，Linux `~/.local/share/Buspeeler`。可用 `--data-dir` 指定目录，`--port` 指定端口；`--no-browser` 用于自动验收。
- 页面关闭后采集继续。先在页面停止采集，再关闭应用终端；异常退出的数据留在数据目录的 `captures`，下次启动标记中断，可重新导入完整分块。
- 发行包携带 Python、页面和 C++ 采集器，不依赖 Node.js、Python 安装或云服务。设备驱动和厂商 SDK 不随包分发。

推荐操作：创建项目 → 载入合成演示熟悉页面 → 导入实测 CAN 与参考 → 分析候选 → 登记发现会话和证据来源 → 参考匹配 → 独立会话验证 → 导出。

## 输入格式

CAN 支持 UTF-8 的 `candump -L` 与逐行 JSON。首版单次导入上限 64 MiB、50 万帧；候选搜索按所选时间窗口执行，单次至多 1 万帧；实时采集每一万帧分块，同一采集任务的分块不能用来相互独立验证。

```text
(0.100000) can0 390#0000010000000000
(0.200000) can0 123##1000102030405060708090A0B
```

```json
{"timestamp":0.1,"channel":"can0","can_id":912,"extended":false,"fd":false,"data":"0000010000000000","time_source":"import","hardware_timestamp":null,"dropped":null}
```

EST 参考文件为 JSONL，每行含 `timestamp` 与 `raw`；`raw` 为完整 `$OBD-RT,...` 行。以 EST580 内部版手册的 39 字段配置解析；40 字段与未知行只保存、不猜测。

独立测量文件示例：

```json
{"timestamp":0.1,"field":"driver_door","value":1,"quality":"valid"}
{"timestamp":10.0,"field":"driver_door","value":null,"quality":"unknown","expected_valid":false}
```

质量可为 `valid`、`unknown`、`unsupported`、`excluded`。`expected_valid:false` 需来自独立观察，明确表示目标信号此时应失效；参考仪器自身缺测只标质量未知，不能自动当成目标失效证据。导入页面登记来源组、单位、测量误差、时间不确定度和时钟映射。时间映射为 `CAN时间 = 参考时间 × clock_scale + clock_offset`；候选的 `lag` 是参考相对 CAN 信号的额外延迟。不会跨缺口做线性插值。

`R25` 按 25% 保存，方向字母另存；左右映射须实测。超过 100 的值保留并警告，不能直接作为角度。

## 设备接入

当前代码具有 Linux SocketCAN 接收后端及两品牌的 legacy ControlCAN ABI 接收后端。**ABI 模拟测试不是实机支持证明。** 目标为 CANalyst-II、周立功 USBCAN2；设备硬件版本、SDK、Linux 支持和真实监听行为仍需核验。

SocketCAN 仅接入由系统管理员事先配置为 UP、经典 CAN、LISTEN-ONLY 的接口。采集器读取内核配置并定期复核，不自行修改接口、速率或系统权限。首版不开放实机 FD。

厂商设备由技术人员将设备配置保存到应用数据目录 `devices/<配置名>.json`；页面会列出配置。厂商库必须是当前平台/位数的合法 SDK。两个品牌分别启动独立进程，用绝对路径加载，不能把一个品牌的库替代另一个。

下面是字段模板，**不能直接作为已验收配置使用**。型号、驱动、device_type、timings、SDK 哈希及监听证据均需从实际设备和 SDK 确认：

```json
{
  "backend":"zlg",
  "model":"实际型号和硬件版本",
  "system":"Windows",
  "driver_version":"实际驱动版本",
  "abi":"legacy-controlcan-v1",
  "library":"D:/vendor/zlg/ControlCAN.dll",
  "library_sha256":"实际 SHA-256",
  "device_type":4,
  "timings":{"500000":[0,28]},
  "listen_only_evidence":"台架报告路径/编号；证明配置模式下不发送数据、ACK 或主动错误帧"
}
```

此模板的 type/timing 数字仅展示格式，不替代具体 SDK 的参数定义。现代 ZCAN API 或其他 ABI 不适用。SDK 缺失、哈希不符、配置不全时拒绝启动；不回退普通模式。双通道在同一厂商进程内打开同一设备，避免重复开关设备。

串口可选 9600/38400/115200 波特率、数据位、校验与停止位，默认值仅是页面预填。打开前关闭 DTR/RTS 和流控，不调用写接口；仍需实测串口打开是否影响设备。EST 在 CAN 侧可能主动诊断，必须单独确认。

## 验证与发布

候选状态为 candidate → reference_matched → pending_validation → validated → published；未通过为 rejected，旧的未发布修订为 invalidated。规则、复用、无效码、有效时间或来源变化都生成新候选版本。已发布版本不可改写。

验证前冻结误差、范围、最少样本数及四类操作安排。服务端核验独立会话、独立来源、单位与精度、范围覆盖、失效和超时操作证据。同源 EST 输出、演示数据、未确认车型范围及相同原始数据不能正式验证。旧验证数据用于修订后会并入发现数据来源，下一轮需新验证会话。

DBC 只表达可无损表示的线性定义；VA3 固件的整数/条件后处理保留在内部模型中，并阻断线性导出。导出时重新导入 DBC，逐帧核对内部解码。实验包带 `EXPERIMENTAL.dbc`；正式包带 `validated.dbc`、适用清单和报告。无效码、新鲜度、来源等条件必须连同清单使用。

不自动恢复未知 CRC，不将高熵认定为校验。复用支持人工定义单层选择器；导入 DBC 中的复杂/浮点定义列入未支持项。ISO-TP 仅对用户确认普通寻址的单条报文做被动重组，缺片、超时、序号错误分开标记，不发流控。

## 开发与打包

使用 Python 3.12、Node.js 22 和 C++17 编译器。依赖锁为 `requirements.lock` 与 `frontend/package-lock.json`；不要依赖机器全局 Python 包。

**一键发行构建：** Windows 运行根目录 `build.cmd`，Linux 运行 `bash ./build.sh`。需预先安装 CMake 与上述开发工具；Windows 可使用 MinGW，或在 Visual Studio C++ 开发终端运行。脚本不安装驱动、不修改全局工具环境。可用 `BUSPEELER_PYTHON` 指定启动 Python 的绝对路径。

脚本先检查工具，复用或创建独立虚拟环境，按锁文件安装依赖，检查依赖一致性并运行测试，然后依次构建页面、C++ 采集器与 PyInstaller 应用，最后生成平台压缩包及 SHA-256 文件。任一步失败均返回非零退出码，不继续发布后续产物。默认输出为 `dist/windows` 或 `dist/linux`，重复构建会替换该目录中同名发行包。

参数：`--check` 只检查工具；`--cache <目录>` 指定缓存；`--dist <目录>` 指定输出。Windows 默认复用 `D:/dev-cache/buspeeler/shared`；本项目 WSL 默认复用对应 `/mnt/d` 缓存；其他 Linux 使用 `${XDG_CACHE_HOME:-~/.cache}/buspeeler`。首次构建需要下载依赖，离线构建需事先备齐缓存；用户运行发行包不需要开发环境或网络。

以下为按需单步开发命令，日常发行构建直接使用上述入口：

```powershell
python -m venv D:\dev-cache\buspeeler\shared\python312
D:\dev-cache\buspeeler\shared\python312\Scripts\python.exe -m pip install --cache-dir D:\dev-cache\buspeeler\shared\pip -r requirements.lock
cd frontend
npm ci --cache D:\dev-cache\buspeeler\shared\npm
npm run build
cd ..
D:\dev-cache\buspeeler\shared\python312\Scripts\python.exe run.py
```

采集器开发构建后可用 `BUSPEELER_COLLECTOR` 指定其绝对路径；默认开发目录没有预编译采集器，离线功能仍可用。

```powershell
D:\dev-cache\buspeeler\shared\python312\Scripts\python.exe -m pytest -q
D:\dev-cache\buspeeler\shared\python312\Scripts\python.exe scripts/build_release.py --cache D:\dev-cache\buspeeler\shared\release-windows
```

Linux 使用独立虚拟环境安装相同锁文件，在目标 Linux 上运行同一打包脚本并指定缓存目录。目标基线为 Windows 11 x64、Ubuntu 22.04 x64；不能在 Windows 上生成或声明已验证 Linux 原生包。CI 包含两端构建任务，工作流存在不等于已经运行成功。

固件索引可用 `python scripts/firmware_catalog.py` 重新生成。脚本只读仓库原始固件，生成 94 个文件的元数据和八条已审阅的 VA3 目标信号局部静态候选；不声称恢复所有 RT 字段。

## GitHub Actions

配置文件为 `.github/workflows/build.yml`，工作流名称为 `Build Windows and Linux packages`。推送分支/标签、PR 和手动运行均可触发；手动运行要求该工作流先合入默认分支。两端独立执行，其中一端失败不会取消另一端。

- Windows Server 2022 / Ubuntu 22.04 x64，Python 3.12、Node.js 22，显式检查本机 x64 GCC 与构建工具，避免模拟采集测试因缺少编译器而跳过。
- 缓存仅包含按锁文件和系统区分的 pip/npm 下载，不缓存虚拟环境、采集数据或旧发行目录。
- 使用 `scripts/build.py` 完成依赖检查、16 项测试、页面/C++/应用构建与归档。
- 使用 `tests/packaged_smoke.py` 核验归档和 SHA-256，在临时数据目录中以精简 PATH 启动发行程序，检查页面及已有独立验证/DBC 发布/修订门槛。测试只用合成数据，结束后关闭程序。
- 通过后上传 `Buspeeler-windows-x64` 和 `Buspeeler-linux-x64`，附带版本化压缩包与 SHA-256；保留 14 天，运行摘要提供下载入口。Linux 应用放在 tar.gz 内，避免外层 Artifact ZIP 丢失执行权限。

版本号读取 `pyproject.toml`，文件名例如 `Buspeeler-0.1.0-Windows-x64.zip` 和 `Buspeeler-0.1.0-Linux-x64.tar.gz`。标签触发同样上传 Actions 产物，不自动创建 GitHub Release。工作流只申请源码读取权限，无需配置额外 Secret；尚未在 GitHub 运行成功前，不能将本地检查称为云端构建通过。

## 接口与实现结构

- `buspeeler/models.py`：版本 1 内部帧、信号、参考与验证计划模型。
- `buspeeler/service.py`：证据来源、修订、验证、发布门槛；不能由页面直接改验证状态。
- `buspeeler/app.py`：项目、导入、任务、标注、采集、验证、导出接口，均在 `/api` 下。
- `buspeeler/jobs.py`：单工作进程执行分析；最多四个待处理任务。任务输入固定，结果失败可重跑。
- `collector`：独立只收进程，stdout 为 Frame JSONL，stderr 为状态日志，stdin `stop` 或 EOF 停止。
- `profiles`：可审计的固件目录与候选规则；不包含可刷写固件。

本机 API 绑定回环地址，检查 Host、Origin 与启动凭据。SQLite 由主应用写入；原始文件按 SHA-256 存储，分析前检查输入完整性。

## 尚需外部验收的项目

两款真实设备的具体变体与两端驱动、监听电气行为、真实 EST 固件/串口参数、2023 VA3 MT 同步记录、八类目标信号的独立验证、干净目标系统的驱动安装与长时间采集。缺少这些材料时，软件可运行和模拟测试通过都不能作为实车验收结论。

## 本次软件验证记录（2026-09-22）

- Windows 11：16 项自动测试通过；C++ 采集器构建通过；前端类型检查与生产构建通过。
- WSL Ubuntu 20.04 / Linux x64：相同 16 项测试通过，原生 C++ 与 PyInstaller 发行包构建通过。此结果不等于干净 Ubuntu 22.04 已验收。
- Windows/Linux 发行包：移除开发工具搜索路径后启动，通过浏览器创建项目、合成数据、波形、后台统计/搜索、实验包下载、未验证发布拦截和刷新恢复测试。
- 模拟 SDK 覆盖只收接口、监听模式参数及双通道；模拟串口覆盖原始记录、解析、停止和会话归档。未使用真实 CAN 设备或车辆。
- 98 份原始手册、固件及相关资料哈希与实施前清单一致。
- 构建仍有非阻断提示：前端单包较大；测试依赖给出弃用提示；PyInstaller 列出不在当前平台使用的动态库/可选导入提示。已运行的页面、分析和导出路径未观察到相关错误。
