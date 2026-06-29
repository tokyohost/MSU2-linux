# 系统资源监控工具

一个基于Python的linux系统资源监控工具，可将CPU、内存、磁盘、网络状态、CPU温度实时显示在外部LCD屏幕上。
* 买的是  https://e.tb.cn/h.hCXIqGOdWP64HT6?tk=jwPZ4nAaGaK CZ028 「USB小屏幕 电脑性能监控 桌面时钟」
* MSU2_LINUX.py 是 CPU使用率、内存使用率、磁盘使用率、网络流量总量
* MSU2_LINUX-1.py 是 CPU使用率、内存使用率、磁盘使用率、CPU温度
## 功能特点

- 实时监控系统资源使用情况
- 支持多种系统信息显示（CPU、内存、磁盘、网络）
- 专为小尺寸LCD屏幕优化显示布局
- 支持屏幕上下翻转配置
- 自动搜索和连接兼容设备
- 可配置的刷新频率

## 显示信息

- **CPU使用率**: 当前CPU占用百分比
- **内存使用**: 内存占用百分比及详细使用量 (使用量/总量)
- **内存使用**: 内存占用百分比及详细使用量 (使用量)
- **磁盘使用**: 磁盘占用百分比及详细使用量 (使用量/总量)  
- **磁盘使用**: 磁盘占用百分比及详细使用量 (使用量)  
- **网络流量**: 累计网络传输数据量
- **CPU温度**: CPU平均温度

## 硬件要求

- 支持串口通信的LCD显示设备（基于WCH芯片）
- 设备需支持自定义通信协议

## 软件依赖

请查看 `requirements.txt` 文件获取完整的依赖列表。

## 使用 APT 安装

项目已经提供 Debian 打包配置，适用于 Debian、Ubuntu 及其衍生发行版。

1. 安装构建工具：

   ```bash
   sudo apt update
   sudo apt install build-essential debhelper devscripts
   ```

2. 在项目根目录构建软件包：

   ```bash
   chmod 0755 debian/rules bin/msu2-linux
   dpkg-buildpackage --no-sign -b
   ```

3. 使用 APT 安装生成的软件包及其依赖：

   ```bash
   sudo apt install ../msu2-linux_1.0.0_all.deb
   ```

安装完成后，`msu2-linux.service` 会自动启用并立即启动，后续开机时自动运行。
主程序也可以通过 `msu2-linux` 命令手动启动。

## 服务管理

```bash
sudo systemctl status msu2-linux
sudo systemctl restart msu2-linux
sudo journalctl -u msu2-linux -f
```

如需调整运行参数，可编辑 `/etc/msu2-linux.conf`：

```bash
# 显示模板对应的脚本名称
MSU2_TEMPLATE="MSU2_LINUX.py"

# 用于检测网络延迟的域名或 IP 地址
MSU2_PING_TARGET="1.1.1.1"

# 屏幕刷新间隔，单位为秒
MSU2_REFRESH_INTERVAL="1.0"

# 是否启用屏幕上下翻转
MSU2_FLIP_VERTICAL="false"
```

修改配置后重启服务：

```bash
sudo systemctl restart msu2-linux
```

服务默认以 `root` 用户运行，以便读取硬件温度并访问 USB 串口设备。

## 构建 Windows EXE

在 Windows 项目根目录运行一键打包脚本：

```bat
build-exe.bat
```

脚本会安装运行依赖和 PyInstaller，并生成单文件程序：

```text
dist\msu2-linux.exe
```

EXE 内已包含全部四套显示模板，使用方式与 Python 统一入口一致：

```bat
dist\msu2-linux.exe --list-templates
dist\msu2-linux.exe --template MSU2_LINUX.py --refresh-interval 1.5 --flip-vertical true
```

## 自动构建与发布

项目提供两个 GitHub Actions 工作流：

- Linux 工作流构建 `amd64`、`arm64`、`armhf`、`i386` 四种 DEB 软件包。
- Windows 工作流使用对应架构的 Python 构建 32 位及 64 位单文件 EXE。

两个工作流都可以在 GitHub Actions 页面手动运行。推送 `v` 开头的版本标签时，
会自动创建或更新对应的 GitHub Release，并上传全部 DEB 和 EXE：

```bash
git tag v1.0.0
git push origin v1.0.0
```

## 命令行参数

手动运行时可使用与服务配置对应的命令行参数：

```bash
msu2-linux --template MSU2_LINUX.py --ping-target www.baidu.com --refresh-interval 1.5 --flip-vertical true
```

- `--template`：选择显示模板对应的脚本，可使用 `--list-templates` 查看全部模板。
- `--ping-target`：用于检测延迟的域名或 IP 地址。
- `--refresh-interval`：屏幕刷新间隔，单位为秒，必须大于 0。
- `--flip-vertical`：是否启用屏幕上下翻转，支持 `true/false`、`yes/no`、`on/off` 或 `1/0`。

## 使用方法

1. 确保LCD设备已正确连接到电脑
2. 运行程序后，工具会自动搜索并连接设备
3. 连接成功后，系统资源信息将实时显示在LCD屏幕上
4. 按 `Ctrl+C` 退出程序

## 注意事项

- 程序需要管理员权限访问系统资源信息
- 首次运行时确保已安装所有依赖包
- 如遇到字体显示问题，程序会自动使用默认字体
- 请确保串口设备未被其他程序占用

## 许可证

MIT License
