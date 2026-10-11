# SSH 多终端工具快速入门

在Ubuntu桌面操作，目标设备开启SSH服务。

## 打开工具

Ubuntu24.04 x86_64可先解压测试包 `ssh-multi-tool-0.1.0-ubuntu24.04-x86_64.tar.gz`，在解压目录执行 `./ssh-multi-tool`，无需Python/pip。此包已验证WSL中的GUI及helper启动，真实桌面与X5端到端仍需验收；Ubuntu22.04先使用源码版。

源码版准备依赖并运行：

```bash
sudo apt install python3 python3-tk python3-venv gnome-terminal fonts-noto-cjk
bash install-ubuntu.sh
bash start.sh
```

运行需要GNOME Terminal；缺失时安装 `sudo apt install gnome-terminal`。

## 先验证连接

填写设备名称、IP、端口22、用户名和密码。点击“测试连接”，首次连接通过可信渠道核对指纹，再确认信任。

导入 `examples/smoke-test.json`，检查命令并保存，点击“一键启动”。一个窗口显示环境变量和设备信息，另一个持续输出时间；在第二个窗口按Ctrl+C停止，按回车关闭。

## 创建 RDK X5 配置

点击“导入示例”。在“公共初始化”修改ROS发行版、工作空间路径、ROS_DOMAIN_ID及DDS实现。初始化使用 `source` 和 `export`，每窗口执行一次。

在“终端窗口”新建或编辑窗口，填写远端工作目录及终端命令。多个常驻节点分窗口。工作目录支持 `/绝对路径`、`~/目录` 或留空；延迟只用于错峰。

保存后返回主界面，点击“一键启动”。常驻程序在各终端Ctrl+C停止。执行中只表示命令已经开始，需要自行确认业务是否正常。

## 下次使用与分享

下次自动选中上次目标和配置，只需重新输入密码。导出配置为JSON发给他人，分享前检查自写命令是否含秘密；对方导入后修改设备路径即可使用。

关闭主界面后已有终端继续执行；后台化程序也可能在断线后运行，重新启动前检查已有任务。

WSL或最小化Ubuntu若中文显示为方框，请运行 `sudo apt install fonts-noto-cjk`，关闭工具后重新打开。预编译包也依赖系统中文字体。
