import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional

def setup_logging(
    log_level: int = logging.INFO,
    log_file: Optional[str] = None,
    console: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
) -> None:
    """
    Configure logging for the eubi-bridge package.

    Args:
        log_level: Logging level (e.g., logging.INFO, logging.DEBUG)
        log_file: Path to log file. If None, file logging is disabled
        console: Whether to log to console
        max_bytes: Maximum log file size before rotation
        backup_count: Number of backup log files to keep
    """
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Get the root logger
    logger = logging.getLogger('eubi_bridge')
    logger.setLevel(log_level)

    # Clear any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Add console handler if requested
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # Add file handler if log file is specified
    if log_file:
        # Create log directory if it doesn't exist
        log_path = Path(log_file).parent
        log_path.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Set up exception hook to log uncaught exceptions
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            # Call default handler for KeyboardInterrupt
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        logger.critical(
            "Uncaught exception",
            exc_info=(exc_type, exc_value, exc_traceback)
        )

    sys.excepthook = handle_exception


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the specified name, scoped under 'eubi_bridge'.
    
    Args:
        name: Name of the logger (usually __name__)
        
    Returns:
        Configured logger instance
    """
    # Remove any existing handlers from the root logger to prevent duplicate logs
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if not isinstance(handler, logging.NullHandler):
            root_logger.removeHandler(handler)
    
    # Return a namespaced logger
    return logging.getLogger(f'eubi_bridge.{name}')
