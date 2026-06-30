#!/usr/bin/env python3
"""验证多磁盘温度采集的 Linux 回退逻辑。"""

import sys
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from msu2_dashboard_disk_temperature import DiskTemperatureMonitor


class DiskTemperatureMonitorTest(unittest.TestCase):
    """验证磁盘温度采集器在不同传感器条件下的行为。"""

    def setUp(self):
        """创建不启动后台线程的最小监控器实例。"""
        self.monitor = DiskTemperatureMonitor.__new__(DiskTemperatureMonitor)
        self.monitor.disk_temperature_cache = []
        self.monitor.disk_temperature_time = 0.0

    def test_sata_disk_uses_smartctl_when_sysfs_has_no_temperature(self):
        """验证 SATA 磁盘缺少 sysfs 温度时会按设备名查询 SMART。"""
        with (
            mock.patch("msu2_dashboard_disk_temperature.base.platform.system", return_value="Linux"),
            mock.patch.object(self.monitor, "_list_physical_disks", return_value=["sda"]),
            mock.patch.object(self.monitor, "_read_block_temperature", return_value=None),
            mock.patch.object(self.monitor, "_read_smart_temperature", return_value=38) as smart_reader,
            mock.patch.object(self.monitor, "_read_unassigned_sensor_temperatures", return_value=[]),
        ):
            readings = self.monitor._get_disk_temperatures()

        print(f"SATA SMART 温度采集结果：{readings}")
        self.assertEqual([("sda", 38)], readings)
        smart_reader.assert_called_once_with("/dev/sda")

    def test_unmatched_sensor_is_not_assigned_to_wrong_disk(self):
        """验证数量不匹配的无归属传感器不会被错误绑定到某块磁盘。"""
        with (
            mock.patch("msu2_dashboard_disk_temperature.base.platform.system", return_value="Linux"),
            mock.patch.object(self.monitor, "_list_physical_disks", return_value=["sda", "sdb"]),
            mock.patch.object(self.monitor, "_read_block_temperature", return_value=None),
            mock.patch.object(self.monitor, "_read_smart_temperature", return_value=None),
            mock.patch.object(self.monitor, "_read_unassigned_sensor_temperatures", return_value=[47]),
        ):
            readings = self.monitor._get_disk_temperatures()

        print(f"无归属传感器匹配结果：{readings}")
        self.assertEqual([("sda", None), ("sdb", None)], readings)


if __name__ == "__main__":
    unittest.main()
