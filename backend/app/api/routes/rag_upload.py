"""RAG 上传路由 — PR-2.x 从 rag.py 抽出。"""
import asyncio, os, shutil, uuid, time
from asyncio import Queue
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from backend.app.api.deps import get_rag_pipeline, require_rag_ready
from backend.config.rag import RAG_MAX_FILE_SIZE, RAG_TMP_DIR, RAG_UPLOAD_CHUNK_SIZE, RAG_UPLOAD_EMIT_BYTES, RAG_UPLOAD_EMIT_MS
# F7: IncrementalIndexer 不在模块顶层导入（导入链含 langchain/tracer 等重依赖），
# 改为 _do_index_sync 内惰性导入，路由模块冷启动不再被拖慢。
from backend.rag.progress_listener import ProgressListener
# 显式导入替代 import *（详见 rag_documents.py 同处注释）
from backend.app.api.routes._rag_shared import (
    _extract_source,
    _get_registry,
    _progress_queues,
    _safe_log_op,
    _sse_encode,
)
from backend.shared.logger import logger

router = APIRouter()


# ============ 文件锁（P1-2 防止同文件并发 race condition）============
# 背景:_do_index_sync 在线程池跑,duplicate 检测和 reindex 流程不是原子的。
# 两个并发请求同时上传同一文件可能都通过 duplicate 检测,然后都走到 _index_file,
# 导致同 doc_id 被写两次到向量库（rerank 阶段被同 chunk 命中两次,稀释 mrr）。
#
# 修法:Windows 用 msvcrt.locking,Linux/macOS 用 fcntl.flock。
# 用非阻塞锁 — 第二个请求立即抛 FileLockedByOtherError,而不是阻塞等待。
# 阻塞等待会让前端 SSE 超时,而且浪费资源。
class FileLockedByOtherError(Exception):
    """同文件正在被另一个请求处理,当前请求拒绝（避免双写向量库）。"""


# Stale 锁 TTL:进程崩溃残留的 .lock 超过该时长后允许被新请求接管(自愈)。
# 背景:旧实现无任何回收路径,索引中途崩溃后 .lock 永久残留,
# 同名文件此后每次上传都被 FileLockedByOtherError 拒绝。
# 取 30 分钟:大于 50MB 上限文件的最慢索引耗时,避免误抢仍在跑的锁。
LOCK_STALE_SECONDS = 30 * 60


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
        文件描述符 fd(指向 .lock 文件)。调用方负责 release_index_lock(fd, filepath)。
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
    fd = _try_create_lock(lock_path)
    if fd is not None:
        return fd

    # 被占用 → 检查是否崩溃残留的 stale 锁(时间戳/mtime 超 TTL)
    age = _lock_age_seconds(lock_path)
    if age is not None and age > LOCK_STALE_SECONDS:
        try:
            os.unlink(lock_path)
        except OSError:
            pass
        logger.warning(f"[RAG] 接管 stale 锁 (age={int(age)}s): {lock_path}")
        fd = _try_create_lock(lock_path)
        if fd is not None:
            return fd

    holder_pid = None
    try:
        with open(lock_path, "r", encoding="utf-8", errors="replace") as f:
            holder_pid = f.readline().strip() or None
    except OSError:
        pass
    raise FileLockedByOtherError(
        f"文件 {filepath} 正在被另一个上传请求处理（holder_pid={holder_pid}）"
    )


def sha256_of_file(path: str, chunk_size: int = 8 * 1024 * 1024) -> str:
    """分块流式计算文件 SHA256 — 避免整文件读入内存。

    背景:旧实现 hashlib.sha256(f.read()) 对 50MB 上限的文件一次性
    读入内存,并发上传时内存峰值翻倍。改为固定块大小流式读取。
    """
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


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


def cleanup_stale_upload_artifacts(docs_dir: str, tmp_dir: str,
                                   max_age_seconds: float = LOCK_STALE_SECONDS) -> dict:
    """启动清理:回收 docs 目录下超龄 .lock 与中断上传遗留的孤儿 tmp 文件。

    两类残留都来自进程崩溃/强杀,不清理会持续积累:
      - .lock 残留 → 对应文件永久被 FileLockedByOtherError 拒绝
      - tmp 残留 → 磁盘占用泄漏
    只删 mtime 超过 max_age_seconds 的文件,不误伤进行中的上传/索引。

    Returns: {"locks_removed": int, "tmp_removed": int}
    """
    counts = {"locks_removed": 0, "tmp_removed": 0}
    now = time.time()

    for root, _dirs, files in os.walk(docs_dir):
        for fname in files:
            if not fname.endswith(".lock"):
                continue
            p = os.path.join(root, fname)
            try:
                if now - os.path.getmtime(p) > max_age_seconds:
                    os.unlink(p)
                    counts["locks_removed"] += 1
            except OSError as e:
                logger.warning(f"[RAG] stale 锁清理失败 {p}: {e}")

    if os.path.isdir(tmp_dir):
        for fname in os.listdir(tmp_dir):
            p = os.path.join(tmp_dir, fname)
            if not os.path.isfile(p):
                continue
            try:
                if now - os.path.getmtime(p) > max_age_seconds:
                    os.unlink(p)
                    counts["tmp_removed"] += 1
            except OSError as e:
                logger.warning(f"[RAG] 孤儿 tmp 清理失败 {p}: {e}")

    if counts["locks_removed"] or counts["tmp_removed"]:
        logger.info(f"[RAG] 启动清理: {counts['locks_removed']} 个 stale 锁, "
                    f"{counts['tmp_removed']} 个孤儿 tmp")
    return counts


# ============ F11: 进度队列过期清理 ============
# 背景:旧实现的过期清理仅在新上传时触发（sync_upload_impl 内联），
# 无上传流量时过期队列永久滞留。现抽为独立函数，除上传入口复用外，
# 由 server 启动的后台定时任务周期性驱动。
def cleanup_expired_progress_queues() -> int:
    """清理超过 PROGRESS_QUEUE_TTL_SECONDS 的进度队列，返回清理条数。

    安全:队列残留只发生在客户端从未订阅 SSE 且后台任务已结束的极端场景；
    超 30 分钟的队列不可能仍有活跃消费者（后台索引远超此时长）。
    """
    now = time.time()
    expired = [uid for uid, q in _progress_queues.items()
               if getattr(q, "_created_at", 0) < now - PROGRESS_QUEUE_TTL_SECONDS]
    for uid in expired:
        _progress_queues.pop(uid, None)
    if expired:
        logger.info(f"[RAG] 清理过期进度队列 {len(expired)} 个")
    return len(expired)


async def progress_queue_gc_loop(interval_seconds: float = 300) -> None:
    """后台定时清理过期进度队列（F11），由 server 启动时 create_task。

    异常不退出循环 — GC 任务自身挂掉比队列滞留更糟，单轮失败只告警。
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            cleanup_expired_progress_queues()
        except Exception as e:
            logger.warning(f"[RAG] 进度队列定时清理异常: {e}")


# ============ MIME 白名单（P0-2 收紧 / F6 单一来源）============
# 设计要点：
#   1. 支持的扩展名从解析器注册表 PARSABLE_EXTS 派生（F6 单一来源），
#      与解析层永远一致；新增解析器自动对上传开放，无需同步多处名单。
#   2. content_type 为空时（curl 命令行场景）由 _validate_mime 空值分支提前放行，
#      落盘后靠魔数校验兜底；白名单表只登记"显式声明时允许的具体 MIME"。
#      历史教训：曾在表里列 octet-stream 作兜底，但该条目永不可达
#      （空 content_type 提前返回、显式 octet-stream 前置拒绝），已清理防误导。
#   3. PDF 不接受 octet-stream（PDF 是二进制格式，octet-stream 兜底没意义）。
#   4. 模块级常量，方便纯函数 import 测试。
from backend.rag.preprocessing.parser import PARSABLE_EXTS

_MIME_BY_EXT: dict[str, set[str]] = {
    "pdf":      {"application/pdf"},
    "md":       {"text/markdown", "text/plain"},
    "markdown": {"text/markdown", "text/plain"},
    "txt":      {"text/plain"},
    "docx":     {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "xlsx":     {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
}

ALLOWED_MIME_TYPES: dict[str, set[str]] = {
    ext: mimes for ext, mimes in _MIME_BY_EXT.items()
    if f".{ext}" in PARSABLE_EXTS
}

SUPPORTED_EXTS: frozenset[str] = frozenset(ALLOWED_MIME_TYPES.keys())

# F10: Content-Length 预检余量 — multipart 封装（boundary + 头字段）比裸文件大
# 几百字节到几 KB，贴上限的文件会被误拒；预检放宽一个余量，
# 精确上限仍由 sync_upload_impl 流式字节计数强制执行（双保险不放松）。
_MULTIPART_OVERHEAD = 16 * 1024

# F11: 进度队列过期阈值（与 sync_upload_impl 内联清理同口径）
PROGRESS_QUEUE_TTL_SECONDS = 1800


def _validate_mime(ext: str, content_type: str | None) -> tuple[bool, str]:
    """校验 MIME 与扩展名一致性（纯函数）。

    Args:
        ext: 文件扩展名（小写，无点），如 "md" / "pdf"。
        content_type: HTTP 声明的 Content-Type（含 charset 后缀也 OK），可能为 None/""。

    Returns:
        (ok, error_msg) — ok=True 时 error_msg 为空字符串。

    规则:
      - ext 不在 SUPPORTED_EXTS → 拒（unsupported ext）
      - content_type 为 None / "" → 通过（走 magic 校验兜底）
      - 显式 content_type（去 charset 后）必须在 ALLOWED_MIME_TYPES[ext] 内
        显式声明 application/octet-stream 必须拒绝（P0-2 收紧）
    """
    if ext not in SUPPORTED_EXTS:
        return False, f"unsupported ext: .{ext}"
    if not content_type or not content_type.strip():
        # content_type 为空（curl 命令行等场景）→ 走 magic 校验兜底
        return True, ""
    # strip charset 参数（如 "text/plain; charset=utf-8" → "text/plain"）
    ctype = content_type.split(";", 1)[0].strip().lower()
    if not ctype:
        return True, ""
    # P0-2 收紧：客户端显式声明 application/octet-stream 必须拒绝。
    # 原因：octet-stream 兜底仅在 content_type 为空时生效（curl/某些 SDK 默认场景），
    # 一旦客户端声明了 octet-stream，多半是扩展名伪装（如 MD 实际是二进制），
    # 必须走 magic 校验后端兜底，而不是 MIME 层面放行。
    # 历史教训：P0-1 GBK 乱码文件就是通过 octet-stream 蒙混进 doc_db 的。
    if ctype == "application/octet-stream":
        return False, (
            f"explicit application/octet-stream not allowed for .{ext}; "
            f"客户端必须声明具体 MIME（如 text/markdown / application/pdf）"
        )
    if ctype not in ALLOWED_MIME_TYPES[ext]:
        return False, f"MIME type not allowed for .{ext}: {ctype}"
    return True, ""


@router.post("/upload")
async def upload_document(request: Request, file: UploadFile = File(...),
                          kb_id: str = Form("policy_general"),
                          department: str = Form("general")):
    """P0-1 流式上传: 临时文件 + atomic rename + 双保险大小限制 + SSE 进度"""
    require_rag_ready()
    from backend.config.knowledge_base import validate_kb_dept
    if not validate_kb_dept(kb_id, department):
        return {"ok": False, "error": f"知识库 '{kb_id}' 不允许选择部门 '{department}'"}
    from backend.config.rag import (
        RAG_MAX_FILE_SIZE, RAG_TMP_DIR, RAG_UPLOAD_CHUNK_SIZE,
        RAG_UPLOAD_EMIT_BYTES, RAG_UPLOAD_EMIT_MS,
    )

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    ok, err = _validate_mime(ext, file.content_type)
    if not ok:
        return {"ok": False, "error": err}

    max_size = RAG_MAX_FILE_SIZE * 1024 * 1024
    cl = request.headers.get("content-length")
    # F10: 预检带 multipart 余量，贴上限文件不误拒；超限仍由流式计数拦截
    if cl and cl.isdigit() and int(cl) > max_size + _MULTIPART_OVERHEAD:
        return {"ok": False, "error": f"file too large (max {RAG_MAX_FILE_SIZE}MB)"}

    try:
        # sync_upload_impl 现在是 async def, 不需要 threadpool 包装
        # (file.read() 是 async, 必须在 async context 调, 同步 I/O 也用 sync open/write)
        result = await sync_upload_impl(
            file, request, max_size,
            RAG_TMP_DIR, RAG_UPLOAD_CHUNK_SIZE, RAG_UPLOAD_EMIT_BYTES, RAG_UPLOAD_EMIT_MS,
            kb_id=kb_id, department=department,
        )
    except Exception as e:
        return {"ok": False, "error": f"upload failed: {type(e).__name__}: {e}"}
    if not result.get("ok"):
        return result

    asyncio.create_task(_run_index_background(
        result["upload_id"], result["filepath"], result["filename"],
        result["source"], result["batch_id"], kb_id=kb_id, department=department,
        upload_elapsed_ms=result.get("upload_elapsed_ms"),
        was_overwrite=result.get("was_overwrite", False),  # P0-X
    ))
    return {"ok": True, "upload_id": result["upload_id"], "filename": result["filename"]}




async def sync_upload_impl(
    file, request, max_size, tmp_dir, chunk_size, emit_bytes, emit_ms,
    kb_id: str = "policy_general", department: str = "general",
) -> dict:
    """P0-1 流式上传 (async def, 直接在 upload_document 事件循环里跑).
    file.read() 是 async method, 必须 await. 写文件是 sync (open + write).
    """
    from backend.config.database import DOCS_DIRECTORY as _DOCS_DIRECTORY

    # _progress_queues / _extract_source 现为模块级显式导入，直接引用即可
    # （原先经 globals() 取值是为了绕开 import * 的名字丢失问题）

    safe_name = os.path.basename(file.filename or "")
    # 修复中文文件名乱码：尝试多种编码回编解码
    if safe_name:
        for enc in ('latin-1', 'cp1252', 'iso-8859-1'):
            try:
                raw = safe_name.encode(enc)
                candidate = raw.decode('utf-8')
                # 成功解码且包含中文字符 → 采纳
                if any('一' <= c <= '鿿' for c in candidate):
                    safe_name = candidate
                    break
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
    if not safe_name or safe_name.startswith("."):
        return {"ok": False, "error": "invalid filename"}
    # P1 fix: 用 realpath 解析 backend/data junction 符号链接
    # 避免同一物理文件被存为两条不同 file_path 记录（SQLite 主键冲突 → 重复 doc_id）
    abs_docs_dir = os.path.realpath(_DOCS_DIRECTORY)
    final_dir = os.path.abspath(os.path.join(abs_docs_dir, kb_id, department))
    final_path = os.path.normpath(os.path.join(final_dir, safe_name))
    # 末尾再 realpath 一次（防御 abspath 残留符号链接组件）
    final_path = os.path.realpath(final_path)
    try:
        if os.path.commonpath([abs_docs_dir, final_path]) != abs_docs_dir:
            return {"ok": False, "error": "invalid path"}
    except ValueError:
        return {"ok": False, "error": "invalid path"}

    # F9: was_overwrite 检测移到 os.replace 前一刻（原在函数入口，与 replace
    # 隔了整段上传+校验流程，TOCTOU 窗口内并发同名上传可基于陈旧状态决策
    # cleanup 策略）。贴近 replace 后窗口缩到备份+replace 两条语句。

    ext = safe_name.rsplit(".", 1)[-1].lower()
    upload_id = uuid.uuid4().hex[:12]
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = f"{tmp_dir}/{upload_id}.{ext}"

    # 真实 HTTP 上传耗时（POST body 接收 + 写临时文件 + atomic rename）
    # 让前端"上传文件"阶段显示准确值，而非被减法逻辑吞掉为 0
    _upload_t0_sync = time.time()

    # F11: 过期队列清理抽为独立函数（定时 GC 复用同一逻辑）
    cleanup_expired_progress_queues()

    queue: Queue = Queue()
    queue._created_at = time.time()
    _progress_queues[upload_id] = queue

    # 进度推送: 在 async context 直接 queue.put_nowait (因为是 asyncio.Queue, 跨 coroutine 同一 loop OK)
    def _safe_put(evt):
        queue.put_nowait(evt)

    try:
        total = 0
        last_emit_bytes = 0
        last_emit_time = time.time()
        cl_str = request.headers.get("content-length", "0")
        cl_int = int(cl_str) if cl_str.isdigit() else None

        with open(tmp_path, "wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    # F1 修复:超限拒绝必须清理临时文件。
                    # 旧实现在 with 块内直接 return,文件句柄关了但文件留在磁盘,
                    # 反复上传大文件会持续泄漏 tmp_dir。
                    f.close()
                    try:
                        os.unlink(tmp_path)
                    except OSError as cleanup_error:
                        logger.warning(f"[RAG] 超限上传临时文件清理失败: {cleanup_error}")
                    return {"ok": False, "error": f"file too large (max {max_size//1024//1024}MB, uploaded {total} bytes)"}
                f.write(chunk)

                now_emit = time.time()
                if total - last_emit_bytes >= emit_bytes or (now_emit - last_emit_time) * 1000 >= emit_ms:
                    progress = int(total * 100 / max(cl_int or total, 1))
                    _safe_put({
                        "stage": "uploading",
                        "progress": min(progress, 99),
                        "bytes": total,
                    })
                    last_emit_bytes = total
                    last_emit_time = now_emit

        os.makedirs(final_dir, exist_ok=True)
        # P1-8: 入口校验 — 空文件与损坏文件（魔数）直接拒绝，避免后台索引阶段才失败
        if total == 0:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return {"ok": False, "error": "file is empty"}
        with open(tmp_path, "rb") as _f:
            head = _f.read(8192)
        _magic_ok = True
        if ext == "pdf" and not head.startswith(b"%PDF-"):
            _magic_ok = False
        elif ext in ("docx", "xlsx") and not head.startswith(b"PK\x03\x04"):
            # F6: xlsx 同为 OOXML zip 容器，魔数与 docx 一致
            _magic_ok = False
        elif ext in ("md", "markdown", "txt") and b"\x00" in head:
            # P2: 文本格式无魔数可查,用 NUL 字节探测拒绝二进制伪装。
            # 历史教训:P0-1 二进制/乱码文件曾直接进入 doc_db。
            _magic_ok = False
        if not _magic_ok:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return {"ok": False, "error": f"file is corrupted or not a valid .{ext} file (magic check failed)"}
        # F9: 覆盖检测紧贴 replace — P0-X: 覆盖场景下 _cleanup_failed_upload
        # 必须保留源文件（atomic rename 已覆盖，删除会丢用户原文件）。
        was_overwrite = os.path.isfile(final_path)
        # P2: 覆盖场景先把旧版本备份到 .bak — 索引成功由后台任务清理,
        # 索引失败时旧内容仍可人工恢复(旧实现 os.replace 后旧版本不可逆丢失)。
        if was_overwrite:
            try:
                shutil.copy2(final_path, final_path + ".bak")
            except OSError as bak_err:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return {"ok": False, "error": f"备份旧版本失败,中止覆盖: {bak_err}"}
        os.replace(tmp_path, final_path)
        _safe_put({"stage": "uploading", "progress": 100, "bytes": total})

        return {
            "ok": True,
            "upload_id": upload_id,
            "filepath": final_path,
            "filename": safe_name,
            "size": total,
            "source": _extract_source(request),
            "batch_id": request.headers.get("X-Batch-Id") or None,
            "upload_elapsed_ms": int((time.time() - _upload_t0_sync) * 1000),
            "was_overwrite": was_overwrite,  # P0-X: 传给 _run_index_background 决定 cleanup 策略
        }
    except Exception as e:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError as cleanup_error:
                logger.warning(f"[RAG] 临时文件清理失败: {cleanup_error}")
        return {"ok": False, "error": f"upload failed: {type(e).__name__}: {e}"}


async def _finalize_upload_queue(upload_id: str) -> None:
    """P1-3：向队列放 None 哨兵（让 SSE 流结束）+ 主动从 _progress_queues 弹出。

    三处分支（失败 / duplicate / 成功）的统一收尾。
    之前的实现只在 SSE 断连时 pop（line 608）,如果客户端没订阅 SSE,队列残留,
    后台索引完成后 queue 里堆积的事件 + queue 字典条目都泄漏。
    现在后台任务自己负责清理,双重 pop 幂等安全。
    """
    queue = _progress_queues.get(upload_id)
    if queue is not None:
        try:
            await queue.put(None)
        except Exception as put_err:
            logger.warning(f"[RAG] queue.put(None) 失败 ({upload_id}): {put_err}")
    # 主动 pop — 如果 SSE 已经断连并 pop 过,这里 pop 返回 None,无副作用
    _progress_queues.pop(upload_id, None)


async def _cleanup_failed_upload(filepath: str, was_overwrite: bool = False) -> None:
    """索引失败后删除已落盘文件，避免孤儿文档被后续扫描重新索引。

    Args:
        filepath: 上传后落盘的目标路径。
        was_overwrite: True 表示这次上传是覆盖现有同名文件,P0-X:不删原文件
            (因为原文件可能正是用户宝贵的生产数据,且已被 atomic rename 覆盖,
            删除会让用户失去旧版本)。False(新上传副本)才安全删除。

    P0-X 修复:
      旧实现无条件 os.remove(filepath),当用户上传同名文件覆盖源文件时,
      sync 失败会物理删除源文件,造成不可逆数据丢失。
    """
    if not filepath:
        return
    if was_overwrite:
        logger.warning(
            f"[RAG] 跳过清理: {filepath} 是覆盖场景,源文件不删 "
            f"(索引失败但保留文件供排查/重试)"
        )
        return
    try:
        if os.path.isfile(filepath):
            os.remove(filepath)
            logger.info(f"[RAG] 已清理索引失败文件: {filepath}")
    except OSError as exc:
        logger.warning(f"[RAG] 索引失败文件清理失败 {filepath}: {exc}")


async def _run_index_background(upload_id: str, filepath: str, filename: str, source: str = "", batch_id: str | None = None, kb_id: str = "policy_general", department: str = "general", upload_elapsed_ms: int | None = None, was_overwrite: bool = False):
    """后台执行索引，向 queue 推送阶段事件；完成后记录操作日志。

    upload_elapsed_ms: sync_upload_impl 实测的 HTTP 上传耗时（POST + 写文件 + atomic rename）。
    终态用此值填 stage_elapsed["uploading"]，避免被减法逻辑吞掉为 0。
    total_ms 改为 upload_elapsed_ms + 后台索引耗时（端到端总耗时）。

    was_overwrite: P0-X 上传是否覆盖了已有同名文件。True 时 cleanup 不能删源文件。
    """
    queue = _progress_queues.get(upload_id)
    if queue is None:
        await _cleanup_failed_upload(filepath, was_overwrite=was_overwrite)
        return

    async def emit(stage: str, message: str = "", **extra):
        await queue.put({"stage": stage, "message": message, **extra})

    _upload_t0 = time.time()
    result = None
    try:
        await emit("uploading", f"文件 {filename} 已保存，开始索引")

        # 同步索引（在线程池跑，不阻塞事件循环）；返回含 trace_id
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _do_index_sync, upload_id, filepath, filename, loop, kb_id, department)
    except Exception as e:
        logger.error(f"[RAG] 后台索引失败: {e}")
        # P1-4:ChunkingEmptyError 是业务失败(扫描件/结构损坏),保留源文件供排查;
        #      其它异常按孤儿文件处理逻辑清理
        from backend.rag.indexing.indexer import ChunkingEmptyError
        if isinstance(e, ChunkingEmptyError):
            await emit("error", f"索引失败:{e}（源文件已保留,请检查文档内容或解析器兼容性）",
                       error_type="chunking_empty", recoverable=True)
            _safe_log_op("", filename, "upload", source, trace_id=None, batch_id=batch_id,
                         result="failed", duration_ms=int((time.time() - _upload_t0) * 1000) + (upload_elapsed_ms or 0),
                         detail={"error": str(e)[:200], "error_type": "chunking_empty"})
        else:
            await _cleanup_failed_upload(filepath, was_overwrite=was_overwrite)
            await emit("error", str(e))
            _safe_log_op("", filename, "upload", source, trace_id=None, batch_id=batch_id,
                         result="failed", duration_ms=int((time.time() - _upload_t0) * 1000) + (upload_elapsed_ms or 0),
                         detail={"error": str(e)[:200]})
        await _finalize_upload_queue(upload_id)
        return

    terminal = (result or {}).get("terminal", "done")
    # span_id → 前端 stage 键的统一映射（终态阶段耗时使用同一规则）
    _SPAN_STAGE_KEY = ProgressListener.SPAN_STAGE_KEY
    if terminal == "duplicate":
        duplicate_doc = (result or {}).get("doc") or {}
        stage_elapsed = (result or {}).get("stage_elapsed") or {}
        # 用真实上传耗时覆盖（duplicate 跳过索引，后端算的 uploading 没意义）
        if upload_elapsed_ms is not None:
            stage_elapsed["uploading"] = upload_elapsed_ms
        total_ms = (upload_elapsed_ms or 0) + int((time.time() - _upload_t0) * 1000)
        await emit("duplicate", "文件已存在，未重复索引",
                   doc=duplicate_doc, trace_id="", stage_elapsed=stage_elapsed, total_ms=total_ms)
        _remove_bak(filepath)  # 内容未变,旧版本备份无保留价值
        await _finalize_upload_queue(upload_id)
        _safe_log_op(
            duplicate_doc.get("doc_id", ""), filename, "upload", source,
            trace_id="", batch_id=batch_id, result="duplicate",
            duration_ms=total_ms,
            detail={"duplicate": True, "chunk_count": duplicate_doc.get("chunk_count", 0)},
        )
        return

    # 成功终态：按 path 直接拿刚索引的文档
    new_doc = None
    try:
        reg = _get_registry()
        new_doc = reg.get_by_path(filepath)
        # 终态携带完整阶段耗时（来自后端 span duration_ms），覆盖前端累加
        raw_elapsed = (result or {}).get("stage_elapsed") or {}
        # span_id 转为前端 stage 键（如 index_chunk → chunking）
        stage_elapsed: dict[str, int] = {}
        for sid, ms in raw_elapsed.items():
            stage_key = _SPAN_STAGE_KEY.get(sid, sid)
            stage_elapsed[stage_key] = int(ms)
        # 优先用 sync_upload_impl 实测的上传耗时；缺失时回退到减法逻辑（向后兼容）
        index_elapsed_ms = int((time.time() - _upload_t0) * 1000)
        total_ms = index_elapsed_ms + (upload_elapsed_ms or 0)
        if upload_elapsed_ms is not None:
            stage_elapsed["uploading"] = upload_elapsed_ms
        elif "uploading" not in stage_elapsed:
            others = sum(v for k, v in stage_elapsed.items() if k != "uploading")
            stage_elapsed["uploading"] = max(total_ms - others, 0)
        await emit("done", "索引完成", doc=new_doc,
                   trace_id=(result or {}).get("trace_id") or "",
                   stage_elapsed=stage_elapsed,
                   total_ms=total_ms)
        _remove_bak(filepath)  # 新版本已确认入库,清理覆盖备份
    except Exception as e:
        await emit("done", "索引完成（文档信息获取失败）")
        logger.warning(f"[RAG] 获取入库文档信息失败: {e}")
    finally:
        await _finalize_upload_queue(upload_id)

    _safe_log_op(
        (new_doc or {}).get("doc_id", ""), filename, "upload", source,
        trace_id=(result or {}).get("trace_id") or None,
        batch_id=batch_id, result="success",
        duration_ms=int((time.time() - _upload_t0) * 1000),
        detail={
            "chunk_count": (result or {}).get("chunk_count", 0),
            "file_hash": (result or {}).get("file_hash", ""),
            "duplicate": False,
            "doc_type": (new_doc or {}).get("doc_type", "general"),
            "llm_used": bool((new_doc or {}).get("llm_used", False)),
            "confidence": (new_doc or {}).get("confidence", 0),
        },
    )


def _remove_bak(filepath: str) -> None:
    """索引成功终态清理覆盖上传的 .bak 备份。

    失败分支不调用 — 索引失败时 .bak 保留,旧版本可人工恢复。
    """
    bak = filepath + ".bak"
    try:
        if os.path.isfile(bak):
            os.remove(bak)
    except OSError as exc:
        logger.warning(f"[RAG] .bak 清理失败 {bak}: {exc}")


def _do_index_sync(upload_id: str, filepath: str, filename: str, main_loop: asyncio.AbstractEventLoop, kb_id: str = "policy_general", department: str = "general"):
    """同步执行索引，通过 _progress_queues[upload_id] 推送阶段（从线程内调用）。

    P1 改造：
      - 不再调 indexer.sync()（全盘扫描，会把已软删但原文件还在的文档判为 ADDED 重新索引→删除复活）。
        改用 reindex_file() 单文件索引。duplicate 检测保留（reindex_file 不做 hash 比对）。
      - 复用 pipeline 已加载的 embedding/vectordb/doc_db，避免每次上传重新加载 bge 模型。
      - doc_db 用 pipeline.doc_db（DOC_DB_PATH），修复原误用同一个 store 导致 doc 全文写进 chunk 库。
      - P2: SHA256 只算一次,透传给 reindex_file（旧实现 duplicate 检测算一次、
        _index_file 内部再算一次,50MB 文件多一次全盘读）。
    """
    from backend.config import DOCS_DIRECTORY  # noqa: F401

    # P1 修复:队列消失(SSE 断连被 pop)不再静默跳过索引 —
    # 旧实现这里直接 return None,调用方会把未索引的文件记为 success(chunk_count=0)。
    # 进度队列只是可选项,emit 在无队列时退化为 no-op,索引本身必须继续。
    queue = _progress_queues.get(upload_id)

    # 同步索引开始时刻：用于 SSE uploading 阶段真实耗时；duplicate 也带上
    _upload_t0_sync = time.time()

    def sync_emit(stage: str, message: str = "", **extra):
        """从同步线程调用：run_coroutine_threadsafe 把事件投到主 async loop 的队列"""
        if queue is None:
            return  # 无订阅者 → 只跳进度推送,不影响索引
        evt = {"stage": stage, "message": message, **extra}
        # 主 loop 在另一个线程，必须用 run_coroutine_threadsafe（不能 asyncio.get_event_loop()）
        asyncio.run_coroutine_threadsafe(queue.put(evt), main_loop)

    # P1-2:同文件并发上传加文件锁,避免 race condition
    # 两个并发请求可能都通过下面的 duplicate 检测,然后都走到 _index_file,
    # 导致同 doc_id 写两次到向量库。这里在入口加非阻塞锁,
    # 第二个请求立即抛 FileLockedByOtherError(不让它阻塞 SSE)。
    index_lock_fd = acquire_index_lock(filepath)
    try:
        # === 原 _do_index_locked_body 内容内联到这里 ——
        # 必须在本函数作用域内,否则 DOCS_DIRECTORY 找不到
        reg = _get_registry()

        existing = reg.get_by_path(filepath)
        try:
            _file_size = os.path.getsize(filepath)
        except OSError:
            _file_size = 0
        # P2: 哈希只算一次,duplicate 检测与 reindex_file 共用
        file_hash = sha256_of_file(filepath)
        if existing and existing.get("status") == "active":
            if existing.get("file_hash") == file_hash:
                logger.info(f"[RAG] 文件未变化，跳过索引: {filename}")
                return {
                    "trace_id": "",
                    "duplicate": True,
                    "terminal": "duplicate",
                    "doc": {**existing, "duplicate": True},
                    "stage_elapsed": {"uploading": int((time.time() - _upload_t0_sync) * 1000)},
                }
            else:
                logger.info(f"[RAG] 文件已变化，重新索引: {filename} (old={existing.get('file_hash','')[:12]} new={file_hash[:12]})")
        elif existing:
            logger.info(f"[RAG] 文件状态非 active ({existing.get('status')})，重新索引: {filename}")
        else:
            logger.info(f"[RAG] 新文件，首次索引: {filename} (path={filepath})")

        sync_emit("uploading", "正在初始化索引管道（首次 ~15s）...")
        _pipe_t0 = time.time()
        pipeline = get_rag_pipeline()
        _pipe_elapsed = int((time.time() - _pipe_t0) * 1000)
        if _pipe_elapsed > 3000:
            logger.info(f"[RAG] 管道初始化耗时 {_pipe_elapsed}ms（可能预热未完成）")
        listener = ProgressListener(sync_emit)
        # F7: 惰性导入（模块顶层已移除该重依赖导入）
        from backend.rag.indexing.indexer import IncrementalIndexer
        indexer = IncrementalIndexer(
            docs_dir=DOCS_DIRECTORY,
            vectordb=pipeline.vectordb,
            doc_db=pipeline.doc_db,
            embedding=pipeline.embedding,
            registry=reg,
            kb_id=kb_id,
            department=department,
            bm25_store=pipeline.bm25_store,  # P0-1: 上传后立即同步 BM25
        )
        try:
            result = indexer.reindex_file(filepath, file_hash=file_hash)
        finally:
            listener.unsub()  # 索引失败也必须退订，防 trace 订阅泄漏
        logger.info(f"[RAG] 上传索引完成: {filename} → {result}")
        return result
    finally:
        release_index_lock(index_lock_fd, filepath)




@router.get("/upload/{upload_id}/stream")
async def stream_upload_progress(upload_id: str):
    """SSE 订阅：实时推送上传 + 索引进度。

    事件类型：
      stage  → {stage: uploading|parsing|chunking|embedding|writing|done|error, message}
      done   → 包含 doc 信息
      error  → 索引失败
    """
    queue = _progress_queues.get(upload_id)
    if queue is None:
        async def not_found():
            yield _sse_encode("error", {"message": f"upload_id {upload_id} 不存在或已过期"})
        return StreamingResponse(not_found(), media_type="text/event-stream")

    async def event_stream():
        try:
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    # P2 心跳:大文件索引期间可能数分钟无事件,
                    # 无心跳的 SSE 长连接容易被代理/网关断开
                    yield ": keepalive\n\n"
                    continue
                if evt is None:
                    break
                # 关键：保留 stage 字段在 data 中 — 前端 onmessage 解析 payload.stage
                # _sse_encode 的 event 参数（stage）不再用作 SSE event name（无 event: 字段）
                yield _sse_encode("message", evt)
        finally:
            # SSE 断开 → 清理队列（防内存泄漏）
            _progress_queues.pop(upload_id, None)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

