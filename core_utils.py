# -*- coding: utf-8 -*-
"""core_utils.py -- 《异键演奏》共享基础设施工具集。

提供：
- ROOT：项目根目录 Path；
- get_logger(name)：延迟初始化的 RotatingFileHandler 日志器（输出至 logs/app.log）；
- sha256_file(path)：流式分块计算文件 sha256；
- heal_portable_venvs(root_dir)：便携双虚拟环境 pyvenv.cfg 路径自愈；
- fit_screen(widget, width, height, min_w, min_h)：按屏幕可用几何安全缩放窗口。
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent

_LOG_LOCK = threading.RLock()
_LOG_HANDLER: RotatingFileHandler | None = None
_LOG_INIT_FAILED = False


def _ensure_log_handler() -> logging.Handler:
    global _LOG_HANDLER, _LOG_INIT_FAILED
    if _LOG_HANDLER is not None:
        return _LOG_HANDLER
    if _LOG_INIT_FAILED:
        return logging.NullHandler()
    with _LOG_LOCK:
        if _LOG_HANDLER is not None:
            return _LOG_HANDLER
        if _LOG_INIT_FAILED:
            return logging.NullHandler()
        try:
            log_dir = ROOT / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "app.log"
            handler = RotatingFileHandler(
                str(log_file),
                maxBytes=2 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
                delay=True,
            )
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            _LOG_HANDLER = handler
            return _LOG_HANDLER
        except Exception:
            _LOG_INIT_FAILED = True
            return logging.NullHandler()


def get_logger(name: str = "app") -> logging.Logger:
    """获取基于 RotatingFileHandler 的延迟初始化日志器（仅写入项目 logs/app.log）。"""
    logger_name = str(name or "app")
    logger = logging.getLogger(logger_name)
    if not logger.handlers:
        handler = _ensure_log_handler()
        with _LOG_LOCK:
            if not logger.handlers:
                logger.setLevel(logging.DEBUG)
                logger.propagate = False
                logger.addHandler(handler)
    return logger


def sha256_file(path: str | os.PathLike, chunk_size: int = 65536) -> str:
    """流式分块计算文件 sha256；失败返回空字符串并记录调试日志。"""
    try:
        h = hashlib.sha256()
        with open(os.fspath(path), "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception as exc:
        get_logger("core_utils").debug("sha256_file failed for %s: %s", path, exc)
        return ""


def heal_portable_venvs(root_dir: str | os.PathLike = ROOT) -> list[str]:
    """把两处 pyvenv.cfg 的 home/executable 指回当前 runtime，随目录移动自动修复。"""
    root = Path(root_dir)
    runtime = root / "runtime" / "python311"
    exe = runtime / "python.exe"
    if not exe.is_file():
        return []
    want_home = os.path.normcase(os.path.abspath(str(runtime)))
    want_exe = os.path.normcase(os.path.abspath(str(exe)))
    fixed: list[str] = []
    for cfg in (root / ".venv" / "pyvenv.cfg",
                root / "vendor" / "sheetsage2" / ".venv" / "pyvenv.cfg"):
        if not cfg.is_file():
            continue
        try:
            lines = cfg.read_text(encoding="utf-8", errors="replace").splitlines()
            changed = False
            for i, line in enumerate(lines):
                key = line.split("=", 1)[0].strip().lower() if "=" in line else ""
                if key in ("home", "executable"):
                    cur = line.split("=", 1)[1].strip()
                    target = want_home if key == "home" else want_exe
                    if os.path.normcase(os.path.abspath(cur)) != target:
                        lines[i] = "%s = %s" % (key, runtime if key == "home" else exe)
                        changed = True
            if changed:
                cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
                fixed.append(str(cfg))
        except Exception as exc:
            get_logger("core_utils").debug("heal_portable_venvs failed for %s: %s", cfg, exc)
    return fixed


def fit_screen(widget, width: int, height: int, min_w: int = 560, min_h: int = 420) -> tuple[int, int]:
    """安全按当前屏幕可用几何尺寸调整窗口大小，返回最终 (w, h)。"""
    avail = None
    try:
        scr = widget.screen() if hasattr(widget, "screen") else None
        if scr is None:
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                scr = app.primaryScreen()
        if scr is not None:
            avail = scr.availableGeometry()
    except Exception as exc:
        get_logger("core_utils").debug("fit_screen geometry query failed: %s", exc)

    w = int(width)
    h = int(height)
    lo_w = int(min_w)
    lo_h = int(min_h)
    if avail is not None and avail.width() > 0 and avail.height() > 0:
        max_w = max(lo_w, int(avail.width() * 0.96))
        max_h = max(lo_h, int(avail.height() * 0.94))
        w = max(lo_w, min(w, max_w))
        h = max(lo_h, min(h, max_h))
    else:
        w = max(lo_w, w)
        h = max(lo_h, h)

    try:
        if hasattr(widget, "resize"):
            widget.resize(w, h)
    except Exception as exc:
        get_logger("core_utils").debug("fit_screen resize failed: %s", exc)
    return w, h
