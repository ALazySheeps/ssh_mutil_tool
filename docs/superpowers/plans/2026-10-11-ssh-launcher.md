# SSH 多终端工具实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Ubuntu 开发电脑提供可保存配置、恢复上次选择并一键启动 RDK X5 多个 SSH 终端的图形工具。

**Architecture:** Tkinter 主界面负责编辑与调度，独立 helper 负责每窗口 SSH 和 PTY 桥接。受限本机 socket 分发一次性启动快照及凭据；JSON 存储配置和偏好，系统终端负责渲染和交互。

**Tech Stack:** Python 3.10+、Tkinter/ttk、Paramiko、JSON、unittest、PyInstaller。

**Spec:** [已确认规格](../specs/2026-10-11-ssh-launcher-design.md)

## 全局约束

- 首版正式支持 Ubuntu 22.04、24.04 的 x86_64 桌面及 GNOME Terminal，Windows 为可选后续平台。
- SSH 远端无需安装本工具或常驻代理；环境命令在每个窗口的同一 Bash 中分别执行。
- 密码不得写入进程参数、环境变量、文件或日志；仅本轮内存保留。
- 配置格式版本1，最多32窗口，每文件最多1 MiB，延迟0至3600秒。
- 目录字段只允许绝对路径及 `~/` 路径，不执行字段中的变量或命令替换。
- 配置显式保存；目标、选择立即原子保存；导入及应用启动不执行配置。
- 不自动信任主机、不自动重连重启、不承诺远端强制停止或ROS就绪。
- 当前工作目录没有Git仓库，提交步骤暂不适用；不擅自初始化仓库。

## 重点审查条件

1. 空格、中文及shell字符出现在安装路径、标题或目录时，不能导致本机命令注入；任务2及4验证。
2. 恶意、损坏、字段拼错或超限JSON不能覆盖已保存数据；任务1验证。
3. 同时领取凭据、令牌重放、领取超时及GUI退出不能泄露密码或挂住helper；任务3验证。
4. 日志持续输出、Ctrl+C、PTY缩放及断线不能造成死锁或破坏本机终端模式；任务4验证。
5. SSH预检成功但某窗口随后失败时，状态必须反映实际阶段，其他窗口按规格继续；任务5验证。

## 文件布局

| 路径 | 职责 |
| --- | --- |
| `pyproject.toml`、`requirements.txt` | 包、入口点及依赖约束 |
| `ssh_multi_tool/__main__.py` | GUI与helper入口分流 |
| `ssh_multi_tool/models.py` | 目标、配置和窗口数据校验 |
| `ssh_multi_tool/storage.py` | 原子写入、导入导出及偏好 |
| `ssh_multi_tool/scripts.py` | 安全构建远端Bash脚本 |
| `ssh_multi_tool/broker.py` | 本机凭据服务、令牌和状态事件 |
| `ssh_multi_tool/ssh.py` | 连接、信任与预检 |
| `ssh_multi_tool/helper.py` | 独立会话执行及终端桥接 |
| `ssh_multi_tool/terminal.py` | GNOME Terminal启动适配 |
| `ssh_multi_tool/launcher.py` | 启动调度、快照与状态 |
| `ssh_multi_tool/gui.py`、`editor.py` | 主界面与配置编辑器 |
| `tests/` | 自动化校验及本地SSH集成测试 |
| `examples/rdkx5.json` | 可导入示例 |
| `packaging/` | Ubuntu构建脚本、入口及依赖许可汇总流程 |
| `README.md`、`docs/quickstart.md`、`docs/verification.md` | 使用、快速入门及平台验收记录 |

## 任务1 配置与持久化

**文件：** models.py、storage.py、tests/test_storage.py、tests/test_models.py及包元数据。

**接口：** `validate_profile(data: dict) -> dict`、`validate_target(data: dict) -> dict`；`Store(root: Path)` 提供配置、目标、偏好读写及导入导出。返回数据不含密码，写入失败保留旧数据。

- [ ] 写失败测试：往返保存多行中文命令；拒绝版本2、重复ID、33窗口、NaN延迟、未知字段、相对目录及超限文件。
- [ ] 运行 `python -m unittest discover -s tests -v`，确认缺失行为导致失败。
- [ ] 实现校验、权限、同目录原子替换、冲突导入新副本和恢复缺失配置的回退。
- [ ] 验证保存失败不改变旧文件；偏好立即保存；导出无目标凭据。
- [ ] 运行完整测试并确认通过。

## 任务2 远端脚本与终端启动参数

**文件：** scripts.py、terminal.py、tests/test_scripts.py、tests/test_terminal.py。

**接口：** `build_script(profile: dict, window: dict) -> str`；`terminal_argv(helper_argv: list[str], title: str) -> list[str]`。前者保持用户Bash片段，后者只返回参数数组。

- [ ] 写失败测试：公共export、source和cd在同一shell生效；初始化失败阻止任务；空格中文目录有效；目录中的命令替换保持字面含义。
- [ ] 运行测试，确认缺失行为失败。
- [ ] 实现 `set -e`、`pipefail`、HOME基线及目录引用；不依赖交互式bashrc。
- [ ] 测试本机启动参数无密码，恶意标题只是一个参数；缺失GNOME Terminal给出可操作错误。
- [ ] Windows上检查字符串与参数构建；Ubuntu上实际用Bash运行语义测试并记录平台限制。

## 任务3 本机凭据与事件服务

**文件：** broker.py、tests/test_broker.py。

**接口：** `Broker` 发放每任务启动令牌、以令牌领取快照、接收任务事件、撤销待领取令牌及关闭服务；helper领取结果包括目标、密码、脚本和事件认证信息，事件认证不能重复领取密码。

- [ ] 写失败测试：令牌仅可领取一次，60秒到期，错误任务ID及错误用户被拒绝，同时领取只有一个成功。
- [ ] 运行测试确认失败，再实现长度有限的JSON通信协议、超时和任务绑定。
- [ ] Linux使用私有目录Unix socket，校验0700/0600权限及同用户身份；不实现无认证TCP回退。
- [ ] 验证事件与凭据令牌用途隔离，服务关闭能终止阻塞连接，恶意或超长请求被拒绝。
- [ ] 非Linux仅执行可移植协议与令牌测试；Unix权限测试在Ubuntu执行。

## 任务4 SSH 会话与交互桥接

**文件：** ssh.py、helper.py、tests/test_ssh.py、tests/test_helper.py及测试SSH服务工具。

**接口：** `preflight(target: dict, password: str, known_hosts: Path) -> result` 返回成功、未知指纹或明确错误；`run_session(snapshot: dict, events: callable) -> int` 执行单窗口，输出阶段与退出码。

- [ ] 先写本地Paramiko测试服务器集成测试：错误密码、未知或变化主机密钥、正常退出、非零退出及断线。
- [ ] 运行测试确认行为缺失，再实现严格信任、连接10秒、认证15秒、保活15秒及资源关闭。
- [ ] 用PTY执行远端脚本，凭据只通过broker取得；脚本经SSH发送，不持久落地。
- [ ] 增加输入输出、流控、Ctrl+C及窗口尺寸变化测试；以finally恢复本机终端属性。
- [ ] Ubuntu实测交互sudo、方向键、中文、输出压力和Ctrl+C停止典型节点；测试服务无法替代真实终端验收。
- [ ] 实现会话退出后显示结果并等待回车；GUI离线不阻止已启动任务完成。

## 任务5 调度与主界面

**文件：** launcher.py、gui.py、__main__.py、tests/test_launcher.py。

**接口：** `Launcher.start(target, password, profile)` 使用不可变快照启动；`cancel_pending()` 取消尚未启动任务；线程安全事件队列供GUI通过 `after` 消费。

- [ ] 写失败测试：预检失败不开窗口；延迟相对共同起点；同延迟列表排序；一个窗口失败不取消其他任务；重复活动启动被阻止。
- [ ] 验证失败后实现后台预检、调度取消、状态过渡和有上限事件缓冲。
- [ ] 实现目标编辑、遮蔽密码、测试连接、指纹确认及显式信任更新界面。
- [ ] 实现配置选择、窗口摘要及阶段显示；选项变化立即保存，不在Tk主线程执行网络操作。
- [ ] GUI退出取消待启动任务，已领取凭据的helper继续；状态未知时允许用户明确再次启动。
- [ ] Tk界面冒烟验证启动、刷新、关闭和错误弹窗；Windows可验证GUI布局，但不调用未支持的终端后端。

## 任务6 配置编辑与示例

**文件：** editor.py、examples/rdkx5.json、tests/test_editor_data.py。

**接口：** 编辑器输入配置副本，保存时返回通过校验的配置；取消不改变原配置。窗口使用稳定ID，复制生成新ID。

- [ ] 写失败测试：新增、复制、删除、排序保留正确任务数据；未保存编辑不污染原配置。
- [ ] 测试失败后实现配置名称、说明、公共命令、多窗口工作目录及命令编辑。
- [ ] 实现导入导出、复制删除确认、未保存关闭的保存/放弃/取消，以及启动前保存。
- [ ] 展示公共初始化重复执行、export继承、前台阻塞及导出前检查秘密的提示。
- [ ] 验证示例导入、往返导出及重启恢复上次选择。

## 任务7 分发与交付验证

**文件：** packaging/、README.md、docs/quickstart.md、docs/verification.md、许可证文件。

- [ ] 增加命令行帮助及模块入口冒烟测试，运行完整单元与可运行的集成测试。
- [ ] 提供PyInstaller目录模式构建脚本，收集Tk资源、Paramiko依赖、示例及依赖许可；从可执行入口启动helper。
- [ ] 提供Ubuntu桌面启动入口及一页中文使用说明；缺依赖可定位，不要求用户安装Python/pip。
- [ ] Ubuntu22.04及24.04分别验证解压启动、中文空格路径、SSH交互、多窗口生命周期及配置权限。
- [ ] 六窗口持续输出30分钟，记录实际内存及包体；测量冷启动3秒、GUI空闲内存100 MiB目标。
- [ ] 当前Windows环境无法完成的Ubuntu构建及实机测试逐项写入验证记录，不以单元测试通过替代平台验收。

## 执行方式及交付边界

推荐在当前会话由主执行者顺序实现，无须并行子代理。先完成自动化可验证核心，再进行UI及平台集成。

最终交付源码、示例、使用说明、测试及Ubuntu打包流程。只有实际生成并验证Ubuntu包后，才能将包描述为已可直接交付；如环境缺乏Linux运行能力，应明确源码已完成与Ubuntu验收未完成的区别。

## 执行结果

2026年10月11日：任务1至6实现完成；任务7完成源码分发与Ubuntu24.04冻结测试包。Ubuntu24.04 WSL中47项测试全部通过，包括实际PTY加载器及Ctrl+C；冻结包GUI和helper启动通过。Windows47项中42通过、5 Linux-only跳过。真实GNOME Terminal与RDK X5端到端、Ubuntu22.04及长期性能验收仍未完成，详见 `docs/verification.md` 与 `docs/development-ledger.md`。
