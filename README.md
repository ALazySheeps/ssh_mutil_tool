# SSH 多终端启动工具

在本机选择目标设备及启动配置，一次输入SSH密码，一键打开多个终端，在RDK X5等Ubuntu设备上分别配置环境并启动程序。

首版采用Python、Tkinter及Paramiko。Ubuntu桌面的多终端运行需要GNOME Terminal；Windows目前可编辑配置及测试连接，不提供多窗口启动。已生成Ubuntu24.04 x86_64测试包，并在Ubuntu24.04 WSL下通过47项测试及冻结包GUI/helper启动检查。真实GNOME Terminal与RDK X5端到端验收仍待完成；Ubuntu22.04需另行构建验证。

## Ubuntu 24.04 测试包

使用 `dist/ssh-multi-tool-0.1.0-ubuntu24.04-x86_64.tar.gz`，解压后运行：

```bash
cd ssh-multi-tool
./ssh-multi-tool
```

无需安装Python/pip。Ubuntu默认GNOME桌面通常已有GNOME Terminal，缺失时运行 `sudo apt install gnome-terminal`。可选 `bash install-desktop.sh` 添加应用菜单入口。测试包基于Ubuntu24.04、glibc2.39构建，不承诺在22.04运行。

## Ubuntu 源码版启动

首次准备依赖：

```bash
sudo apt install python3 python3-tk python3-venv gnome-terminal fonts-noto-cjk
bash install-ubuntu.sh
bash start.sh
```

`install-ubuntu.sh`在项目目录创建隔离虚拟环境，首次安装依赖需要网络。之后打开工具不需要联网安装，只需要能访问目标设备。可将命令绑定到桌面快捷方式。

远端需已有SSH服务、Bash、stty及head（Ubuntu默认提供后两项），以及用户程序和ROS依赖。远端不安装本工具，不要求Python或额外常驻服务。

## 操作

1. 填目标名称、IP、端口、用户名和密码，可点击“测试连接”。首次连接核对服务器SHA256指纹后信任。
2. 点击“导入示例”或“新建”。公共初始化填写source、export等命令；新建窗口填写工作目录、命令和延迟。
3. 保存配置，点击“一键启动”。每窗口执行同一份公共初始化，再进入自己的目录并运行程序。
4. 在窗口列表选中一行，点击“关闭窗口”关闭对应终端并断开SSH，尚在等待的窗口会取消启动；也可点击“重启窗口”，验证密码并关闭旧会话后，立即按本轮启动时的配置重新连接执行，不重复等待延迟，也不影响其他窗口。
5. 点击“一键关闭”关闭当前工具打开的全部终端（包括上一轮保留的结果窗口），取消延迟任务及进行中的启动/重启。关闭主界面仍会保留已启动的终端。
6. 也可在各终端Ctrl+C停止前台程序。程序结束后终端保留结果，按回车关闭。

建议先导入 `examples/smoke-test.json` 验证基础连接，再使用RDK示例。ROS示例中的发行版、工作空间、包名和脚本名需要按设备修改，不是开箱可用的实际业务程序。

点击窗口列表下方的“打开远程终端”，可使用当前目标和密码打开一个独立的SSH交互窗口，手动输入命令；无需启动配置，可重复打开，也可连接不同目标。新终端显示在窗口列表中，切换配置后仍保留，支持关闭、重启和一键关闭。选择了启动配置时，新终端会先执行该配置的公共初始化，继承其中通过 `source` / `export` 设置的环境变量和 `cd` 目录，再进入交互Bash；初始化失败会停止并保留错误结果。它不执行任何配置窗口的启动命令；未选择配置时使用远端默认登录shell。

在当前配置的窗口列表中选中一个启用的窗口，点击“启动所选窗口”，立即执行公共初始化、该窗口的工作目录切换和启动命令，不等待配置中的批量启动延迟。其他窗口不受影响；已在等待、连接或执行中的窗口不能重复启动，状态未知时需先关闭。可以逐个启动同一目标、同一配置中的不同窗口；其他目标或配置存在活动任务时须先关闭。

下次打开自动恢复上次保存的目标及配置，密码重新输入。切换已保存目标会清空密码。配置选择即时保存，编辑内容须点击保存。

## 命令规则

公共初始化在每个窗口重复执行，只放环境初始化和快速检查，不要放只应执行一次的硬件重置或常驻程序。

```bash
source /opt/ros/humble/setup.bash
source ~/robot_ws/install/setup.bash
export ROS_DOMAIN_ID=10
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
```

每窗口命令是完整Bash片段，环境和cd在同一shell中延续。默认启用 `set -e` 和 `pipefail`，初始化失败停止当前窗口后续程序。条件语句、函数等仍遵守Bash自身退出规则。多个常驻程序建议分窗口运行，后续命令会等待前一条前台程序退出。

工作目录支持留空、绝对路径和 `~/` 路径。目录字段不展开 `$VAR` 或命令替换。延迟秒数相对本轮共同启动起点，不能保证ROS节点已就绪。

## 生命周期及限制

关闭主界面取消尚未启动的任务，已启动的终端独立继续执行。关闭终端会断开SSH，但不能保证所有远端子进程退出；nohup、后台化及服务启动的程序可能继续运行。

启动配置一次只运行一批活动任务，避免重复运行；可另外打开多个独立远程终端。重开主界面不会接管旧终端，也不会自动启动程序；再次启动前请检查已有终端及远端进程。工具不自动重试程序、不检测ROS节点健康；窗口关闭及重启通过断开SSH结束会话，不保证清理远端后台进程。重启使用各窗口启动时的配置快照；编辑并保存配置后，新开的远程终端及“启动所选窗口”会采用当前配置，已有窗口不受影响；切换目标后需切回对应窗口的原目标并输入密码才能重启。

密码只通过内存及受限本机通信传递，不写进配置、参数或临时脚本。主机指纹变化会阻止连接，需要核验后使用“主机信任管理”更新。配置中的自写命令可执行任意远端操作，导入时须检查全部命令，分享前检查是否含秘密。

## 本地文件

Ubuntu：`${XDG_CONFIG_HOME:-~/.config}/ssh-multi-tool/`。Windows编辑模式：`%APPDATA%/ssh-multi-tool/`。

- `profiles/`：可分享JSON启动配置。
- `targets.json`：设备信息，不含密码。
- `preferences.json`：上次选择、窗口尺寸及连接超时。
- `known_hosts`：工具独立的主机信任记录，不修改系统SSH配置。

损坏配置会跳过并显示原因。导入后先在编辑器检查并保存，导入和选择不会执行程序。

若目标或偏好文件损坏，新数据保存前会把原文件完整备份为同目录的 `targets.json.recovery-*` 或 `preferences.json.recovery-*`，便于手动恢复；备份失败则停止覆盖。

## 开发与测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

没有图形显示时GUI测试会跳过，可用 `xvfb-run -a .venv/bin/python -m unittest discover -s tests -v` 执行GUI冒烟测试。Linux测试包含Unix socket权限和Bash语义。Windows可选设置 `SSHMT_TEST_BASH` 指向MSYS Bash以运行Bash语义测试，这不替代Ubuntu PTY验收。

## 构建可分发 Ubuntu 包

在Ubuntu x86_64运行：

```bash
bash packaging/build-ubuntu.sh
```

输出为 `dist/ssh-multi-tool-0.1.0-<发行版版本>-x86_64.tar.gz`，例如 `ubuntu24.04`。目录式包内包含Python、Tk及SSH依赖，接收者无需Python/pip，仍需GNOME Terminal及图形桌面。在解压目录运行 `./ssh-multi-tool`；可选 `bash install-desktop.sh` 添加应用菜单入口。

构建脚本收集第三方许可证，缺失时停止并要求补齐。构建通过不等于可发布，Ubuntu22.04和24.04应分别按[验证清单](docs/verification.md)验收。

## 文档

- [快速入门](docs/quickstart.md)
- [设计规格](docs/superpowers/specs/2026-10-11-ssh-launcher-design.md)
- [实现计划](docs/superpowers/plans/2026-10-11-ssh-launcher.md)
- [验证记录与平台验收](docs/verification.md)

本项目代码采用MIT许可证，第三方依赖遵守各自许可。

WSL或最小化Ubuntu若中文显示为方框，请运行 `sudo apt install fonts-noto-cjk`，关闭工具后重新打开。预编译包也依赖系统中文字体。
