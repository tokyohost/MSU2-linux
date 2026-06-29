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
   chmod 0755 debian/rules debian/msu2-linux
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

如需设置附加启动参数，可编辑 `/etc/msu2-linux.conf`，然后执行：

```bash
sudo systemctl restart msu2-linux
```

服务默认以 `root` 用户运行，以便读取硬件温度并访问 USB 串口设备。

## 配置说明

在 `if __name__ == "__main__":` 代码块中可以配置以下参数：

- `refresh_interval`: 屏幕刷新间隔（秒）
- `lcd_flip_vertical`: 是否启用屏幕上下翻转（True/False）

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
