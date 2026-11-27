"""RAG Upload 并发文件锁与心跳租期管理。"""
from __future__ import annotations

import asyncio
import os
import threading
import time

from backend.app.api.routes.rag_upload_subroutes.base import *

def _lock_age_seconds(lock_path: str) -> float | None:
    """读锁的已存在秒数:优先解析 acquire 时写入的时间戳(payload 第 2 行),
    不可解析时回退文件 mtime;都拿不到返回 None(保守不接管)。"""
    try:
        with open(lock_path, "r", encoding="utf-8", errors="replace") as f:
            f.readline()  # 第 1 行是 pid
            ts_line = f.readline().strip()
        ts = float(ts_line)
        if ts > 0:
            return time.time() - ts
    except (OSError, ValueError):
        pass
    try:
        return time.time() - os.path.getmtime(lock_path)
    except OSError:
        return None


def _try_create_lock(lock_path: str) -> int | None:
    """原子创建锁文件:成功返回 fd,已被占用返回 None。"""
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        return None
    try:
        os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode())
    except OSError:
        pass  # 写入失败不影响锁本身
    return fd


def acquire_index_lock(filepath: str) -> int:
    """对 filepath 加非阻塞排他锁(用 .lockfile 原子创建方案)。

    Args:
        filepath: 文件绝对路径。

    Returns:
        文件描述符 fd(指向 .lock 文件)。调用方负责 facade.release_index_lock(fd, filepath)。
        实际为兼容性返回 fd,但调用方必须同时传 filepath 给 release。

    Raises:
        FileLockedByOtherError: 文件已被另一个请求加锁(且锁未超 TTL)。

    设计:
      - 用 sidecar `.lock` 文件 + O_CREAT | O_EXCL 实现原子互斥
      - 跨平台、纯标准库,无需 pywin32/fcntl
      - 跨进程安全(O_EXCL 是 atomic on most filesystems)
      - 跨线程安全(同一进程内 fd 唯一)
      - Stale 自愈:锁存在但超 LOCK_STALE_SECONDS → unlink 后重试一次;
        两个请求同时抢 stale 锁时,后到者 O_EXCL 失败 → 保守报锁占用
    """
    lock_path = filepath + ".lock"
    # B 阶段：正式目录在上传阶段不再预创建（正式文件发布时才落位），
    # 锁文件要与正式文件同目录 → 这里负责确保父目录存在
    parent = os.path.dirname(lock_path)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass
    fd = facade._try_create_lock(lock_path)
    if fd is not None:
        return fd

    # 被占用 → 检查是否崩溃残留的 stale 锁(时间戳/mtime 超 TTL)
    age = facade._lock_age_seconds(lock_path)
    if age is not None and age > facade.LOCK_STALE_SECONDS:
        try:
            os.unlink(lock_path)
        except OSError:
            pass
        logger.warning(f"[RAG] 接管 stale 锁 (age={int(age)}s): {lock_path}")
        fd = facade._try_create_lock(lock_path)
        if fd is not None:
            return fd

    holder_pid = None
    try:
        with open(lock_path, "r", encoding="utf-8", errors="replace") as f:
            holder_pid = f.readline().strip() or None
    except OSError:
        pass
    # 细节（服务器绝对路径/持锁 pid）只进服务端日志：异常信息会原样展示在
    # 前端与操作审计 detail 里，不应泄漏服务器文件系统结构与进程号
    logger.warning(f"[RAG] 锁竞争拒绝: {filepath} (holder_pid={holder_pid})")
    raise FileLockedByOtherError(
        f"文件 {os.path.basename(filepath)} 正在被另一个上传请求处理，请稍后重试"
    )


def release_index_lock(fd: int, filepath: str = "") -> None:
    """释放 acquire_index_lock 获取的锁 + 关闭 fd。

    设计:fd 指向 .lock 文件,关闭 fd 后 unlink .lock 文件(用 filepath 推导)。
    安全:
      - 重复 release / fd=-1 / filepath 空 都不抛异常
      - unlink 失败 swallow(可能在另一进程已被删)
    """
    if fd < 0:
        return
    try:
        os.close(fd)
    except OSError:
        pass
    if filepath:
        lock_path = filepath + ".lock"
        try:
            os.unlink(lock_path)
        except OSError:
            pass


def _start_lock_heartbeat(lock_path: str, stop_event: threading.Event) -> threading.Thread:
    """启动锁心跳线程：持锁期间定期重写 .lock 时间戳。

    把 stale 判定从"固定 30 分钟"变为"心跳超时"：超长索引任务（真实存在，
    30 分钟 TTL 就是按它定的上限）不再被并发请求误判接管；持有进程崩溃后
    心跳停止，时间戳停滞，TTL 后照常自愈接管——两种场景都正确。

    防御：每拍心跳先校验锁文件仍属于本进程（pid 匹配），锁已被他人接管或
    清理时立即退出，绝不复活已易主的锁文件。
    """
    my_pid = str(os.getpid())

    def _beat():
        while not stop_event.wait(facade.LOCK_HEARTBEAT_SECONDS):
            try:
                with open(lock_path, "r", encoding="utf-8", errors="replace") as f:
                    if f.readline().strip() != my_pid:
                        return  # 锁已易主/被清理，退出
                with open(lock_path, "w", encoding="utf-8") as f:
                    f.write(f"{my_pid}\n{time.time()}\n")
            except OSError:
                return  # 锁文件已消失（正常释放），心跳退出

    t = threading.Thread(target=_beat, name="lock-heartbeat", daemon=True)
    t.start()
    return t


for _fn in [
    _lock_age_seconds,
    _try_create_lock,
    acquire_index_lock,
    release_index_lock,
    _start_lock_heartbeat
]:
    facade.register(_fn.__name__, _fn)
