#!/usr/bin/env python3
"""为 Windows 打包程序提供托盘、日志控制台、自启和桌面快捷方式。"""

import ctypes
import os
import subprocess
import sys
import threading
import winreg
from pathlib import Path

import pystray
from PIL import Image


APPLICATION_NAME = "MSU2 系统监控"
AUTOSTART_VALUE_NAME = "MSU2Monitor"
MUTEX_NAME = "Local\\MSU2MonitorTray"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
ERROR_ALREADY_EXISTS = 183


class WindowsTrayApplication:
    """管理 Windows 托盘进程及后台监控子进程。"""

    def __init__(self, worker_arguments):
        """初始化托盘状态、日志缓存和后台进程参数。"""
        self.worker_arguments = list(worker_arguments)
        self.worker_process = None
        self.console_process = None
        self.tray_icon = None
        self.stopping = threading.Event()
        self.mutex_handle = None
        data_directory = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MSU2Monitor"
        data_directory.mkdir(parents=True, exist_ok=True)
        self.log_path = data_directory / "msu2-monitor.log"

    @staticmethod
    def _get_resource_path(relative_path):
        """返回源码运行或 PyInstaller 打包后的资源绝对路径。"""
        resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        return resource_root / relative_path

    @staticmethod
    def _get_executable_path():
        """返回当前打包程序或源码入口的绝对路径。"""
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve()
        return Path(__file__).resolve().with_name("msu2_launcher.py")

    def _acquire_single_instance(self):
        """获取托盘程序互斥锁，避免重复启动多个实例。"""
        kernel32 = ctypes.windll.kernel32
        self.mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return bool(self.mutex_handle) and kernel32.GetLastError() != ERROR_ALREADY_EXISTS

    def _build_worker_command(self):
        """构造后台监控子进程命令行。"""
        if getattr(sys, "frozen", False):
            return [sys.executable, "--worker", *self.worker_arguments]
        launcher_path = Path(__file__).resolve().with_name("msu2_launcher.py")
        return [sys.executable, str(launcher_path), "--worker", *self.worker_arguments]

    def _rotate_log_file(self):
        """当日志超过两兆字节时保留最近一半内容。"""
        if not self.log_path.exists() or self.log_path.stat().st_size <= 2 * 1024 * 1024:
            return
        content = self.log_path.read_bytes()
        newline_index = content.find(b"\n", len(content) // 2)
        self.log_path.write_bytes(content[newline_index + 1:] if newline_index >= 0 else b"")

    def _start_worker(self):
        """以无窗口方式启动后台监控进程并接管标准输出。"""
        self._rotate_log_file()
        worker_environment = os.environ.copy()
        worker_environment["PYTHONIOENCODING"] = "utf-8"
        worker_environment["PYTHONUNBUFFERED"] = "1"
        self.worker_process = subprocess.Popen(
            self._build_worker_command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
            env=worker_environment,
        )
        threading.Thread(target=self._collect_worker_output, name="日志收集", daemon=True).start()

    def _collect_worker_output(self):
        """持续将后台监控进程输出保存到 UTF-8 日志文件。"""
        if self.worker_process is None or self.worker_process.stdout is None:
            return
        with self.log_path.open("a", encoding="utf-8", newline="") as log_file:
            for line in self.worker_process.stdout:
                log_file.write(line)
                log_file.flush()
        return_code = self.worker_process.wait()
        if not self.stopping.is_set():
            message = f"后台监控进程已退出，返回码：{return_code}\n"
            if self.tray_icon is not None:
                self.tray_icon.notify(message.strip(), APPLICATION_NAME)

    def _is_console_open(self):
        """检查独立日志控制台进程是否仍在运行。"""
        return self.console_process is not None and self.console_process.poll() is None

    def _show_console(self):
        """启动独立 PowerShell 控制台并持续显示日志文件末尾内容。"""
        if self._is_console_open():
            return
        environment = os.environ.copy()
        environment["MSU2_LOG_PATH"] = str(self.log_path)
        script = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "$Host.UI.RawUI.WindowTitle='MSU2 系统监控 - 运行日志';"
            "Write-Host ('日志文件：' + $env:MSU2_LOG_PATH);"
            "Write-Host '';"
            "Get-Content -LiteralPath $env:MSU2_LOG_PATH -Encoding UTF8 -Tail 200 -Wait"
        )
        self.console_process = subprocess.Popen(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", script],
            env=environment,
            creationflags=CREATE_NEW_CONSOLE,
        )

    def _hide_console(self):
        """关闭独立日志控制台但保持后台监控继续运行。"""
        process = self.console_process
        self.console_process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()

    def _toggle_console(self, icon=None, item=None):
        """根据当前状态打开或关闭日志控制台。"""
        if self._is_console_open():
            self._hide_console()
        else:
            self._show_console()
        if icon is not None:
            icon.update_menu()

    def _get_console_menu_text(self, item):
        """返回符合当前控制台状态的托盘菜单文字。"""
        return "关闭日志控制台" if self._is_console_open() else "打开日志控制台"

    @staticmethod
    def _get_autostart_command():
        """返回注册到用户启动项中的程序命令。"""
        executable_path = WindowsTrayApplication._get_executable_path()
        if getattr(sys, "frozen", False):
            return f'"{executable_path}"'
        return f'"{sys.executable}" "{executable_path}"'

    @staticmethod
    def _is_autostart_enabled(item=None):
        """检查当前用户启动项是否指向本程序。"""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                value, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
            return value == WindowsTrayApplication._get_autostart_command()
        except OSError:
            return False

    def _toggle_autostart(self, icon, item):
        """开启或关闭当前 Windows 用户的系统自启。"""
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            if self._is_autostart_enabled():
                try:
                    winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, self._get_autostart_command())
        icon.update_menu()

    @staticmethod
    def _get_desktop_directory():
        """读取 Windows 当前用户实际桌面目录。"""
        desktop_buffer = ctypes.create_unicode_buffer(260)
        result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, desktop_buffer)
        return Path(desktop_buffer.value) if result == 0 else Path.home() / "Desktop"

    def _create_desktop_shortcut(self):
        """首次运行打包程序时在当前用户桌面创建快捷方式。"""
        if not getattr(sys, "frozen", False):
            return
        shortcut_path = self._get_desktop_directory() / f"{APPLICATION_NAME}.lnk"
        if shortcut_path.exists():
            return
        environment = os.environ.copy()
        environment.update({
            "MSU2_SHORTCUT_PATH": str(shortcut_path),
            "MSU2_TARGET_PATH": str(Path(sys.executable).resolve()),
            "MSU2_WORKING_DIR": str(Path(sys.executable).resolve().parent),
        })
        script = (
            "$shell=New-Object -ComObject WScript.Shell;"
            "$shortcut=$shell.CreateShortcut($env:MSU2_SHORTCUT_PATH);"
            "$shortcut.TargetPath=$env:MSU2_TARGET_PATH;"
            "$shortcut.WorkingDirectory=$env:MSU2_WORKING_DIR;"
            "$shortcut.IconLocation=$env:MSU2_TARGET_PATH + ',0';"
            "$shortcut.Description='MSU2 系统资源监控';"
            "$shortcut.Save()"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=environment,
            creationflags=CREATE_NO_WINDOW,
            timeout=10,
            check=False,
        )

    def _exit_application(self, icon, item):
        """停止后台监控进程并退出托盘程序。"""
        self.stopping.set()
        if self.worker_process is not None and self.worker_process.poll() is None:
            self.worker_process.terminate()
            try:
                self.worker_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.worker_process.kill()
        if self._is_console_open():
            self._hide_console()
        icon.stop()

    def _create_menu(self):
        """创建日志、自启及退出操作组成的托盘右键菜单。"""
        return pystray.Menu(
            pystray.MenuItem(self._get_console_menu_text, self._toggle_console, default=True),
            pystray.MenuItem("系统自启", self._toggle_autostart, checked=self._is_autostart_enabled),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._exit_application),
        )

    def run(self):
        """创建桌面快捷方式、启动监控并进入托盘消息循环。"""
        if not self._acquire_single_instance():
            return
        self._create_desktop_shortcut()
        self._start_worker()
        tray_image = Image.open(self._get_resource_path("assets/msu2-monitor.ico"))
        self.tray_icon = pystray.Icon("msu2-monitor", tray_image, APPLICATION_NAME, self._create_menu())
        self.tray_icon.run()
