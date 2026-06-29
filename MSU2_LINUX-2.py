#!/usr/bin/env python3
"""在 160×80 USB 小屏幕上显示 Linux 系统运行状态。"""

import argparse
import datetime as dt
import glob
import os
import platform
import socket
import subprocess
import threading
import time
import traceback
from collections import deque

import numpy as np
import psutil
import serial
import serial.tools.list_ports
from PIL import Image, ImageDraw, ImageFont


# 屏幕及串口参数。
SHOW_WIDTH = 160
SHOW_HEIGHT = 80
SERIAL_BAUDRATE = 115200
REFRESH_INTERVAL = 1.0
LCD_FLIP_VERTICAL = False
PING_TARGET = "1.1.1.1"

# 仪表盘颜色（RGB888）。
BLACK = (0, 0, 0)
WHITE = (225, 225, 230)
GRAY = (88, 92, 99)
BLUE = (30, 151, 245)
GREEN = (65, 220, 80)
YELLOW = (255, 193, 0)
PURPLE = (157, 92, 235)

# LCD 协议颜色（RGB565）。
LCD_WHITE = 0xFFFF
LCD_BLACK = 0x0000

ser = None
device_state = 0
serial_lock = threading.Lock()


class SystemMonitor:
    """采集系统状态，并绘制与参考图一致的紧凑型仪表盘。"""

    def __init__(self):
        """初始化字体、历史数据和网络采样基准。"""
        self.start_monotonic = time.monotonic()
        self.cpu_history = deque([0] * 22, maxlen=22)
        self.ping_history = deque([0] * 18, maxlen=18)
        self.upload_history = deque([0] * 26, maxlen=26)
        self.download_history = deque([0] * 26, maxlen=26)
        counters = psutil.net_io_counters()
        self.last_net_bytes = (counters.bytes_sent, counters.bytes_recv)
        self.last_net_time = time.monotonic()
        self.font_tiny = self._load_font(6)
        self.font_small = self._load_font(7)
        self.font_normal = self._load_font(8)
        self.font_large = self._load_font(10)

    @staticmethod
    def _load_font(size):
        """按常见 Linux 和 Windows 路径加载支持中文的字体。"""
        candidates = (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "DejaVuSans.ttf",
        )
        for font_path in candidates:
            try:
                return ImageFont.truetype(font_path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _format_capacity(byte_count):
        """将字节数格式化为适合小屏幕显示的容量文本。"""
        value = float(byte_count)
        for unit in ("B", "K", "M", "G", "T"):
            if value < 1024 or unit == "T":
                return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
            value /= 1024
        return "0B"

    @staticmethod
    def _format_capacity_pair(used_bytes, total_bytes):
        """使用同一容量单位紧凑显示已用量和总量。"""
        unit_index = 0
        scaled_total = float(total_bytes)
        units = ("B", "K", "M", "G", "T")
        while scaled_total >= 1024 and unit_index < len(units) - 1:
            scaled_total /= 1024
            unit_index += 1
        divisor = 1024 ** unit_index
        scaled_used = used_bytes / divisor
        if units[unit_index] == "T":
            return f"{scaled_used:.2f}/{scaled_total:.2f}T"
        return f"{scaled_used:.0f}/{scaled_total:.0f}{units[unit_index]}"

    @staticmethod
    def _get_cpu_temperature():
        """通过 psutil 与 Linux sysfs 多级回退读取 CPU 温度。"""
        candidates = []
        try:
            temperatures = psutil.sensors_temperatures()
        except (AttributeError, OSError):
            temperatures = {}

        preferred = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "soc_thermal")
        for sensor_name in preferred:
            for entry in temperatures.get(sensor_name, []):
                value = SystemMonitor._normalize_temperature(entry.current)
                if value is not None:
                    priority = 3 if "package" in (entry.label or "").lower() else 2
                    candidates.append((priority, value))

        if platform.system() == "Linux":
            candidates.extend(SystemMonitor._read_hwmon_temperatures())
            candidates.extend(SystemMonitor._read_thermal_zone_temperatures())

        if not candidates:
            for entries in temperatures.values():
                for entry in entries:
                    value = SystemMonitor._normalize_temperature(entry.current)
                    if value is not None:
                        candidates.append((0, value))
        if not candidates:
            return None

        highest_priority = max(priority for priority, _ in candidates)
        preferred_values = [value for priority, value in candidates if priority == highest_priority]
        return round(sum(preferred_values) / len(preferred_values))

    @staticmethod
    def _normalize_temperature(raw_value):
        """将摄氏度或毫摄氏度数值归一化，并过滤异常读数。"""
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return None
        if abs(value) > 1000:
            value /= 1000
        return value if -20 <= value <= 150 else None

    @staticmethod
    def _read_text_file(file_path):
        """读取 sysfs 文本文件，读取失败时返回空文本。"""
        try:
            with open(file_path, "r", encoding="utf-8") as data_file:
                return data_file.read().strip()
        except (OSError, UnicodeError):
            return ""

    @staticmethod
    def _read_hwmon_temperatures():
        """从 Linux hwmon 接口读取 Intel、AMD 与 SoC 的 CPU 温度。"""
        results = []
        cpu_sensor_names = ("coretemp", "k10temp", "zenpower", "cpu", "soc", "acpitz")
        for hwmon_path in glob.glob("/sys/class/hwmon/hwmon*"):
            sensor_name = SystemMonitor._read_text_file(os.path.join(hwmon_path, "name")).lower()
            if not any(keyword in sensor_name for keyword in cpu_sensor_names):
                continue
            for input_path in glob.glob(os.path.join(hwmon_path, "temp*_input")):
                value = SystemMonitor._normalize_temperature(
                    SystemMonitor._read_text_file(input_path)
                )
                if value is None:
                    continue
                label_path = input_path.replace("_input", "_label")
                label = SystemMonitor._read_text_file(label_path).lower()
                priority = 3 if "package" in label or "tctl" in label else 2
                results.append((priority, value))
        return results

    @staticmethod
    def _read_thermal_zone_temperatures():
        """从 Linux thermal_zone 接口读取 CPU 或 SoC 温度。"""
        results = []
        cpu_keywords = ("cpu", "x86_pkg", "package", "soc", "acpi", "thermal")
        for zone_path in glob.glob("/sys/class/thermal/thermal_zone*"):
            zone_type = SystemMonitor._read_text_file(os.path.join(zone_path, "type")).lower()
            value = SystemMonitor._normalize_temperature(
                SystemMonitor._read_text_file(os.path.join(zone_path, "temp"))
            )
            if value is None:
                continue
            priority = 2 if any(keyword in zone_type for keyword in cpu_keywords) else 0
            results.append((priority, value))
        return results

    @staticmethod
    def _get_all_disks_usage():
        """汇总所有已挂载物理磁盘分区的已用容量和总容量。"""
        total_bytes = 0
        used_bytes = 0
        visited_devices = set()
        for partition in psutil.disk_partitions(all=False):
            device_key = os.path.realpath(partition.device) if partition.device else partition.mountpoint
            if device_key in visited_devices:
                continue
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except (OSError, PermissionError):
                continue
            visited_devices.add(device_key)
            total_bytes += usage.total
            used_bytes += usage.used

        if total_bytes == 0:
            fallback = psutil.disk_usage(os.path.abspath(os.sep))
            return fallback.used, fallback.total
        return used_bytes, total_bytes

    @staticmethod
    def _get_local_ip():
        """获取默认路由对应的本机局域网地址。"""
        connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            connection.connect(("8.8.8.8", 80))
            return connection.getsockname()[0]
        except OSError:
            return "未连接"
        finally:
            connection.close()

    @staticmethod
    def _get_ping_delay():
        """对配置的域名或 IP 地址执行一次 Ping，并返回毫秒延迟。"""
        command = (["ping", "-n", "1", "-w", "1000", PING_TARGET]
                   if platform.system() == "Windows"
                   else ["ping", "-c", "1", "-W", "1", PING_TARGET])
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
                check=False,
            )
            return round((time.monotonic() - started) * 1000) if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def collect_system_data(self):
        """采集 CPU、内存、磁盘、网络和运行时间数据。"""
        now = time.monotonic()
        cpu_percent = round(psutil.cpu_percent(interval=None))
        memory = psutil.virtual_memory()
        disk_used, disk_total = self._get_all_disks_usage()
        counters = psutil.net_io_counters()
        elapsed = max(now - self.last_net_time, 0.001)
        upload = max(0, counters.bytes_sent - self.last_net_bytes[0]) * 8 / elapsed
        download = max(0, counters.bytes_recv - self.last_net_bytes[1]) * 8 / elapsed
        self.last_net_bytes = (counters.bytes_sent, counters.bytes_recv)
        self.last_net_time = now

        ping = self._get_ping_delay()
        self.cpu_history.append(cpu_percent)
        self.ping_history.append(ping or 0)
        self.upload_history.append(upload)
        self.download_history.append(download)
        local_ip = self._get_local_ip()
        return {
            "cpu": cpu_percent,
            "temperature": self._get_cpu_temperature(),
            "memory_percent": round(memory.percent),
            "memory_capacity": self._format_capacity_pair(memory.used, memory.total),
            "disk_percent": round(disk_used * 100 / disk_total) if disk_total else 0,
            "disk_capacity": self._format_capacity_pair(disk_used, disk_total),
            "upload": upload,
            "download": download,
            "ping": ping,
            "ip": local_ip,
            "online": local_ip != "未连接",
            "uptime": max(0, int(time.time() - psutil.boot_time())),
        }

    @staticmethod
    def _format_speed(bits_per_second):
        """将网络速率格式化为 Kbps 或 Mbps。"""
        if bits_per_second >= 1_000_000:
            return f"{bits_per_second / 1_000_000:.2f}M"
        return f"{bits_per_second / 1_000:.0f}K"

    @staticmethod
    def _format_uptime(seconds):
        """将运行秒数格式化为天、时、分、秒。"""
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f"{days}天 {hours:02}:{minutes:02}:{seconds:02}"

    @staticmethod
    def _draw_progress(draw, box, percent, color):
        """绘制带边框的百分比进度条。"""
        left, top, right, bottom = box
        draw.rectangle(box, outline=color, width=1)
        fill_right = left + round((right - left - 2) * max(0, min(percent, 100)) / 100)
        if fill_right > left:
            draw.rectangle((left + 1, top + 1, fill_right, bottom - 1), fill=color)

    @staticmethod
    def _draw_sparkline(draw, box, values, color, fixed_max=None):
        """在指定区域绘制历史折线。"""
        left, top, right, bottom = box
        values = list(values)
        if len(values) < 2:
            return
        maximum = max(fixed_max or 0, max(values), 1)
        points = []
        for index, value in enumerate(values):
            x = left + index * (right - left) / (len(values) - 1)
            y = bottom - min(value, maximum) * (bottom - top) / maximum
            points.append((round(x), round(y)))
        draw.line(points, fill=color, width=1)

    @staticmethod
    def _draw_bars(draw, box, values, color):
        """在指定区域绘制网络速率柱状历史图。"""
        left, top, right, bottom = box
        values = list(values)
        maximum = max(values, default=1) or 1
        width = max(1, (right - left + 1) // max(len(values), 1))
        for index, value in enumerate(values):
            x = left + index * width
            height = max(1, round(value / maximum * (bottom - top))) if value else 1
            draw.rectangle((x, bottom - height, min(x + width - 1, right), bottom), fill=color)

    def _draw_header(self, draw, data):
        """绘制顶部 CPU、内存、磁盘与网络四个信息块。"""
        separators = (40, 81, 120)
        for x in separators:
            draw.line((x, 3, x, 29), fill=GRAY)

        draw.text((3, 2), "CPU", font=self.font_normal, fill=BLUE)
        draw.text((38, 2), f"{data['cpu']}%", font=self.font_normal, fill=BLUE, anchor="ra")
        self._draw_sparkline(draw, (3, 12, 37, 20), self.cpu_history, BLUE, 100)
        temperature = "--" if data["temperature"] is None else str(data["temperature"])
        draw.text((3, 21), f"温度 {temperature}°C", font=self.font_small, fill=WHITE)

        draw.text((43, 2), "内存", font=self.font_normal, fill=GREEN)
        draw.text((78, 2), f"{data['memory_percent']}%", font=self.font_normal, fill=GREEN, anchor="ra")
        self._draw_progress(draw, (43, 13, 78, 17), data["memory_percent"], GREEN)
        draw.text((43, 21), data["memory_capacity"], font=self.font_small, fill=WHITE)

        draw.text((84, 2), "磁盘", font=self.font_normal, fill=YELLOW)
        draw.text((117, 2), f"{data['disk_percent']}%", font=self.font_normal, fill=YELLOW, anchor="ra")
        self._draw_progress(draw, (84, 13, 117, 17), data["disk_percent"], YELLOW)
        draw.text((84, 21), data["disk_capacity"], font=self.font_tiny, fill=WHITE)

        draw.text((123, 2), "网络", font=self.font_normal, fill=PURPLE)
        draw.text((157, 11), "在线" if data["online"] else "离线", font=self.font_normal,
                  fill=PURPLE if data["online"] else GRAY, anchor="ra")
        ip_parts = data["ip"].split(".")
        ip_text = f"*.{'.'.join(ip_parts[-2:])}" if len(ip_parts) == 4 else data["ip"]
        draw.text((123, 22), ip_text, font=self.font_small, fill=WHITE)

    def _draw_network(self, draw, data):
        """绘制中部上下行速率和 Ping 延迟图。"""
        draw.line((2, 32, 157, 32), fill=GRAY)
        draw.line((75, 35, 75, 67), fill=GRAY)

        draw.text((3, 35), "↑上传", font=self.font_normal, fill=BLUE)
        draw.text((72, 35), self._format_speed(data["upload"]), font=self.font_normal,
                  fill=BLUE, anchor="ra")
        self._draw_bars(draw, (3, 44, 72, 49), self.upload_history, BLUE)
        draw.text((3, 52), "↓下载", font=self.font_normal, fill=GREEN)
        draw.text((72, 52), self._format_speed(data["download"]), font=self.font_normal,
                  fill=GREEN, anchor="ra")
        self._draw_bars(draw, (3, 61, 72, 66), self.download_history, GREEN)

        delay_text = "-- ms" if data["ping"] is None else f"{data['ping']} ms"
        draw.text((79, 35), "PING 延迟", font=self.font_normal, fill=PURPLE)
        draw.text((157, 35), delay_text, font=self.font_normal, fill=PURPLE, anchor="ra")
        draw.line((83, 49, 157, 49), fill=(50, 50, 56))
        draw.line((83, 58, 157, 58), fill=(50, 50, 56))
        self._draw_sparkline(draw, (83, 45, 157, 66), self.ping_history, PURPLE, 100)

    def _draw_footer(self, draw, data):
        """绘制底部日期时间及系统运行时长。"""
        now = dt.datetime.now()
        draw.line((2, 69, 157, 69), fill=GRAY)
        draw.text((4, 72), now.strftime("%m-%d %H:%M:%S"), font=self.font_small, fill=WHITE)
        draw.text((157, 72), self._format_uptime(data['uptime']),
                  font=self.font_small, fill=WHITE, anchor="ra")

    def create_display_image(self):
        """采集当前数据并生成一帧 160×80 RGB 图像。"""
        data = self.collect_system_data()
        image = Image.new("RGB", (SHOW_WIDTH, SHOW_HEIGHT), BLACK)
        draw = ImageDraw.Draw(image)
        self._draw_header(draw, data)
        self._draw_network(draw, data)
        self._draw_footer(draw, data)
        return image


monitor = SystemMonitor()


def digit_to_ints(value):
    """将 32 位整数拆分为四个高位优先字节。"""
    return [(value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF]


def rgb888_to_rgb565(rgb888_array):
    """将 RGB888 图像数组转换为 RGB565 数组。"""
    red = (rgb888_array[:, :, 0] & 0xF8) << 8
    green = (rgb888_array[:, :, 1] & 0xFC) << 3
    blue = (rgb888_array[:, :, 2] & 0xF8) >> 3
    return red | green | blue


def screen_data_process(photo_data):
    """按设备分页协议压缩 RGB565 像素数据。"""
    data_per_page = 128
    encoded = bytearray()
    for offset in range(0, len(photo_data), data_per_page):
        page = photo_data[offset:offset + data_per_page]
        valid_size = len(page)
        if valid_size < data_per_page:
            page = np.append(page, np.full(data_per_page - valid_size, 0xFFFF, dtype=np.uint32))
        pairs = page[::2] << 16 | page[1::2]
        if valid_size == data_per_page:
            values, counts = np.unique(pairs, return_counts=True)
            background = values[counts.argmax()]
            encoded.extend([2, 4])
            encoded.extend(digit_to_ints(int(background)))
            for index, pair in enumerate(pairs):
                if pair != background:
                    encoded.extend([4, index])
                    encoded.extend(digit_to_ints(int(pair)))
            encoded.extend([2, 3, 8, 1, 0, 0])
        else:
            for index, pair in enumerate(pairs):
                encoded.extend([4, index])
                encoded.extend(digit_to_ints(int(pair)))
            encoded.extend([2, 3, 8, 0, valid_size * 2, 0])
    return encoded


def lcd_set_xy(x, y):
    """生成设置 LCD 起始坐标的协议数据。"""
    return bytearray([2, 0, x // 256, x % 256, y // 256, y % 256])


def lcd_set_size(width, height):
    """生成设置 LCD 显示区域尺寸的协议数据。"""
    return bytearray([2, 1, width // 256, width % 256, height // 256, height % 256])


def serial_read():
    """读取串口响应，超时则返回空字节串。"""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        received = ser.read(ser.in_waiting)
        if received:
            return received
        time.sleep(0.001)
    return b""


def serial_read_write(data, read=True, minimum_size=0):
    """以线程安全方式执行串口写入及可选响应读取。"""
    global ser
    result = bytearray()
    with serial_lock:
        try:
            if ser is None or not ser.is_open:
                return result
            ser.reset_input_buffer()
            ser.write(data)
            ser.flush()
            if not read:
                return result
            while len(result) < minimum_size:
                received = serial_read()
                if not received:
                    break
                result.extend(received)
        except (OSError, serial.SerialException) as error:
            print(f"串口通信异常：{error}")
            set_device_state(0)
    return result


def set_device_state(state):
    """更新设备连接状态，并在断开时安全关闭串口。"""
    global device_state, ser
    device_state = state
    if state == 0 and ser is not None and ser.is_open:
        ser.close()


def lcd_add(x, y, width, height):
    """设置 LCD 写入区域并使设备进入像素写入状态。"""
    command = lcd_set_xy(x, y)
    command.extend(lcd_set_size(width, height))
    command.extend([2, 3, 7, 0, 0, 0])
    response = serial_read_write(command, minimum_size=2)
    return len(response) > 1 and response[0] == 2 and response[1] == 3


def lcd_set_color(foreground, background):
    """设置 LCD 协议使用的前景色和背景色。"""
    command = bytearray([2, 2, foreground // 256, foreground % 256,
                         background // 256, background % 256])
    serial_read_write(command, read=False)


def lcd_set_direction(flipped):
    """设置 LCD 正常显示或上下翻转。"""
    command = bytearray([2, 3, 10, 1 if flipped else 0, 0, 0])
    response = serial_read_write(command, minimum_size=6)
    return len(response) > 5 and response[:2] == command[:2]


def read_adc_channel(channel):
    """读取指定 ADC 通道，失败时返回零。"""
    command = bytearray([8, channel, 0, 0, 0, 0])
    response = serial_read_write(command, minimum_size=6)
    return response[4] * 256 + response[5] if len(response) > 5 and response[:2] == command[:2] else 0


def connect_device(port_list):
    """搜索端口列表并连接兼容的 MSN 小屏设备。"""
    global ser
    for port in port_list:
        try:
            ser = serial.Serial(port.device, SERIAL_BAUDRATE, timeout=0.1, write_timeout=5.0)
            greeting = serial_read()
            valid = any(greeting[index:index + 4] == b"\x00MSN" for index in range(max(0, len(greeting) - 5)))
            if not valid:
                ser.close()
                continue
            handshake = b"\x00MSNCN"
            response = serial_read_write(handshake, minimum_size=6)
            if response[-6:] != handshake:
                ser.close()
                continue
            lcd_set_direction(LCD_FLIP_VERTICAL)
            read_adc_channel(9)
            set_device_state(1)
            print(f"设备连接成功：{port.device}")
            return True
        except (OSError, serial.SerialException) as error:
            print(f"无法打开 {port.device}：{error}")
            if ser is not None and ser.is_open:
                ser.close()
    return False


def show_pc_state():
    """生成系统状态图像，并转换后发送到 LCD。"""
    image = monitor.create_display_image()
    rgb565 = rgb888_to_rgb565(np.asarray(image, dtype=np.uint32))
    serial_read_write(screen_data_process(rgb565.flatten()), read=False)


def daemon_task():
    """持续刷新屏幕，并在连接断开后自动重连。"""
    global device_state
    while True:
        try:
            if device_state == 1:
                started = time.monotonic()
                if not lcd_add(0, 0, SHOW_WIDTH, SHOW_HEIGHT):
                    set_device_state(0)
                    continue
                lcd_set_color(LCD_WHITE, LCD_BLACK)
                show_pc_state()
                time.sleep(max(0, REFRESH_INTERVAL - (time.monotonic() - started)))
                continue
            ports = [port for port in serial.tools.list_ports.comports()
                     if getattr(port, "vid", None) == 0x1A86]
            if not ports or not connect_device(ports):
                print("未找到可用设备，2 秒后重试……")
                time.sleep(2)
        except Exception:
            print(f"主任务异常：\n{traceback.format_exc()}")
            set_device_state(0)
            time.sleep(1)


def save_preview(output_path):
    """生成本地预览图，便于在没有 LCD 时检查布局。"""
    image = monitor.create_display_image()
    image.resize((SHOW_WIDTH * 6, SHOW_HEIGHT * 6), Image.Resampling.NEAREST).save(output_path)
    print(f"预览图已保存：{os.path.abspath(output_path)}")


def parse_boolean(value):
    """将常见的命令行布尔值转换为真或假。"""
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("必须使用 true/false、yes/no、on/off 或 1/0")


def main():
    """解析命令行参数并启动预览或硬件显示模式。"""
    global LCD_FLIP_VERTICAL, PING_TARGET, REFRESH_INTERVAL

    parser = argparse.ArgumentParser(description="MSU2 Linux 系统监控仪表盘")
    parser.add_argument("--preview", metavar="PNG", help="仅生成放大预览图，不连接设备")
    parser.add_argument("--ping-target", default=PING_TARGET, metavar="域名或IP",
                        help="用于检测网络延迟的域名或 IP 地址")
    parser.add_argument("--refresh-interval", default=REFRESH_INTERVAL, type=float, metavar="秒",
                        help="屏幕刷新间隔，必须大于 0 秒")
    parser.add_argument("--flip-vertical", default=LCD_FLIP_VERTICAL, type=parse_boolean,
                        metavar="布尔值", help="是否启用屏幕上下翻转")
    arguments = parser.parse_args()
    if arguments.refresh_interval <= 0:
        parser.error("--refresh-interval 必须大于 0")
    PING_TARGET = arguments.ping_target
    REFRESH_INTERVAL = arguments.refresh_interval
    LCD_FLIP_VERTICAL = arguments.flip_vertical
    if arguments.preview:
        save_preview(arguments.preview)
        return
    print("MSU2 系统监控仪表盘已启动，按 Ctrl+C 退出。")
    daemon_task()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n程序已退出。")
    finally:
        if ser is not None and ser.is_open:
            ser.close()
