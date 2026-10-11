# 验证记录与 Ubuntu 发布验收

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
