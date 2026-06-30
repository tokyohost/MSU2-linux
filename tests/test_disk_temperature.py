#!/usr/bin/env python3
"""验证多磁盘温度采集的 Linux 回退逻辑。"""

import json
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
            mock.patch.object(self.monitor, "_scan_smart_devices", return_value={}),
            mock.patch.object(self.monitor, "_list_physical_disks", return_value=["sda"]),
            mock.patch.object(self.monitor, "_read_block_temperature", return_value=None),
            mock.patch.object(self.monitor, "_read_smart_temperature", return_value=38) as smart_reader,
            mock.patch.object(self.monitor, "_read_unassigned_sensor_temperatures", return_value=[]),
        ):
            readings = self.monitor._get_disk_temperatures()

        print(f"SATA SMART 温度采集结果：{readings}")
        self.assertEqual([("sda", 38)], readings)
        smart_reader.assert_called_once_with("/dev/sda", None)

    def test_unmatched_sensor_is_not_assigned_to_wrong_disk(self):
        """验证数量不匹配的无归属传感器不会被错误绑定到某块磁盘。"""
        with (
            mock.patch("msu2_dashboard_disk_temperature.base.platform.system", return_value="Linux"),
            mock.patch.object(self.monitor, "_scan_smart_devices", return_value={}),
            mock.patch.object(self.monitor, "_list_physical_disks", return_value=["sda", "sdb"]),
            mock.patch.object(self.monitor, "_read_block_temperature", return_value=None),
            mock.patch.object(self.monitor, "_read_smart_temperature", return_value=None),
            mock.patch.object(self.monitor, "_read_unassigned_sensor_temperatures", return_value=[47]),
        ):
            readings = self.monitor._get_disk_temperatures()

        print(f"无归属传感器匹配结果：{readings}")
        self.assertEqual([("sda", None), ("sdb", None)], readings)

    def test_smartctl_scan_adds_all_detected_disks_and_device_types(self):
        """验证 SMART 扫描发现的磁盘及接口类型会参与逐盘温度采集。"""
        smart_devices = {
            "sda": ("/dev/sda", "sat"),
            "nvme0n1": ("/dev/nvme0", "nvme"),
        }
        temperatures = {
            ("/dev/sda", "sat"): 36,
            ("/dev/nvme0", "nvme"): 47,
        }
        with (
            mock.patch("msu2_dashboard_disk_temperature.base.platform.system", return_value="Linux"),
            mock.patch.object(self.monitor, "_scan_smart_devices", return_value=smart_devices),
            mock.patch.object(self.monitor, "_list_physical_disks", return_value=["sda"]),
            mock.patch.object(self.monitor, "_read_block_temperature", return_value=None),
            mock.patch.object(
                self.monitor,
                "_read_smart_temperature",
                side_effect=lambda path, disk_type: temperatures[(path, disk_type)],
            ),
            mock.patch.object(self.monitor, "_read_unassigned_sensor_temperatures", return_value=[]),
        ):
            readings = self.monitor._get_disk_temperatures()

        print(f"全部 SMART 磁盘温度采集结果：{readings}")
        self.assertEqual([("nvme0n1", 47), ("sda", 36)], readings)

    def test_smartctl_scan_parses_sata_nvme_and_raid_devices(self):
        """验证 SMART 扫描结果可识别 SATA、NVMe 及 RAID 控制器磁盘。"""
        scan_result = {
            "devices": [
                {"name": "/dev/sda", "type": "sat", "protocol": "ATA"},
                {"name": "/dev/nvme0", "type": "nvme", "protocol": "NVMe"},
                {"name": "/dev/bus/0", "type": "megaraid,0", "protocol": "SCSI"},
                {"name": "/dev/sdb", "type": "sat", "open_error": "拒绝访问"},
            ]
        }
        completed_process = mock.Mock(stdout=json.dumps(scan_result))
        with mock.patch(
            "msu2_dashboard_disk_temperature.base.subprocess.run",
            return_value=completed_process,
        ) as process_runner:
            devices = self.monitor._scan_smart_devices()

        print(f"SMART 自动发现磁盘结果：{devices}")
        self.assertEqual(
            {
                "sda": ("/dev/sda", "sat"),
                "nvme0n1": ("/dev/nvme0", "nvme"),
                "megaraid0": ("/dev/bus/0", "megaraid,0"),
            },
            devices,
        )
        process_runner.assert_called_once()

    def test_disk_temperature_collection_is_not_limited_to_twelve_disks(self):
        """验证后台采集与日志数据不会截断十二块之后的物理磁盘。"""
        disk_names = [f"disk{index}" for index in range(13)]
        with (
            mock.patch("msu2_dashboard_disk_temperature.base.platform.system", return_value="Linux"),
            mock.patch.object(self.monitor, "_scan_smart_devices", return_value={}),
            mock.patch.object(self.monitor, "_list_physical_disks", return_value=disk_names),
            mock.patch.object(self.monitor, "_read_block_temperature", return_value=35),
            mock.patch.object(self.monitor, "_read_unassigned_sensor_temperatures", return_value=[]),
        ):
            readings = self.monitor._get_disk_temperatures()

        print(f"超过十二块磁盘的完整温度采集结果：{readings}")
        self.assertEqual(13, len(readings))
        self.assertEqual(("disk12", 35), readings[-1])


if __name__ == "__main__":
    unittest.main()
