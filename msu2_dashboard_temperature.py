#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

import logging
import os
import threading
import time
import traceback

import numpy as np
import psutil
import serial
import serial.tools.list_ports
from PIL import Image, ImageDraw, ImageFont

# RGB565颜色定义
RED = 0xF800
GREEN = 0x07E0
BLUE = 0x001F
WHITE = 0xFFFF
BLACK = 0x0000
YELLOW = 0xFFE0
CYAN = 0x07FF
MAGENTA = 0xF81F
ORANGE = 0xFC00
GRAY0 = 0xEF7D
GRAY1 = 0x8410
GRAY2 = 0x4208

# 显示参数
SHOW_WIDTH = 160   # 屏幕宽度
SHOW_HEIGHT = 80   # 屏幕高度

# 全局变量
ser = None
Device_State = 0
SER_lock = threading.Lock()
ADC_det = 0
display_mode = 0     # 显示模式: 0=网格布局
refresh_interval = float(os.environ.get("MSU2_REFRESH_INTERVAL", "1.0"))  # 刷新间隔（秒）
lcd_flip_vertical = os.environ.get("MSU2_FLIP_VERTICAL", "false").lower() in {
    "1", "true", "yes", "on"
}  # 屏幕上下翻转
logger = logging.getLogger("MSU2-温度仪表盘")

class SystemMonitor:
    """采集系统资源数据并生成 LCD 仪表盘画面。"""

    def __init__(self):
        """初始化监控数据容器并加载显示字体。"""
        self.monitor_data = {}
        self.font = self.load_font()
        self.data_ready = threading.Event()
        self.data_thread = None

    def start_data_collection(self):
        """启动唯一的后台系统数据采集线程。"""
        if self.data_thread is not None and self.data_thread.is_alive():
            return
        self.data_thread = threading.Thread(
            target=self._data_collection_loop,
            name="系统数据采集",
            daemon=True,
        )
        self.data_thread.start()

    def _data_collection_loop(self):
        """持续在后台更新完整数据快照。"""
        while True:
            started = time.monotonic()
            try:
                data = self.collect_system_data()
                self._log_system_data(data)
                self.data_ready.set()
            except Exception:
                logger.exception("后台数据采集异常")
            time.sleep(max(0.1, refresh_interval - (time.monotonic() - started)))

    @staticmethod
    def _log_system_data(data):
        """记录后台线程刚发布的温度仪表盘数据快照。"""
        logger.info(
            "异步数据更新 | CPU=%s%s | 温度=%s%s | 内存=%s%s %s | 磁盘=%s%s %s",
            data["cpu"]["value"], data["cpu"]["unit"],
            data["temp"]["value"], data["temp"]["unit"],
            data["memory"]["value"], data["memory"]["unit"], data["memory"]["detail"],
            data["disk"]["value"], data["disk"]["unit"], data["disk"]["detail"],
        )
        
    def load_font(self):
        """加载字体，使用18号字体以提高可读性"""
        try:
            font = ImageFont.truetype("arial.ttf", 25)
        except:
            try:
                font = ImageFont.truetype("arial.ttf", 25)
            except:
                font = ImageFont.load_default()
        return font
    
    def collect_system_data(self):
        """在后台线程收集系统资源数据。"""
        data = {}
        
        # CPU使用率
        data['cpu'] = {
            'value': round(psutil.cpu_percent(interval=0.1)),
            'label': 'CPU',
            'unit': '%',
            'color': GREEN,
            'icon': 'C'
        }
        
        # 内存使用率和详细信息
        mem = psutil.virtual_memory()
        mem_total = self.format_bytes(mem.total)
        mem_used = self.format_bytes(mem.used)
        data['memory'] = {
            'value': round(mem.percent),
            'label': 'RAM',
            'unit': '%',
            'detail': f"({mem_used})",
            'color': BLUE,
            'icon': 'M'
        }
        
        # 磁盘使用率和详细信息
        disk_info = psutil.disk_usage("/")
        disk_total = self.format_bytes(disk_info.total)
        disk_used = self.format_bytes(disk_info.used)
        disk_usage = round(disk_info.used * 100 / disk_info.total) if disk_info.total > 0 else 100
        
        data['disk'] = {
            'value': disk_usage,
            'label': 'DSK',
            'unit': '%',
            'detail': f"({disk_used})",
            'color': YELLOW,
            'icon': 'D'
        }
        
        # 网络流量
        net_io = psutil.net_io_counters()
        total_bytes = net_io.bytes_sent + net_io.bytes_recv
        
        data['network'] = {
            'value': self.format_bytes(total_bytes),
            'label': 'NET',
            'unit': '',
            'color': CYAN,
            'icon': 'N'
        }

        # CPU 温度
        cpu_temp = self.get_cpu_temperature()
        data['temp'] = {
            'value': cpu_temp,
            'label': 'TMP',
            'unit': '°C',
            'color': ORANGE,
            'icon': 'T'
        }
        
        self.monitor_data = data
        return data
    
    def get_cpu_temperature(self):
        """获取 Linux CPU 有效传感器中的最高温度。"""
        try:
            temps = psutil.sensors_temperatures()
            if 'coretemp' in temps:
                # Intel CPU
                core_temperatures = [entry.current for entry in temps['coretemp']]
                return round(max(core_temperatures)) if core_temperatures else "N/A"
            elif 'cpu_thermal' in temps:
                # 树莓派等 ARM 设备
                cpu_temperatures = [entry.current for entry in temps['cpu_thermal']]
                return round(max(cpu_temperatures)) if cpu_temperatures else "N/A"
            else:
                return "N/A"
        except Exception as e:
            return "N/A"
    
    def format_bytes(self, bytes_val):
        """格式化字节数显示"""
        for unit in ['B', 'K', 'M', 'G']:
            if bytes_val < 1024.0:
                if unit == 'B':
                    return f"{int(bytes_val)}{unit}"
                else:
                    return f"{bytes_val:.1f}{unit}"
            bytes_val /= 1024.0
        return f"{bytes_val:.1f}T"
    
    def format_display_text(self, key, show_icon=False):
        """格式化显示文本，冒号前加空格，包含详细信息"""
        if key not in self.monitor_data:
            return ""
        
        item = self.monitor_data[key]
        icon_part = f"[{item['icon']}] " if show_icon else ""
        detail_part = item.get('detail', '')
        if detail_part:
            return f"{icon_part}{item['label']} : {item['value']}{item['unit']} {detail_part}"
        else:
            return f"{icon_part}{item['label']} : {item['value']}{item['unit']}"
    
    def rgb565_to_rgb(self, color_565):
        """将RGB565颜色转换为RGB888"""
        r = ((color_565 >> 11) & 0x1F) << 3
        g = ((color_565 >> 5) & 0x3F) << 2
        b = (color_565 & 0x1F) << 3
        return (r, g, b)
    
    def create_display_image(self):
        """仅使用后台数据快照创建网格布局图像。"""
        self.start_data_collection()
        
        # 创建黑色背景图像
        image = Image.new("RGB", (SHOW_WIDTH, SHOW_HEIGHT), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # 只使用网格布局
        self.draw_grid_layout(draw)
        
        return image
    
    def draw_grid_layout(self, draw):
        """绘制网格布局（2x2）"""
        layouts = [
            {'key': 'temp', 'position': (5, 5)},      # 左上
            {'key': 'memory', 'position': (70, 5)},  # 右上
            {'key': 'disk', 'position': (5, 45)},    # 左下
            {'key': 'cpu', 'position': (100, 45)},   # 右下
        ]

        for layout in layouts:
            if layout['key'] in self.monitor_data:
                item = self.monitor_data[layout['key']]
                text = self.format_display_text(layout['key'], show_icon=False)  # 不显示[]和图标
                color = self.rgb565_to_rgb(item['color'])
                draw.text(layout['position'], text, fill=color, font=self.font)

# 创建全局监控器实例
monitor = SystemMonitor()
frame_lock = threading.Lock()
frame_payload = b""
frame_thread = None

def digit_to_ints(di):
    """将32位整数拆分为4个字节"""
    return [(di >> 24) & 0xFF, (di >> 16) & 0xFF, (di >> 8) & 0xFF, di & 0xFF]

def rgb888_to_rgb565(rgb888_array):
    """RGB888转RGB565格式"""
    r = (rgb888_array[:, :, 0] & 0xF8) << 8
    g = (rgb888_array[:, :, 1] & 0xFC) << 3
    b = (rgb888_array[:, :, 2] & 0xF8) >> 3
    rgb565 = r | g | b
    return rgb565

def Screen_Date_Process(Photo_data):
    """对图像数据进行压缩处理，减少传输数据量"""
    total_data_size = len(Photo_data)
    data_per_page = 128
    data_page1 = 0
    data_page2 = 0
    hex_use = bytearray()
    
    # 按页处理数据
    for _ in range(0, total_data_size // data_per_page):
        data_page1 = data_page2
        data_page2 += data_per_page
        data_w = Photo_data[data_page1: data_page2]
        cmp_use = data_w[::2] << 16 | data_w[1::2]

        # 找出最频繁的颜色作为背景色
        u, c = np.unique(cmp_use, return_counts=True)
        result = u[c.argmax()]
        hex_use.extend([2, 4])
        hex_use.extend(digit_to_ints(result))

        # 只记录与背景色不同的像素
        for i, cmp_value in enumerate(cmp_use):
            if cmp_value != result:
                hex_use.extend([4, i])
                hex_use.extend(digit_to_ints(cmp_value))

        hex_use.extend([2, 3, 8, 1, 0, 0])

    # 处理剩余数据
    remaining_data_size = total_data_size % data_per_page
    if remaining_data_size != 0:
        data_w = Photo_data[-remaining_data_size:]
        data_w = np.append(data_w, np.full(data_per_page - remaining_data_size, 0xFF, dtype=np.uint32))
        cmp_use = data_w[::2] << 16 | data_w[1::2]
        for i, cmp_value in enumerate(cmp_use):
            hex_use.extend([4, i])
            hex_use.extend(digit_to_ints(cmp_value))
        hex_use.extend([2, 3, 8, 0, remaining_data_size * 2, 0])
    return hex_use

def LCD_Set_XY(LCD_D0, LCD_D1):
    """设置LCD显示起始坐标"""
    hex_use = bytearray()
    hex_use.append(2)  # LCD多次写入命令
    hex_use.append(0)  # 设置起始位置指令
    hex_use.append(LCD_D0 // 256)  # X坐标高字节
    hex_use.append(LCD_D0 % 256)   # X坐标低字节
    hex_use.append(LCD_D1 // 256)  # Y坐标高字节
    hex_use.append(LCD_D1 % 256)   # Y坐标低字节
    return hex_use

def LCD_Set_Size(LCD_D0, LCD_D1):
    """设置LCD显示区域大小"""
    hex_use = bytearray()
    hex_use.append(2)  # LCD多次写入命令
    hex_use.append(1)  # 设置大小指令
    hex_use.append(LCD_D0 // 256)  # 宽度高字节
    hex_use.append(LCD_D0 % 256)   # 宽度低字节
    hex_use.append(LCD_D1 // 256)  # 高度高字节
    hex_use.append(LCD_D1 % 256)   # 高度低字节
    return hex_use

def LCD_Set_Color(LCD_D0, LCD_D1):
    """设置LCD前景色和背景色"""
    hex_use = bytearray()
    hex_use.append(2)  # LCD多次写入命令
    hex_use.append(2)  # 设置颜色指令
    hex_use.append(LCD_D0 // 256)  # 前景色高字节
    hex_use.append(LCD_D0 % 256)   # 前景色低字节
    hex_use.append(LCD_D1 // 256)  # 背景色高字节
    hex_use.append(LCD_D1 % 256)   # 背景色低字节
    SER_rw(hex_use, read=False)    # 发送指令

def LCD_ADD(LCD_X, LCD_Y, LCD_X_Size, LCD_Y_Size):
    """设置LCD显示区域并准备写入数据"""
    hex_use = LCD_Set_XY(LCD_X, LCD_Y)
    hex_use.extend(LCD_Set_Size(LCD_X_Size, LCD_Y_Size))
    hex_use.append(2)  # LCD多次写入命令
    hex_use.append(3)  # 设置指令
    hex_use.append(7)  # 载入地址
    hex_use.append(0)
    hex_use.append(0)
    hex_use.append(0)

    recv = SER_rw(hex_use)  # 发送指令并接收回复
    if len(recv) > 1 and recv[0] == 2 and recv[1] == 3:
        return 1
    else:
        print("LCD_ADD 指令失败: %s" % recv)
        set_device_state(0)
        return 0

def LCD_State(LCD_S):
    """设置LCD显示方向"""
    hex_use = bytearray()
    hex_use.append(2)   # LCD多次写入命令
    hex_use.append(3)   # 设置指令
    hex_use.append(10)  # 显示方向指令
    hex_use.append(LCD_S)
    hex_use.append(0)
    hex_use.append(0)

    recv = SER_rw(hex_use)  # 发送指令并接收回复
    if len(recv) > 5 and recv[0] == hex_use[0] and recv[1] == hex_use[1]:
        return 1
    else:
        print("LCD 方向设置失败: %s" % recv)
        set_device_state(0)
        return 0

def SER_Write(Data_U0):
    """向串口写入数据"""
    global ser
    ser.reset_input_buffer()  # 清空输入缓冲区
    ser.write(Data_U0)
    ser.flush()

def SER_Read():
    """从串口读取数据"""
    global ser
    trytimes = 500000  # 防止无限等待
    recv = ser.read(ser.in_waiting)
    while len(recv) == 0 and trytimes > 0:
        recv = ser.read(ser.in_waiting)
        trytimes -= 1
    if trytimes == 0:
        print("串口读取超时")
        return 0
    return recv

def SER_rw(data, read=True, size=0):
    """串口读写操作（线程安全）"""
    global ser

    result = bytearray()
    SER_lock.acquire()
    try:
        if not ser.is_open:
            print("设备未连接，取消串口读写")
            return result

        SER_Write(data)  # 发送数据
        if not read:
            return result
            
        # 读取回复数据
        while True:
            recv = SER_Read()
            if recv == 0:
                return result
            result.extend(recv)
            if len(result) >= size:
                return result
    except Exception as e:
        print("串口读写异常: %s" % e)
        ser.close()
    finally:
        SER_lock.release()
    
    # 异常后处理
    set_device_state(0)
    return result

def set_device_state(state):
    """设置设备连接状态"""
    global ser, Device_State
    if Device_State != state:
        Device_State = state
        if Device_State == 0:
            ser.close()

def show_PC_state():
    """立即读取后台已编码帧并发送到 LCD。"""
    with frame_lock:
        payload = frame_payload
    if payload:
        SER_rw(payload, read=False)


def render_frame_task():
    """在后台持续绘图并完成颜色转换及协议压缩。"""
    global frame_payload
    while True:
        started = time.monotonic()
        try:
            image = monitor.create_display_image()
            rgb888 = np.asarray(image, dtype=np.uint32)
            rgb565 = rgb888_to_rgb565(rgb888)
            payload = bytes(Screen_Date_Process(rgb565.flatten()))
            with frame_lock:
                frame_payload = payload
        except Exception:
            print("后台帧生成异常：%s" % traceback.format_exc())
        time.sleep(max(0.1, refresh_interval - (time.monotonic() - started)))


def start_background_tasks():
    """启动数据采集与帧生成后台线程。"""
    global frame_thread
    monitor.start_data_collection()
    if frame_thread is not None and frame_thread.is_alive():
        return
    frame_thread = threading.Thread(target=render_frame_task, name="屏幕帧生成", daemon=True)
    frame_thread.start()

def Get_MSN_Device(port_list):
    """搜索并连接MSN设备"""
    global ser, ADC_det
    if ser is not None and ser.is_open:
        ser.close()

    My_MSN_Device = None
    for port in port_list:
        try:
            # 尝试打开串口
            ser = serial.Serial(port.device, 115200, timeout=5.0, write_timeout=5.0, inter_byte_timeout=0.1)
            recv = SER_Read()
            if recv == 0:
                print("未接收到设备响应: %s" % port.device)
                ser.close()
                continue
        except Exception as e:
            print("%s 无法打开，可能被其他程序占用: %s" % (port.device, e))
            if ser is not None and ser.is_open:
                ser.close()
            time.sleep(0.2)
            continue

        # 验证设备标识
        for n in range(0, len(recv) - 5):
            version1 = recv[n + 4] - 48
            version2 = recv[n + 5] - 48
            if recv[n: n + 4] != b'\x00MSN' or not (0 <= version1 < 10 and 0 <= version2 < 10):
                continue
            
            msn_version = 2
            hex_use = b"\x00MSNCN"
            recv = SER_rw(hex_use)
            
            if recv[-6:] == hex_use:
                My_MSN_Device = type('', (object,), {'device': port.device, 'version': msn_version})()
                print("设备连接成功: %s (版本: %d)" % (port.device, msn_version))
                break

        if My_MSN_Device is None:
            print("设备验证失败: %s" % port.device)
            ser.close()
        else:
            break

    if My_MSN_Device is None:
        return

    # 初始化设备
    lcd_direction = 1 if lcd_flip_vertical else 0
    LCD_State(lcd_direction)  # 设置LCD显示方向（0正常，1上下翻转）
    
    # 校准按键基准值
    adc_readings = [Read_ADC_CH(9) for _ in range(3)]
    ADC_det = sum(adc_readings) // 3 - 200
    
    set_device_state(1)
    print("设备初始化完成")

def Read_ADC_CH(ch):
    """读取指定ADC通道的数值"""
    hex_use = bytearray()
    hex_use.append(8)  # ADC读取命令
    hex_use.append(ch) # 通道号
    hex_use.append(0)
    hex_use.append(0)
    hex_use.append(0)
    hex_use.append(0)

    recv = SER_rw(hex_use)  # 发送指令并接收回复
    if len(recv) > 5 and recv[0] == hex_use[0] and recv[1] == hex_use[1]:
        return recv[4] * 256 + recv[5]
    else:
        print("ADC读取失败，将重新连接: %s" % recv)
        set_device_state(0)
        return 0

def daemon_task():
    """主循环任务"""
    global Device_State

    start_background_tasks()
    while True:
        try:
            if Device_State == 1:
                # 设备已连接，正常显示系统状态
                started = time.monotonic()
                LCD_ADD(0, 0, SHOW_WIDTH, SHOW_HEIGHT)
                LCD_Set_Color(WHITE, BLACK)
                show_PC_state()
                time.sleep(max(0, refresh_interval - (time.monotonic() - started)))
                continue

            # 搜索可用设备
            print("正在搜索设备...")
            port_list = list(serial.tools.list_ports.comports())
            # 筛选WCH芯片设备（VID: 0x1a86）
            wch_port_list = [x for x in port_list if hasattr(x, 'vid') and x.vid == 0x1a86]
            
            if wch_port_list:
                Get_MSN_Device(wch_port_list)
                if Device_State != 0:
                    continue

            print("未找到可用设备，请检查连接")
            time.sleep(2)  # 等待2秒后重试
            
        except Exception as e:
            print("主任务异常: %s" % traceback.format_exc())
            time.sleep(1)

if __name__ == "__main__":
    try:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        print("=" * 50)
        print("    系统资源监控工具 v2.0")
        print("=" * 50)
        
        # ================ 配置区域 ================
        # 显示模式配置: 0=网格布局（已固定）
        display_mode = 0
        
        # 刷新频率和屏幕翻转由统一入口或环境变量配置。
        # =========================================
        
        print("当前配置:")
        print(f"- 显示模式: 网格布局")
        print(f"- 刷新频率: {refresh_interval}秒")
        print(f"- 屏幕翻转: {'启用' if lcd_flip_vertical else '禁用'}")
        print("-" * 50)
        print("功能说明:")
        print("- 实时显示CPU、内存、磁盘、CPU温度")
        print("- 显示CPU温度（仅Linux支持）")
        print("- 使用2x2网格布局显示")
        print("- 按 Ctrl+C 退出程序")
        print("=" * 50)
        print("正在启动监控...")
        
        daemon_task()
        
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print("程序异常: %s" % traceback.format_exc())
    finally:
        if ser is not None and ser.is_open:
            ser.close()
        print("程序已退出")
