# Ubuntu / Python 兼容性

源码版的支持范围是 Ubuntu 20.04、22.04、24.04、26.04 的常规桌面环境，以及标准 CPython 3.8–3.14。需要与解释器匹配的 Tk、venv，窗口启动需要 GNOME Terminal 和 gdbus。其他 Linux 桌面、PyPy、自由线程 Python、无桌面服务器不在本轮验证范围内。

## 安装和解释器选择

```bash
sudo apt install python3 python3-tk python3-venv gnome-terminal fonts-noto-cjk libglib2.0-bin libatspi2.0-0
bash install-ubuntu.sh
bash start.sh
```

默认使用系统 `python3`。指定其他已安装解释器：

```bash
SSHMT_PYTHON=python3.12 bash install-ubuntu.sh
bash start.sh
```

安装脚本创建 `.venv`，启动脚本优先使用它。修改解释器版本时，若 `.venv` 与所选 Python 的主次版本不同，安装会停止；先将旧环境移走再安装，例如 `mv .venv .venv.backup`。目标与启动配置保存在用户数据目录，不在虚拟环境中。

显式设置 `SSHMT_PYTHON` 启动时，直接使用该解释器；调用者须为它装好 Tk 和项目依赖。Ubuntu 的 `python3-tk` 通常对应系统 Python，自行安装的新 Python 也须具备自身可用的 Tk。

Python 3.8 使用 Paramiko 3.x 和兼容的 setuptools；Python 3.9+ 使用 Paramiko 3/4。pip 根据解释器和平台选择兼容的间接依赖版本。首次安装需要访问软件源和 PyPI。

## 本轮修改

- 将最低 Python 版本降至 3.8，替换旧版本不支持的类型注解及资源加载接口；图标继续包含在源码、wheel 和冻结包中。
- 没有 `os.pidfd_open` 时，通过 `/proc` 的进程启动时间和状态识别 helper 退出，避免 PID 重用或僵尸进程让已关闭窗口继续占用记录。
- 修复 Tcl/Tk 9 空下拉框的选择读取，兼容 Tk 8.6 / 9。
- 修复旧桌面校验器对特殊路径中双引号的处理；仍通过真实 Gio 启动校验菜单入口。
- 构建平台标签不再依赖 Python 3.10 新增的接口；Python/Tcl/Tk 许可证按实际版本定位，Paramiko 3 无需收集不存在的 invoke。
- 修复测试服务器先关闭 shell 再确认 SSH 请求的竞争，以及 Python 3.8 缺少的 PTY 退出码转换接口。

## 自动验证

`.github/workflows/compatibility.yml` 分别测试七个 CPython 版本，以及四个 Ubuntu 版本的系统 Python。Ubuntu 任务执行安装脚本、完整测试、真实 GNOME 标签页冒烟检查和项目外 wheel 资源检查。

本轮实测记录见[验证记录](verification.md)。自动测试包含本地 SSH 协议、PTY、tmux、配置及窗口管理和 X11 图标检查；容器使用 Xvfb 显示，不等同于真实桌面窗口管理器或开发板验收。

## 可分发二进制

```bash
# 在目标 Ubuntu x86_64 构建；需先完成源码安装依赖。
sudo apt install binutils libpython3-dev
# 也可以指定系统支持的 Python。
SSHMT_PYTHON=python3 bash packaging/build-ubuntu.sh
```

自行编译的 Python 还须启用共享库（`--enable-shared`）；使用非系统版本时，构建依赖也须匹配该版本。

已有 `.build-venv` 的 Python 主次版本必须与所选解释器一致，否则先移走旧环境。输出文件名标明实际构建的发行版；PyInstaller 的 Python 依赖与平台系统库共同决定二进制兼容性。

源码兼容范围不代表一个冻结包可以跨所有 Ubuntu 版本运行。较新 Ubuntu 构建的包可能依赖更高的 glibc；应分别在目标发行版构建并验收。原 Ubuntu 24.04 测试包仍只按原记录描述，不能据此宣称可在 20.04 或 22.04 运行。
