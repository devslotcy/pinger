"""
Pinger v2.0 - Merkezi Logging Modülü
Tüm modüller bu logger'ı kullanır.
"""

import logging
import logging.handlers
import os
import yaml
from pathlib import Path


def setup_logger(name: str, config_path: str = "config.yaml") -> logging.Logger:
    """
    Modül için yapılandırılmış logger döndürür.

    Args:
        name: Logger adı (genellikle __name__)
        config_path: config.yaml dosya yolu

    Returns:
        Yapılandırılmış logging.Logger
    """
    # Config yükle
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        log_cfg = config.get("logging", {})
    except FileNotFoundError:
        log_cfg = {}

    level_str = log_cfg.get("level", "INFO")
    log_file = log_cfg.get("file", "logs/pinger.log")
    max_bytes = log_cfg.get("max_bytes", 10_485_760)
    backup_count = log_cfg.get("backup_count", 5)
    fmt = log_cfg.get("format", "%(asctime)s | %(name)s | %(levelname)s | %(message)s")

    level = getattr(logging, level_str.upper(), logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Çift handler eklemeyi önle
    if logger.handlers:
        return logger

    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Dosya handler (rotating)
    log_dir = Path(log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Mevcut logger'ı döndürür (zaten init edilmişse)."""
    return logging.getLogger(name)
