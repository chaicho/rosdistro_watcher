import logging
import os
import sys
from datetime import datetime

# Global logger instance
_logger = None
_log_file_path = None

def get_logger():
    """Get the global logger instance."""
    global _logger
    if _logger is None:
        _logger = setup_logger()
    return _logger

def setup_logger(name='rosdep_auditor', level=logging.INFO, console_output=True, file_output=True):
    """
    Set up a global logger with both console and file handlers.
    
    Args:
        name: Logger name
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        console_output: Whether to output to console
        file_output: Whether to output to file
    
    Returns:
        Logger instance
    """
    global _log_file_path
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Clear any existing handlers to avoid duplicates
    logger.handlers.clear()
    
    # Create formatter
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # File handler
    if file_output:
        if _log_file_path is None:
            # Create logs directory if it doesn't exist
            logs_dir = 'logs'
            if not os.path.exists(logs_dir):
                os.makedirs(logs_dir)
            
            # Generate timestamped log filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            _log_file_path = os.path.join(logs_dir, f'{name}_{timestamp}.log')
        
        file_handler = logging.FileHandler(_log_file_path, encoding='utf-8')
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger

def set_log_level(level):
    """Set the logging level for the global logger."""
    logger = get_logger()
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)

def get_log_file_path():
    """Get the current log file path."""
    return _log_file_path

# Convenience functions for different log levels
def debug(message):
    """Log a debug message."""
    logger = get_logger()
    if logger.isEnabledFor(logging.DEBUG):
        logger._log(logging.DEBUG, message, (), stacklevel=2)

def info(message):
    """Log an info message."""
    logger = get_logger()
    if logger.isEnabledFor(logging.INFO):
        logger._log(logging.INFO, message, (), stacklevel=2)

def warning(message):
    """Log a warning message."""
    logger = get_logger()
    if logger.isEnabledFor(logging.WARNING):
        logger._log(logging.WARNING, message, (), stacklevel=2)

def error(message):
    """Log an error message."""
    logger = get_logger()
    if logger.isEnabledFor(logging.ERROR):
        logger._log(logging.ERROR, message, (), stacklevel=2)

def critical(message):
    """Log a critical message."""
    logger = get_logger()
    if logger.isEnabledFor(logging.CRITICAL):
        logger._log(logging.CRITICAL, message, (), stacklevel=2)

# Legacy function compatibility
def log_to_file(message, log_file=None):
    """
    Legacy function for compatibility.
    Logs message as INFO level to maintain backward compatibility.
    """
    logger = get_logger()
    if logger.isEnabledFor(logging.INFO):
        logger._log(logging.INFO, message, (), stacklevel=2) 