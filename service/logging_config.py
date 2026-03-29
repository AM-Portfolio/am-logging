import logging
import json
import sys
from datetime import datetime

class JSONFormatter(logging.Formatter):
    """
    JSON Formatter for AM Logging Service
    Produces structured logs consistent with AM ecosystem standards
    """
    def __init__(self, service_name: str = "am-logging-service"):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self.service_name,
            "module": getattr(record, 'module', ''),
            "function": getattr(record, 'funcName', ''),
            "line": getattr(record, 'lineno', ''),
            "filename": getattr(record, 'filename', ''),
            "path": getattr(record, 'pathname', ''),
            "thread": getattr(record, 'thread', ''),
            "process": getattr(record, 'process', ''),
        }

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info)
            }

        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in ["name", "msg", "args", "levelname", "levelno", "pathname", 
                           "filename", "module", "lineno", "funcName", "created", 
                           "msecs", "relativeCreated", "thread", "threadName", 
                           "processName", "process", "message", "exc_info", "exc_text", 
                           "stack_info"]:
                 # Try to serialize, skip if fails or just stringify
                try:
                    json.dumps(value)
                    log_entry[key] = value
                except (TypeError, ValueError):
                    log_entry[key] = str(value)

        return json.dumps(log_entry, default=str)

def setup_logging(service_name: str = "am-logging-service", level: str = "INFO"):
    """
    Setup root logger with JSON formatter
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    root_logger.handlers = []
    
    # Console Handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter(service_name))
    root_logger.addHandler(handler)
    
    # Configure Uvicorn loggers to use our handler/formatter via propagation
    for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
        log = logging.getLogger(logger_name)
        log.handlers = []
        log.propagate = True
    
    return logging.getLogger(service_name)
