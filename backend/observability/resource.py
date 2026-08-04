"""
资源监控模块
监控系统资源使用情况，防止内存溢出和CPU过载
"""
import os
import time
import psutil
from typing import Dict, Optional
from backend.shared.logger import logger


class ResourceMonitor:
    """系统资源监控器"""

    def __init__(self):
        self.process = psutil.Process(os.getpid())
        self.start_time = time.time()
        self.request_count = 0
        self.warning_count = 0

    def get_memory_info(self) -> Dict[str, float]:
        """获取内存使用信息"""
        try:
            memory = self.process.memory_info()
            total_memory = psutil.virtual_memory().total
            return {
                'rss_mb': memory.rss / (1024 * 1024),
                'vms_mb': memory.vms / (1024 * 1024),
                'percent': self.process.memory_percent(),
                'system_percent': psutil.virtual_memory().percent,
                'available_mb': psutil.virtual_memory().available / (1024 * 1024),
            }
        except Exception as e:
            logger.error(f"获取内存信息失败: {e}")
            return {}

    def get_cpu_info(self) -> Dict[str, float]:
        """获取CPU使用信息"""
        try:
            return {
                'process_percent': self.process.cpu_percent(interval=0.1),
                'system_percent': psutil.cpu_percent(interval=0.1),
                'cpu_count': psutil.cpu_count(),
                'load_avg': psutil.getloadavg() if hasattr(psutil, 'getloadavg') else None,
            }
        except Exception as e:
            logger.error(f"获取CPU信息失败: {e}")
            return {}

    def check_resources(self, memory_threshold: float = 0.85, cpu_threshold: float = 0.90) -> bool:
        """
        检查系统资源是否超过阈值

        Args:
            memory_threshold: 内存警告阈值(0-1)
            cpu_threshold: CPU警告阈值(0-1)

        Returns:
            True表示资源正常，False表示资源紧张
        """
        try:
            mem_info = self.get_memory_info()
            cpu_info = self.get_cpu_info()

            system_mem_percent = mem_info.get('system_percent', 0) / 100
            process_mem_percent = mem_info.get('percent', 0) / 100
            cpu_percent = cpu_info.get('system_percent', 0) / 100

            warnings = []

            if system_mem_percent > memory_threshold:
                warning_msg = f"系统内存使用过高: {system_mem_percent*100:.1f}% (阈值: {memory_threshold*100}%)"
                warnings.append(warning_msg)
                logger.warning(warning_msg)

            if process_mem_percent > memory_threshold * 0.8:
                warning_msg = f"进程内存使用过高: {process_mem_percent*100:.1f}%"
                warnings.append(warning_msg)
                logger.warning(warning_msg)

            if cpu_percent > cpu_threshold:
                warning_msg = f"CPU使用率过高: {cpu_percent*100:.1f}% (阈值: {cpu_threshold*100}%)"
                warnings.append(warning_msg)
                logger.warning(warning_msg)

            if warnings:
                self.warning_count += len(warnings)
                return False

            return True

        except Exception as e:
            logger.error(f"资源检查失败: {e}")
            return True  # 出错时不阻塞流程

    def log_status(self):
        """记录当前资源状态（用于调试）"""
        try:
            mem_info = self.get_memory_info()
            cpu_info = self.get_cpu_info()
            logger.debug(
                f"资源状态 | "
                f"内存: {mem_info.get('rss_mb', 0):.1f}MB ({mem_info.get('system_percent', 0):.1f}%) | "
                f"CPU: {cpu_info.get('system_percent', 0):.1f}% | "
                f"请求数: {self.request_count} | "
                f"警告数: {self.warning_count}"
            )
        except Exception as e:
            logger.error(f"记录资源状态失败: {e}")

    def increment_request(self):
        """增加请求计数"""
        self.request_count += 1

    def get_uptime(self) -> float:
        """获取运行时间（秒）"""
        return time.time() - self.start_time


# 全局资源监控器实例
resource_monitor = ResourceMonitor()
