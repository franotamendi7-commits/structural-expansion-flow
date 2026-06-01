"""
Sistema de logging profesional con rotación diaria.
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime

LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

def get_logger(name, filename):
    """Devuelve un logger que escribe en logs/<filename> con rotación."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    # Evitar duplicados si ya existe
    if logger.handlers:
        return logger
    # Formato
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    # Archivo rotativo (10 MB por archivo, 5 backups)
    handler = RotatingFileHandler(
        os.path.join(LOG_DIR, filename),
        maxBytes=10*1024*1024,
        backupCount=5
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    # También consola para debug
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    return logger

# Cuatro loggers especializados
market_logger = get_logger('market', 'market.log')
signals_logger = get_logger('signals', 'signals.log')
trades_logger = get_logger('trades', 'trades.log')
errors_logger = get_logger('errors', 'errors.log')