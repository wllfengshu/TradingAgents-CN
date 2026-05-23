"""
应用日志配置：完全基于环境变量（.env）驱动，不再依赖 config/logging.toml。

支持的环境变量（所有都为可选，未设置使用默认值）：

通用：
  LOG_LEVEL                日志根/控制台级别（默认 INFO）
  LOG_DIR                  日志目录（默认 ./logs）
  LOG_CONSOLE_FORMAT       控制台格式串
  LOG_FILE_FORMAT          文件格式串
  LOG_CONSOLE_JSON         控制台是否使用 JSON 格式（true/false，默认 false）
  LOG_FILE_JSON            文件是否使用 JSON 格式（true/false，默认 true）

主日志（tradingagents.log）：
  LOG_MAIN_ENABLED         是否启用（默认 true）
  LOG_MAIN_FILE            文件路径（默认 <LOG_DIR>/tradingagents.log）
  LOG_MAIN_LEVEL           级别（默认 INFO）
  LOG_MAIN_MAX_SIZE        单文件最大尺寸，如 100MB（默认 100MB）
  LOG_MAIN_BACKUP_COUNT    备份数（默认 5）

WebAPI 日志（webapi.log）/ Worker 日志（worker.log）/ 错误日志（error.log）
  同上，前缀分别为 LOG_WEBAPI_ / LOG_WORKER_ / LOG_ERROR_
  默认级别：WEBAPI=DEBUG, WORKER=DEBUG, ERROR=WARNING
"""

import logging
import logging.config
import os
import platform
import sys
from pathlib import Path

from app.core.logging_context import LoggingContextFilter, trace_id_var  # noqa: F401

# 🔥 在 Windows 上使用 concurrent-log-handler 避免文件占用问题
_IS_WINDOWS = platform.system() == "Windows"
if _IS_WINDOWS:
    try:
        from concurrent_log_handler import ConcurrentRotatingFileHandler  # noqa: F401
        _USE_CONCURRENT_HANDLER = True
    except ImportError:
        _USE_CONCURRENT_HANDLER = False
        logging.warning("concurrent-log-handler 未安装，在 Windows 上可能遇到日志轮转问题")
else:
    _USE_CONCURRENT_HANDLER = False


class SimpleJsonFormatter(logging.Formatter):
    """Minimal JSON formatter without external deps."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        obj = {
            "time": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "name": record.name,
            "level": record.levelname,
            "trace_id": getattr(record, "trace_id", "-"),
            "message": record.getMessage(),
        }
        return json.dumps(obj, ensure_ascii=False)


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None or val == "":
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _env_str(key: str, default: str) -> str:
    val = os.environ.get(key)
    return val if val not in (None, "") else default


def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _parse_size(size_str) -> int:
    """解析大小字符串（如 '10MB', '100MB', '1024'）为字节数"""
    if isinstance(size_str, int):
        return size_str
    if not isinstance(size_str, str) or not size_str:
        return 10 * 1024 * 1024
    s = size_str.strip().upper()
    try:
        if s.endswith("KB"):
            return int(float(s[:-2]) * 1024)
        if s.endswith("MB"):
            return int(float(s[:-2]) * 1024 * 1024)
        if s.endswith("GB"):
            return int(float(s[:-2]) * 1024 * 1024 * 1024)
        return int(float(s))
    except Exception:
        return 10 * 1024 * 1024


def get_log_dir() -> str:
    """对外暴露：获取日志目录（供其他模块复用）"""
    return _env_str("LOG_DIR", "./logs")


def setup_logging(log_level: str = "INFO"):
    """基于环境变量配置应用日志（替代旧的 config/logging.toml 方案）。"""

    level = _env_str("LOG_LEVEL", log_level).upper()
    log_dir = get_log_dir()
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    fmt_console = _env_str(
        "LOG_CONSOLE_FORMAT",
        "%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
    )
    fmt_file = _env_str(
        "LOG_FILE_FORMAT",
        "%(asctime)s | %(name)-20s | %(levelname)-8s | %(module)s:%(funcName)s:%(lineno)d | %(message)s",
    )
    if "%(trace_id)" not in fmt_console:
        fmt_console = fmt_console + " trace=%(trace_id)s"
    if "%(trace_id)" not in fmt_file:
        fmt_file = fmt_file + " trace=%(trace_id)s"

    use_json_console = _env_bool("LOG_CONSOLE_JSON", False)
    use_json_file = _env_bool("LOG_FILE_JSON", True)

    def file_cfg(prefix: str, default_name: str, default_level: str,
                 default_size: str = "100MB", default_backup: int = 5):
        return {
            "enabled": _env_bool(f"LOG_{prefix}_ENABLED", True),
            "filename": _env_str(f"LOG_{prefix}_FILE", str(Path(log_dir) / default_name)),
            "level": _env_str(f"LOG_{prefix}_LEVEL", default_level).upper(),
            "max_bytes": _parse_size(_env_str(f"LOG_{prefix}_MAX_SIZE", default_size)),
            "backup_count": _env_int(f"LOG_{prefix}_BACKUP_COUNT", default_backup),
        }

    main_cfg = file_cfg("MAIN", "tradingagents.log", "INFO")
    webapi_cfg = file_cfg("WEBAPI", "webapi.log", "DEBUG")
    worker_cfg = file_cfg("WORKER", "worker.log", "DEBUG")
    error_cfg = file_cfg("ERROR", "error.log", "WARNING", default_size="10MB")

    handler_class = (
        "concurrent_log_handler.ConcurrentRotatingFileHandler"
        if _USE_CONCURRENT_HANDLER
        else "logging.handlers.RotatingFileHandler"
    )

    handlers_config = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json_console_fmt" if use_json_console else "console_fmt",
            "level": level,
            "filters": ["request_context"],
            "stream": sys.stdout,
        },
    }

    def add_file_handler(name: str, cfg: dict, hcls: str = handler_class):
        if not cfg["enabled"]:
            return
        handlers_config[name] = {
            "class": hcls,
            "formatter": "json_file_fmt" if use_json_file else "file_fmt",
            "level": cfg["level"],
            "filename": cfg["filename"],
            "maxBytes": cfg["max_bytes"],
            "backupCount": cfg["backup_count"],
            "encoding": "utf-8",
            "filters": ["request_context"],
        }

    add_file_handler("main_file", main_cfg)
    add_file_handler("file", webapi_cfg)  # 历史命名：webapi 使用 handler 名 "file"
    add_file_handler("worker_file", worker_cfg)
    add_file_handler("error_file", error_cfg, hcls="logging.handlers.RotatingFileHandler")

    main_handlers = ["console"]
    if main_cfg["enabled"]:
        main_handlers.append("main_file")
    if error_cfg["enabled"]:
        main_handlers.append("error_file")

    webapi_handlers = ["console"]
    if webapi_cfg["enabled"]:
        webapi_handlers.append("file")
    if main_cfg["enabled"]:
        webapi_handlers.append("main_file")
    if error_cfg["enabled"]:
        webapi_handlers.append("error_file")

    worker_handlers = ["console"]
    if worker_cfg["enabled"]:
        worker_handlers.append("worker_file")
    if main_cfg["enabled"]:
        worker_handlers.append("main_file")
    if error_cfg["enabled"]:
        worker_handlers.append("error_file")

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_context": {"()": "app.core.logging_context.LoggingContextFilter"}
        },
        "formatters": {
            "console_fmt": {"format": fmt_console, "datefmt": "%Y-%m-%d %H:%M:%S"},
            "file_fmt": {"format": fmt_file, "datefmt": "%Y-%m-%d %H:%M:%S"},
            "json_console_fmt": {"()": "app.core.logging_config.SimpleJsonFormatter"},
            "json_file_fmt": {"()": "app.core.logging_config.SimpleJsonFormatter"},
        },
        "handlers": handlers_config,
        "loggers": {
            "tradingagents": {"level": "INFO", "handlers": main_handlers, "propagate": False},
            "webapi": {"level": "INFO", "handlers": webapi_handlers, "propagate": False},
            "worker": {"level": "DEBUG", "handlers": worker_handlers, "propagate": False},
            "uvicorn": {"level": "INFO", "handlers": webapi_handlers, "propagate": False},
            "fastapi": {"level": "INFO", "handlers": webapi_handlers, "propagate": False},
            "app": {"level": "INFO", "handlers": main_handlers, "propagate": False},
        },
        "root": {"level": level, "handlers": main_handlers},
    }

    try:
        logging.config.dictConfig(logging_config)
        logging.getLogger("webapi").info(
            f"Logging configured from environment (LOG_DIR={log_dir}, level={level})"
        )
    except Exception as e:
        logging.basicConfig(level=level, format=fmt_console)
        logging.getLogger("webapi").warning(
            f"Failed to apply env-based logging config, fell back to basicConfig: {e}"
        )
