#!/usr/bin/env python3
"""显示多磁盘温度的 MSU2 Linux 系统监控仪表盘。"""

import glob
import importlib.util
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path

import psutil
from PIL import ImageFont


def _load_overview_module():
    """从同目录加载现代综合仪表盘作为硬件通信与基础绘图模块。"""
    module_path = Path(__file__).with_name("msu2_dashboard_overview.py")
    specification = importlib.util.spec_from_file_location("msu2_dashboard_overview", module_path)
    if specification is None or specification.loader is None:
        raise ImportError(f"无法加载基础模块：{module_path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


base = _load_overview_module()
logger = logging.getLogger("MSU2-磁盘温度")

# WMI、SMART 与 sysfs 温度等慢速参数的缓存时间（秒）。
SLOW_DATA_CACHE_SECONDS = 10.0
PING_CACHE_SECONDS = 5.0
ERROR_RED = (255, 55, 55)


class DiskTemperatureMonitor(base.SystemMonitor):
    """使用独立慢速线程采集 CPU、Ping 和多磁盘温度。"""

    def __init__(self):
        """初始化基础监控状态及磁盘温度缓存。"""
        super().__init__()
        self.font_tiny = self._load_font(6)
        self.disk_temperature_cache = []
        self.disk_temperature_time = 0.0
        self.cpu_temperature_cache = None
        self.cpu_temperature_time = 0.0
        self.ping_cache = None
        self.ping_time = 0.0
        self.slow_data_thread = None
        self.cached_data["disk_temperatures"] = ()

    def start_data_collection(self):
        """分别启动基础数据线程和慢速硬件数据线程。"""
        super().start_data_collection()
        if self.slow_data_thread is not None and self.slow_data_thread.is_alive():
            return
        self.slow_data_thread = threading.Thread(
            target=self._slow_data_collection_loop,
            name="慢速硬件数据采集",
            daemon=True,
        )
        self.slow_data_thread.start()

    def _slow_data_collection_loop(self):
        """独立采集可能阻塞的 Ping、WMI、SMART 与磁盘温度。"""
        while True:
            started = time.monotonic()
            try:
                self._collect_ping_delay()
                self._collect_cpu_temperature()
                self._get_disk_temperatures()
            except Exception:
                logger.exception("慢速硬件数据采集异常")
            time.sleep(max(0.1, PING_CACHE_SECONDS - (time.monotonic() - started)))

    @staticmethod
    def _run_powershell_json(script):
        """执行 Windows PowerShell 查询并解析其 JSON 输出。"""
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script,
        ]
        try:
            result = base.subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
                creationflags=getattr(base.subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, base.subprocess.TimeoutExpired):
            return None
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return None

    def _get_cpu_temperature(self):
        """立即返回后台线程缓存的 CPU 温度。"""
        return self.cpu_temperature_cache

    def _collect_cpu_temperature(self):
        """在慢速线程读取 CPU 温度，并在 Windows 上使用 WMI 回退。"""
        now = time.monotonic()
        if self.cpu_temperature_time and now - self.cpu_temperature_time < SLOW_DATA_CACHE_SECONDS:
            return self.cpu_temperature_cache

        temperature = base.SystemMonitor._get_cpu_temperature()
        if temperature is not None or base.platform.system() != "Windows":
            self.cpu_temperature_cache = temperature
            self.cpu_temperature_time = now
            return temperature

        script = r"""
$values = @()
$sources = @(
    @{ Namespace='root/LibreHardwareMonitor'; Class='Sensor' },
    @{ Namespace='root/OpenHardwareMonitor'; Class='Sensor' }
)
foreach ($source in $sources) {
    try {
        $values += @(Get-CimInstance -Namespace $source.Namespace -ClassName $source.Class -ErrorAction Stop |
            Where-Object { $_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU Package|CPU Core|Tctl|Tdie' } |
            Select-Object -ExpandProperty Value)
    } catch {}
}
if ($values.Count -eq 0) {
    try {
        $values += @(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop |
            ForEach-Object { ($_.CurrentTemperature / 10.0) - 273.15 })
    } catch {}
}
@{ Values=@($values) } | ConvertTo-Json -Compress
"""
        response = self._run_powershell_json(script)
        values = response.get("Values", []) if isinstance(response, dict) else []
        if not isinstance(values, list):
            values = [values]
        normalized = [self._normalize_temperature(value) for value in values]
        normalized = [value for value in normalized if value is not None]
        self.cpu_temperature_cache = round(max(normalized)) if normalized else None
        self.cpu_temperature_time = now
        return self.cpu_temperature_cache

    def _get_ping_delay(self):
        """立即返回后台线程缓存的网络延迟。"""
        return self.ping_cache

    def _collect_ping_delay(self):
        """在慢速线程定期检测网络延迟并更新缓存。"""
        now = time.monotonic()
        if self.ping_time and now - self.ping_time < PING_CACHE_SECONDS:
            return self.ping_cache
        self.ping_cache = base.SystemMonitor._get_ping_delay()
        self.ping_time = now
        return self.ping_cache

    @staticmethod
    def _natural_sort_key(text):
        """生成包含数字的设备名称自然排序键。"""
        return [int(part) if part.isdigit() else part.lower()
                for part in re.split(r"(\d+)", text)]

    @staticmethod
    def _partition_to_disk_name(device_path):
        """将磁盘分区设备名转换为所属物理磁盘名。"""
        name = os.path.basename(os.path.realpath(device_path))
        if not name:
            name = str(device_path).rstrip("/\\")
        if re.fullmatch(r"nvme\d+n\d+p\d+", name):
            return re.sub(r"p\d+$", "", name)
        if re.fullmatch(r"mmcblk\d+p\d+", name):
            return re.sub(r"p\d+$", "", name)
        if re.fullmatch(r"(?:dm-|md)\d+", name):
            return name
        return re.sub(r"\d+$", "", name)

    @classmethod
    def _resolve_backing_disks(cls, disk_name):
        """将 RAID、LVM 等逻辑设备解析为底层物理磁盘名称。"""
        slave_paths = glob.glob(f"/sys/class/block/{disk_name}/slaves/*")
        if not slave_paths:
            return {disk_name}
        results = set()
        for slave_path in slave_paths:
            slave_name = cls._partition_to_disk_name(os.path.basename(slave_path))
            if slave_name == disk_name:
                continue
            results.update(cls._resolve_backing_disks(slave_name))
        return results or {disk_name}

    @classmethod
    def _list_physical_disks(cls):
        """通过 sysfs 分区标记列出物理磁盘，并兼容 RAID 与 LVM。"""
        names = set()
        ignored_prefixes = ("loop", "ram", "zram", "fd", "sr")
        for block_path in glob.glob("/sys/class/block/*"):
            disk_name = os.path.basename(block_path)
            if disk_name.startswith(ignored_prefixes):
                continue
            if os.path.exists(os.path.join(block_path, "partition")):
                continue
            size_text = cls._read_text_file(os.path.join(block_path, "size"))
            try:
                if int(size_text or "0") <= 0:
                    continue
            except ValueError:
                continue
            names.update(cls._resolve_backing_disks(disk_name))

        if not names:
            for partition in psutil.disk_partitions(all=True):
                is_linux_device = str(partition.device).startswith("/dev/")
                is_windows_drive = bool(re.match(r"^[A-Za-z]:", str(partition.device)))
                if not is_linux_device and not is_windows_drive:
                    continue
                name = cls._partition_to_disk_name(partition.device)
                if name:
                    names.add(name)
        return sorted(names, key=cls._natural_sort_key)[:12]

    @classmethod
    def _read_block_temperature(cls, disk_name):
        """从指定磁盘关联的 hwmon 或 device 接口读取温度。"""
        block_path = f"/sys/class/block/{disk_name}"
        candidates = []
        direct_paths = (
            os.path.join(block_path, "device", "temperature"),
            os.path.join(block_path, "device", "temp"),
        )
        for input_path in direct_paths:
            value = cls._normalize_temperature(cls._read_text_file(input_path))
            if value is not None:
                candidates.append((3, value))

        hwmon_patterns = (
            os.path.join(block_path, "device", "hwmon", "hwmon*", "temp*_input"),
            os.path.join(block_path, "device", "device", "hwmon", "hwmon*", "temp*_input"),
        )
        for pattern in hwmon_patterns:
            for input_path in glob.glob(pattern):
                value = cls._normalize_temperature(cls._read_text_file(input_path))
                if value is None:
                    continue
                label = cls._read_text_file(input_path.replace("_input", "_label")).lower()
                priority = 3 if "composite" in label else 2 if "drive" in label else 1
                candidates.append((priority, value))
        if not candidates:
            return None
        highest_priority = max(priority for priority, _ in candidates)
        values = [value for priority, value in candidates if priority == highest_priority]
        return round(sum(values) / len(values))

    @staticmethod
    def _read_unassigned_sensor_temperatures():
        """读取未能通过块设备路径关联的 NVMe 与 drivetemp 传感器。"""
        values = []
        try:
            groups = psutil.sensors_temperatures()
        except (AttributeError, OSError, RuntimeError):
            groups = {}
        for group_name in ("nvme", "drivetemp"):
            for entry in groups.get(group_name, []):
                value = DiskTemperatureMonitor._normalize_temperature(entry.current)
                if value is None:
                    continue
                label = (entry.label or "").lower()
                priority = 2 if "composite" in label else 1
                values.append((priority, value))
        if not values:
            return []
        preferred_priority = max(priority for priority, _ in values)
        return [round(value) for priority, value in values if priority == preferred_priority]

    @classmethod
    def _get_windows_disk_temperatures(cls):
        """通过 Windows 存储接口、WMI 与 SMART 读取物理磁盘温度。"""
        script = r"""
$items = @(Get-PhysicalDisk -ErrorAction SilentlyContinue | Sort-Object DeviceId | ForEach-Object {
    $disk = $_
    $counter = $null
    try { $counter = $disk | Get-StorageReliabilityCounter -ErrorAction Stop } catch {}
    [PSCustomObject]@{
        DeviceId = [string]$disk.DeviceId
        Name = [string]$disk.FriendlyName
        Path = "\\.\PhysicalDrive$($disk.DeviceId)"
        Temperature = if ($null -ne $counter) { $counter.Temperature } else { $null }
    }
})
if ($items.Count -eq 0) {
    $items = @(Get-CimInstance -ClassName Win32_DiskDrive -ErrorAction SilentlyContinue |
        Sort-Object Index | ForEach-Object {
        [PSCustomObject]@{
            DeviceId = [string]$_.Index
            Name = [string]$_.Model
            Path = [string]$_.DeviceID
            Temperature = $null
        }
    })
}
@{ Disks=$items } | ConvertTo-Json -Compress -Depth 3
"""
        response = cls._run_powershell_json(script)
        disks = response.get("Disks", []) if isinstance(response, dict) else []
        if isinstance(disks, dict):
            disks = [disks]
        readings = []
        for index, disk in enumerate(disks[:12]):
            device_id = str(disk.get("DeviceId", index))
            temperature = cls._normalize_temperature(disk.get("Temperature"))
            if temperature is None:
                temperature = cls._read_smart_temperature(disk.get("Path"))
            readings.append((f"D{device_id}", None if temperature is None else round(temperature)))
        if not readings:
            drive_names = []
            for partition in psutil.disk_partitions(all=False):
                drive_name = str(partition.device).rstrip("/\\")
                if re.match(r"^[A-Za-z]:$", drive_name) and drive_name not in drive_names:
                    drive_names.append(drive_name)
            readings = [(drive_name, None) for drive_name in drive_names[:12]]
        return readings

    @classmethod
    def _read_smart_temperature(cls, device_path):
        """使用可选的 smartctl 命令读取 SATA 或 NVMe SMART 温度。"""
        if not device_path:
            return None
        command = ["smartctl", "-a", "-j", str(device_path)]
        try:
            result = base.subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
                creationflags=getattr(base.subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, base.subprocess.TimeoutExpired):
            return None
        try:
            smart_data = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return None

        direct_values = (
            smart_data.get("temperature", {}).get("current"),
            smart_data.get("nvme_smart_health_information_log", {}).get("temperature"),
        )
        for raw_value in direct_values:
            value = cls._normalize_temperature(raw_value)
            if value is not None:
                return value

        attributes = smart_data.get("ata_smart_attributes", {}).get("table", [])
        temperature_names = ("temperature_celsius", "airflow_temperature_cel", "temperature_internal")
        for attribute in attributes:
            attribute_name = str(attribute.get("name", "")).lower()
            if attribute_name not in temperature_names:
                continue
            value = cls._normalize_temperature(attribute.get("raw", {}).get("value"))
            if value is not None:
                return value
        return None

    def _get_disk_temperatures(self):
        """获取最多十二块磁盘的温度，并缓存慢速查询结果。"""
        now = time.monotonic()
        if self.disk_temperature_time and now - self.disk_temperature_time < SLOW_DATA_CACHE_SECONDS:
            return self.disk_temperature_cache

        if base.platform.system() == "Windows":
            self.disk_temperature_cache = self._get_windows_disk_temperatures()
            self.disk_temperature_time = now
            return self.disk_temperature_cache

        readings = []
        missing_indexes = []
        for disk_name in self._list_physical_disks():
            temperature = self._read_block_temperature(disk_name)
            if temperature is None:
                missing_indexes.append(len(readings))
            readings.append([disk_name, temperature])

        fallback_values = self._read_unassigned_sensor_temperatures()
        for index, temperature in zip(missing_indexes, fallback_values):
            readings[index][1] = temperature

        self.disk_temperature_cache = [(name, temperature) for name, temperature in readings]
        self.disk_temperature_time = now
        return self.disk_temperature_cache

    def collect_system_data(self):
        """采集基础系统状态，并读取已缓存的慢速硬件数据。"""
        data = super().collect_system_data()
        data["disk_temperatures"] = tuple(self.disk_temperature_cache)
        return data

    def _log_system_data(self, data):
        """记录后台线程刚发布的磁盘温度完整数据快照。"""
        cpu_temperature = "--°C" if data["temperature"] is None else f"{data['temperature']}°C"
        network_status = "ERROR" if data["ping"] is None else f"PING {data['ping']}ms"
        disk_temperatures = ", ".join(
            f"{disk_name}={'--' if temperature is None else temperature}°C"
            for disk_name, temperature in data["disk_temperatures"]
        ) or "未发现磁盘"
        logger.info(
            "异步数据更新 | CPU=%s%% 温度=%s | 内存=%s%%(%s) | "
            "磁盘=%s%%(%s) | 网络=%s IP=%s 上传=%s 下载=%s | "
            "运行时间=%s | 磁盘温度=[%s]",
            data["cpu"],
            cpu_temperature,
            data["memory_percent"],
            data["memory_capacity"],
            data["disk_percent"],
            data["disk_capacity"],
            network_status,
            data["ip"],
            self._format_speed(data["upload"]),
            self._format_speed(data["download"]),
            self._format_uptime(data["uptime"]),
            disk_temperatures,
        )

    @staticmethod
    def _short_disk_name(disk_name):
        """压缩较长磁盘设备名，使其适合小屏幕网格。"""
        match = re.fullmatch(r"nvme(\d+)n\d+", disk_name)
        if match:
            return f"nv{match.group(1)}"
        match = re.fullmatch(r"mmcblk(\d+)", disk_name)
        if match:
            return f"mm{match.group(1)}"
        return disk_name[-4:]

    @staticmethod
    def _get_ping_color(ping_delay):
        """根据 Ping 延迟阈值返回绿、黄、红三档显示颜色。"""
        if ping_delay < 100:
            return base.GREEN
        if ping_delay < 300:
            return base.YELLOW
        return ERROR_RED

    @staticmethod
    def _get_disk_temperature_color(temperature):
        """根据磁盘温度阈值返回灰、绿、黄、红显示颜色。"""
        if temperature is None:
            return base.GRAY
        if temperature < 35:
            return base.GREEN
        if temperature < 45:
            return base.YELLOW
        return ERROR_RED

    def _draw_header(self, draw, data):
        """绘制顶部信息块，并使用绿色 Ping 或红色错误替换在线状态。"""
        super()._draw_header(draw, data)
        draw.rectangle((121, 0, 159, 31), fill=base.BLACK)
        draw.line((120, 3, 120, 29), fill=base.GRAY)
        draw.text((123, 2), "网络", font=self.font_normal, fill=base.PURPLE)
        if data["ping"] is None:
            draw.text((157, 11), "ERROR", font=self.font_normal, fill=ERROR_RED, anchor="ra")
        else:
            ping_value = min(data["ping"], 999)
            draw.text((157, 11), f"P{ping_value}ms", font=self.font_small,
                      fill=self._get_ping_color(data["ping"]), anchor="ra")
        ip_parts = data["ip"].split(".")
        ip_text = f"*.{'.'.join(ip_parts[-2:])}" if len(ip_parts) == 4 else data["ip"]
        draw.text((123, 22), ip_text, font=self.font_small, fill=base.WHITE)

    def _draw_network(self, draw, data):
        """绘制上下行速率，并在右侧绘制十二盘温度网格。"""
        draw.line((2, 32, 157, 32), fill=base.GRAY)
        draw.line((75, 35, 75, 67), fill=base.GRAY)

        draw.text((3, 35), "↑上传", font=self.font_normal, fill=base.BLUE)
        draw.text((72, 35), self._format_speed(data["upload"]), font=self.font_normal,
                  fill=base.BLUE, anchor="ra")
        self._draw_bars(draw, (3, 44, 72, 49), self.upload_history, base.BLUE)
        draw.text((3, 52), "↓下载", font=self.font_normal, fill=base.GREEN)
        draw.text((72, 52), self._format_speed(data["download"]), font=self.font_normal,
                  fill=base.GREEN, anchor="ra")
        self._draw_bars(draw, (3, 61, 72, 66), self.download_history, base.GREEN)

        temperatures = data["disk_temperatures"]
        draw.text((79, 35), "磁盘温度", font=self.font_normal, fill=base.PURPLE)
        draw.text((157, 35), f"{len(temperatures)}/12", font=self.font_small,
                  fill=base.PURPLE, anchor="ra")
        if not temperatures:
            draw.text((118, 51), "未发现磁盘", font=self.font_normal,
                      fill=base.GRAY, anchor="mm")
            return

        for index, (disk_name, temperature) in enumerate(temperatures[:12]):
            column = index % 3
            row = index // 3
            x = 79 + column * 27
            y = 44 + row * 6
            value_text = "--°" if temperature is None else f"{temperature}°"
            text = f"{self._short_disk_name(disk_name)} {value_text}"
            color = self._get_disk_temperature_color(temperature)
            draw.text((x, y), text, font=self.font_tiny, fill=color)


def main():
    """安装磁盘温度仪表盘实例并启动基础程序入口。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    base.monitor = DiskTemperatureMonitor()
    base.main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n程序已退出。")
    finally:
        if base.ser is not None and base.ser.is_open:
            base.ser.close()
