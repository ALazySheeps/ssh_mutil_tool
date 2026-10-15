# 验证记录与 Ubuntu 发布验收

前半部分保留 2026-10-11 的历史验收记录；最新兼容性验证见本文件末尾。

验证日期：2026年10月11日。开发主机为Windows；Python3.13隔离环境、Paramiko4.0.0、Tk8.6；另有Ubuntu24.04.4 WSL2、Python3.12.3、glibc2.39。Windows Bash语义测试使用MSYS Bash5.3，Linux测试使用Ubuntu原生Bash及PTY。

## 自动化测试

执行命令：`python -m unittest discover -s tests -v`。本机设置 `SSHMT_TEST_BASH` 指向MSYS Bash，运行配置校验、持久化、SSH认证和指纹、本地真实SSH通道脚本传输、阶段控制协议、调度、编辑器和GUI恢复等测试。实际最终数量以本文件末尾的最终记录为准。

Windows测试中Linux socket与PTY测试跳过；Ubuntu WSL中全部执行。SSH集成测试服务器真实使用Paramiko协议；单独的Linux PTY测试执行了与实际远端相同的加载器，覆盖150 KiB脚本、source/export、中文与字面目录以及Ctrl+C。仍不把这些测试当作GNOME Terminal与RDK X5的端到端实测。

## 发布前必须补齐

- [ ] Ubuntu22.04 x86_64：目录模式包在无Python/pip的干净桌面解压运行。
- [ ] Ubuntu24.04 x86_64：同一包实际运行及图形资源加载。
- [x] Linux Unix socket0700/0600权限、同用户检查、拒绝超长请求及服务关闭清理。
- [ ] 三窗口：ROS节点、Python脚本、Shell脚本；环境继承和工作目录正确。
- [ ] Ctrl+C、方向键、交互sudo、中文输出和终端缩放。
- [ ] GUI退出后活动终端继续，延迟任务取消，程序退出后保留结果。
- [ ] 网络断开后状态明确，任务不自动重启，指纹变化会拒绝。
- [ ] 六窗口持续输出30分钟，没有读取死锁或无限内存增长。
- [ ] 冷启动不超过3秒、GUI空闲内存目标100 MiB；记录硬件、版本、包体和实测值。
- [ ] 中文/空格安装目录、远端目录和应用菜单入口。
- [ ] 检查冻结包的第三方许可证文件及构建依赖清单完整。

已生成Ubuntu24.04 x86_64测试包，包含Python/Tk/SSH依赖和第三方许可。源码GUI在WSL的一次导入与构建测量为2.765秒、RSS50.6 MiB；冻结包运行3秒后RSS51.8 MiB、进程存活且无错误输出。这些是单次WSL样本，不能替代干净真实桌面的冷启动及长期资源验收。

## 最终记录

Ubuntu24.04 WSL最终自动化运行共47项，全部通过、无跳过。Windows/MSYS运行47项，42通过、5项Linux-only测试跳过。包含SSH双向回压回归（256 KiB脚本、3 MiB先行输出、32 KiB发送窗口）、helper失联、损坏状态备份、深嵌套JSON以及大整数校验。

普通Python包已在隔离环境构建并安装，从项目外工作目录验证模块入口、helper模块分流及随包示例资源。Python源码编译检查和全部启动/构建脚本的Bash语法检查通过。

独立只读代码审查发现4项重要问题，均补充失败回归并修复；另一个安装包入口问题也经过回归验证。GUI按运行ID保存历史状态索引的清理是后续小优化，终端输出不存入该索引。

已在Ubuntu24.04 WSL完成PyInstaller6.22.3目录模式构建、依赖及原生系统库许可证收集。冻结包的命令行、GUI进程及helper入口验证通过，helper能脱离外部Paramiko安装处理通信失败。该环境缺少GNOME Terminal，无法执行真实多窗口启动。Ubuntu22.04未验证，测试包不宣称兼容22.04。

真实Ubuntu桌面、RDK X5程序、交互sudo、缩放、断线和六窗口长期性能仍未验收，不能将测试包标为已完成正式平台发布验收。

## 2026-10-14 Ubuntu / Python 兼容性验证

分支：`feat/ubuntu-python-compat`。源码支持范围及安装、构建限制见[兼容性说明](compatibility.md)。

| 环境 | Python | 完整自动测试 |
| --- | --- | --- |
| Ubuntu 20.04 官方容器，系统 Tk 8.6，Xvfb | 3.8.10 | 141 项通过，无跳过 |
| Ubuntu 22.04 开发主机，系统 Tk 8.6 | 3.10.12 | 141 项通过，无跳过 |
| Ubuntu 24.04 官方容器，系统 Tk，Xvfb | 3.12.3 | 141 项通过，无跳过 |
| Ubuntu 26.04 官方容器，系统 Tk，Xvfb | 3.14.4 | 141 项通过，无跳过 |
| Ubuntu 22.04 隔离解释器，Tk 8.6 | 3.9.25 | 141 项通过，无跳过 |
| Ubuntu 22.04 隔离解释器，Tk 9 | 3.11.15、3.12.13、3.13.15、3.14.7 | 各 141 项通过，无跳过 |

测试命令为 `python -m unittest discover -s tests -v`，容器加 `xvfb-run -a`。含实际 SSH 通道、Linux PTY、真实 tmux 保活/重连/关闭、GUI、进程回收、原生 X11 图标及 Gio 桌面入口启动。Ubuntu 22.04 的 tmux 测试使用临时提取的 tmux 二进制；容器使用发行版 tmux。

另外，Ubuntu 20.04 / 22.04 的真实 GNOME Terminal 冒烟检查通过：两标签页正常创建和管理，短命令结束后标签页移除。Ubuntu 20.04 验证了 `install-ubuntu.sh` 从空环境安装、wheel 安装及项目外资源读取；3.8/3.9/3.11/3.12/3.13/3.14 的额外隔离环境也通过 wheel 安装与项目外 GUI 模块/内置图标读取。安装脚本拒绝混用既有虚拟环境与不同版本 Python 的检查通过。

Ubuntu 20.04 / Python 3.8 完成 PyInstaller 目录式构建、第三方许可证收集及打包。冻结 GUI 在 Xvfb 下保持运行至 4 秒超时，从项目目录外启动后原生窗口的 256 像素图标及 `WM_CLASS=Sshmultitool` 检查通过。未将该包作为跨 Ubuntu 通用发行包发布。

CI 定义增加了 CPython 3.8–3.14 和 Ubuntu 20.04/22.04/24.04/26.04 两组独立矩阵；本次在本地执行上述验证，尚未触发远端 GitHub Actions。YAML、Bash 语法、源码编译及差异空白检查通过。原 GUI 文件的 CRLF 保持不变，差异检查使用 `core.whitespace=cr-at-eol`。

这些结果不等同于所有 Python × Ubuntu 组合均已逐一测试，也不代替 Wayland 桌面、RDK X5 实际业务、断网及长期运行验收。源码兼容范围不代表原 Ubuntu 24.04 冻结包可以运行在较旧 Ubuntu。

## 2026-10-14 界面布局优化

分支：`feat/ui-refresh`。按 tkinter-ttkbootstrap-modern-ui 技能的布局、层级和主题原则优化，使用原生 ttk 实现，未增加第三方界面运行依赖。

- Ubuntu 22.04 / Python 3.10 / Tk 8.6：完整 143 项通过，无跳过。
- Ubuntu 22.04 / Python 3.11 / Tk 9：完整 143 项通过，无跳过。
- Python 3.8：源码编译和终端参数的 4 项测试通过；本轮未重新执行四发行版矩阵。
- 主界面 1160×780、1040×720，以及配置编辑、四槽位编辑弹窗完成截图检查。默认折叠日志以扩大表格区域；日志折叠时接收和保留新事件的真实 GUI 回归先失败、修复后通过。
- 完整测试发现以 `-` 开头的随机令牌触发 argparse 退出码 2，已用真实子进程重现并改为 `--token=<值>`；终端定位前缀同步改为按 helper 参数边界提取。新增回归验证该令牌能进入 helper 模式。
- 独立只读审查未发现剩余重要问题；wheel 构建、项目外新增 ui 模块/图标读取、Bash 与 Python 语法和差异检查通过。

[主界面示例](images/ui-overview.png)使用临时演示配置，不包含密码或用户保存的配置。Wayland、Windows 实际桌面及其他字体/DPI环境的视觉效果未在本轮重新验收。
