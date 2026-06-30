#!/usr/bin/env python3
"""在 Linux 实机上验证全部物理磁盘的发现与温度读取能力。"""

import platform
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from msu2_dashboard_disk_temperature import DiskTemperatureMonitor


@unittest.skipUnless(platform.system() == "Linux", "仅在 Linux 或飞牛 OS 实机上执行")
class SystemDiskTemperatureTest(unittest.TestCase):
    """验证当前系统全部物理磁盘均可被发现并读取温度。"""

    def setUp(self):
        """创建不启动屏幕及后台线程的磁盘温度采集器。"""
        self.monitor = DiskTemperatureMonitor.__new__(DiskTemperatureMonitor)
        self.monitor.disk_temperature_cache = []
        self.monitor.disk_temperature_time = 0.0

    def test_all_system_disks_have_temperature(self):
        """真实枚举全部磁盘并验证每块磁盘均返回有效温度。"""
        disks = self.monitor._discover_linux_disks()
        discovered_disks = [disk_name for disk_name, _, _ in disks]

        print("\n系统磁盘温度实机测试")
        print("发现磁盘：")
        for disk_name, device_path, device_type in disks:
            print(
                f"  {disk_name:<12} 路径={device_path:<18} "
                f"类型={device_type or '自动识别'}"
            )

        self.assertTrue(
            discovered_disks,
            "未发现任何物理磁盘，请检查 /sys/class/block 与 smartmontools 安装状态",
        )

        readings = self.monitor._collect_linux_disk_temperatures(disks)

        print("读取结果：")
        for disk_name, temperature in readings:
            _, device_path, device_type = next(
                disk for disk in disks if disk[0] == disk_name
            )
            temperature_text = "读取失败" if temperature is None else f"{temperature}°C"
            print(
                f"  {disk_name:<12} 路径={device_path:<18} "
                f"类型={device_type or '自动识别':<12} 温度={temperature_text}"
            )

        reading_names = {disk_name for disk_name, _ in readings}
        missing_disks = [
            disk_name
            for disk_name, temperature in readings
            if temperature is None
        ]
        self.assertEqual(
            set(discovered_disks),
            reading_names,
            "发现的磁盘与温度采集结果不一致",
        )
        self.assertFalse(
            missing_disks,
            "以下磁盘未读取到温度："
            f"{', '.join(missing_disks)}；请使用 sudo smartctl -a -j /dev/<设备名> 检查",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
