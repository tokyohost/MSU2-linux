#!/usr/bin/env python3
"""为 MSU2 多套显示仪表盘提供统一启动入口。"""

import argparse
import os
import runpy
import sys
from pathlib import Path


TEMPLATE_FILES = (
    "msu2_dashboard_classic.py",
    "msu2_dashboard_temperature.py",
    "msu2_dashboard_overview.py",
    "msu2_dashboard_disk_temperature.py",
)
MODERN_TEMPLATE_FILES = {
    "msu2_dashboard_overview.py",
    "msu2_dashboard_disk_temperature.py",
}
TEMPLATE_ALIASES = {
    "MSU2_LINUX.py": "msu2_dashboard_classic.py",
    "MSU2-LINUX-1.py": "msu2_dashboard_temperature.py",
    "MSU2_LINUX-2.py": "msu2_dashboard_overview.py",
    "MSU2_LINUX-3.py": "msu2_dashboard_disk_temperature.py",
}

def parse_boolean(value):
    """将配置中的常见布尔值转换为真或假。"""
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("必须使用 true/false、yes/no、on/off 或 1/0")


def create_argument_parser():
    """创建统一入口使用的命令行参数解析器。"""
    parser = argparse.ArgumentParser(description="MSU2 Linux 统一启动入口")
    parser.add_argument(
        "--template",
        default=os.environ.get("MSU2_TEMPLATE", "msu2_dashboard_disk_temperature.py"),
        metavar="脚本名称",
        help="显示模板对应的脚本名称",
    )
    parser.add_argument(
        "--ping-target",
        default=os.environ.get("MSU2_PING_TARGET", "1.1.1.1"),
        metavar="域名或IP",
        help="用于检测网络延迟的域名或 IP 地址",
    )
    parser.add_argument(
        "--refresh-interval",
        default=float(os.environ.get("MSU2_REFRESH_INTERVAL", "1.0")),
        type=float,
        metavar="秒",
        help="屏幕刷新间隔，必须大于 0 秒",
    )
    parser.add_argument(
        "--flip-vertical",
        default=parse_boolean(os.environ.get("MSU2_FLIP_VERTICAL", "false")),
        type=parse_boolean,
        metavar="布尔值",
        help="是否启用屏幕上下翻转",
    )
    parser.add_argument("--list-templates", action="store_true", help="列出可用显示模板")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def get_resource_directory():
    """返回源码目录或 PyInstaller 单文件程序的临时资源目录。"""
    bundled_directory = getattr(sys, "_MEIPASS", None)
    if bundled_directory:
        return Path(bundled_directory)
    return Path(__file__).resolve().parent


def run_template(template_name, arguments, remaining_arguments):
    """校验模板名称并使用统一配置启动对应脚本。"""
    template_name = TEMPLATE_ALIASES.get(template_name, template_name)
    if template_name not in TEMPLATE_FILES:
        available = "、".join(TEMPLATE_FILES)
        raise SystemExit(f"不支持的模板脚本：{template_name}；可用模板：{available}")

    script_path = get_resource_directory() / template_name
    if not script_path.is_file():
        raise SystemExit(f"模板脚本不存在：{script_path}")

    os.environ["MSU2_PING_TARGET"] = arguments.ping_target
    os.environ["MSU2_REFRESH_INTERVAL"] = str(arguments.refresh_interval)
    os.environ["MSU2_FLIP_VERTICAL"] = str(arguments.flip_vertical).lower()

    target_arguments = list(remaining_arguments)
    if template_name in MODERN_TEMPLATE_FILES:
        target_arguments = [
            "--ping-target", arguments.ping_target,
            "--refresh-interval", str(arguments.refresh_interval),
            "--flip-vertical", str(arguments.flip_vertical).lower(),
            *target_arguments,
        ]
    sys.argv = [str(script_path), *target_arguments]
    runpy.run_path(str(script_path), run_name="__main__")


def main():
    """解析公共配置并将执行流程分派给选定模板。"""
    parser = create_argument_parser()
    arguments, remaining_arguments = parser.parse_known_args()
    if arguments.list_templates:
        if sys.stdout is not None:
            print("\n".join(TEMPLATE_FILES))
        return
    if arguments.refresh_interval <= 0:
        parser.error("--refresh-interval 必须大于 0")
    if sys.platform == "win32" and getattr(sys, "frozen", False) and not arguments.worker:
        from msu2_windows_tray import WindowsTrayApplication

        tray_arguments = [argument for argument in sys.argv[1:] if argument != "--worker"]
        WindowsTrayApplication(tray_arguments).run()
        return
    run_template(arguments.template, arguments, remaining_arguments)


if __name__ == "__main__":
    main()
