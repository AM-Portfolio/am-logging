import os
import yaml
import httpx
import asyncio
from jinja2 import Template

def generate_sdk_from_openapi():
    """Generate Python SDK from OpenAPI spec"""
    # Use paths relative to the project root
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    openapi_spec_path = os.path.join(base_dir, "docs", "logging", "logging_api_spec.yaml")
    sdk_output_path = os.path.join(base_dir, "libraries", "python", "am-logging-sdk", "am_logging_client.py")
    
    with open(openapi_spec_path, "r") as f:
        spec = yaml.safe_load(f)
    
    sdk_code = f'''# DO NOT EDIT: THIS FILE IS AUTO-GENERATED FROM OPENAPI SPEC
"""
Auto-generated Python SDK for AM Logging API
Generated from OpenAPI spec version {spec['info']['version']}
"""

import httpx
import asyncio
import os
from typing import Dict, Any, Optional, List
from datetime import datetime

class AMLoggingClient:
    """Fire-and-forget logging client for AM Logging Service"""
    
    def __init__(self, base_url: str = "http://am-logging-svc/v1", timeout: float = 2.0, persist_to_db: Optional[bool] = None):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        # Default to environment variable AM_LOGGING_PERSIST_TO_DB or False
        if persist_to_db is None:
            self.persist_to_db = os.getenv("AM_LOGGING_PERSIST_TO_DB", "False").lower() == "true"
        else:
            self.persist_to_db = persist_to_db
        
    async def _send_log_async(self, log_entry: Dict[str, Any]) -> bool:
        """Send log asynchronously - fire and forget"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await client.post(f"{{self.base_url}}/logs", json=log_entry)
                return True
        except Exception:
            # Silent fail for fire-and-forget
            return False
    
    def send_log(self, log_entry: Dict[str, Any]) -> None:
        """Fire-and-forget log sending - non-blocking"""
        if self._validate_log_entry(log_entry):
            asyncio.create_task(self._send_log_async(log_entry))
    
    def _validate_log_entry(self, log_entry: Dict[str, Any]) -> bool:
        """Validate log entry against OpenAPI schema"""
        required_fields = set({spec['components']['schemas']['LogEntry']['required']})
        return required_fields.issubset(set(log_entry.keys()))
    
    def create_log_entry(
        self,
        trace_id: str,
        span_id: str,
        service: str,
        level: str,
        payload: Dict[str, Any],
        log_type: str = "TECHNICAL",
        context: Optional[Dict[str, Any]] = None,
        exception: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, str]] = None,
        persist_to_db: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Create a properly formatted log entry"""
        metadata = metadata or {{}}
        if persist_to_db is not None:
            metadata["persist_to_db"] = str(persist_to_db).lower()
        elif self.persist_to_db is not None:
            metadata["persist_to_db"] = str(self.persist_to_db).lower()

        return {{
            "trace_id": trace_id,
            "span_id": span_id,
            "service": service,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "log_type": log_type,
            "level": level,
            "payload": payload,
            "context": context or {{}},
            "exception": exception,
            "metadata": metadata or {{}}
        }}

# Convenience functions for different log levels
class LoggerMixin:
    """Mixin class to add logging capabilities to any class"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._log_client = AMLoggingClient()
        self._service_name = getattr(self, 'service_name', self.__class__.__module__.split('.')[0])
    
    def _log_async(self, level: str, message: str, **kwargs):
        """Async logging method"""
        import uuid
        import inspect
        
        trace_id = kwargs.get('trace_id', str(uuid.uuid4()))
        span_id = kwargs.get('span_id', str(uuid.uuid4()))
        
        frame = inspect.currentframe().f_back
        class_name = frame.f_locals.get('self', None).__class__.__name__ if 'self' in frame.f_locals else "Global"
        method_name = frame.f_code.co_name
        
        log_entry = self._log_client.create_log_entry(
            trace_id=trace_id,
            span_id=span_id,
            service=self._service_name,
            level=level,
            payload={{"message": message}},
            context={{"class": class_name, "method": method_name}},
            metadata=kwargs.get('metadata'),
            persist_to_db=kwargs.get('persist_to_db')
        )
        
        self._log_client.send_log(log_entry)
    
    def log_info(self, message: str, **kwargs):
        self._log_async("INFO", message, **kwargs)
    
    def log_error(self, message: str, **kwargs):
        self._log_async("ERROR", message, **kwargs)
    
    def log_debug(self, message: str, **kwargs):
        self._log_async("DEBUG", message, **kwargs)
    
    def log_warn(self, message: str, **kwargs):
        self._log_async("WARN", message, **kwargs)
    
    def log_critical(self, message: str, **kwargs):
        self._log_async("CRITICAL", message, **kwargs)
'''
    
    os.makedirs(os.path.dirname(sdk_output_path), exist_ok=True)
    with open(sdk_output_path, "w") as f:
        f.write(sdk_code)
    print(f"Generated Python SDK from OpenAPI at {{sdk_output_path}}")
    return sdk_output_path

def generate_libraries():
    # Use paths relative to the project root
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pattern_file = os.path.join(base_dir, "docs", "logging", "pattern_definition.yaml")
    
    with open(pattern_file, "r") as f:
        pattern_config = yaml.safe_load(f)
    
    output_format = pattern_config['output_format']
    
    # Generate SDK from OpenAPI first
    sdk_path = generate_sdk_from_openapi()
    
    # 1. Generate Python
    py_template_path = os.path.join(base_dir, "generator", "python_logger.py.j2")
    py_output_path = os.path.join(base_dir, "libraries", "python", "am-logging-py", "am_logging", "core.py")
    
    with open(py_template_path, "r") as f:
        py_template = Template(f.read())
    
    py_code = py_template.render(output_format=output_format)
    os.makedirs(os.path.dirname(py_output_path), exist_ok=True)
    with open(py_output_path, "w") as f:
        f.write(py_code)
    print(f"Generated Python AMLogger at {{py_output_path}}")

    # 2. Generate Java
    java_template_path = os.path.join(base_dir, "generator", "java_logger.java.j2")
    java_output_path = os.path.join(base_dir, "libraries", "java", "am-logging-java", "src", "main", "java", "com", "am", "logging", "AMLogger.java")
    
    with open(java_template_path, "r") as f:
        java_template = Template(f.read())
    
    java_code = java_template.render(output_format=output_format)
    os.makedirs(os.path.dirname(java_output_path), exist_ok=True)
    with open(java_output_path, "w") as f:
        f.write(java_code)
    print(f"Generated Java AMLogger at {{java_output_path}}")

    # 3. Generate Dart/Flutter
    dart_template_path = os.path.join(base_dir, "generator", "dart_logger.dart.j2")
    dart_output_path = os.path.join(base_dir, "libraries", "dart", "am-logging-dart", "lib", "am_logging.dart")
    
    with open(dart_template_path, "r") as f:
        dart_template = Template(f.read())
    
    dart_code = dart_template.render(output_format=output_format)
    os.makedirs(os.path.dirname(dart_output_path), exist_ok=True)
    with open(dart_output_path, "w") as f:
        f.write(dart_code)
    print(f"Generated Dart AMLogger at {dart_output_path}")

    print("\\nValidation: Comparing output formats...")
    print(f"Master Pattern: {output_format}")
    # Simple check to ensure the pattern string exists in all files
    for path, lang in [(py_output_path, "Python"), (java_output_path, "Java"), (dart_output_path, "Dart")]:
        with open(path, "r") as f:
            if output_format in f.read(): print(f"{lang} pattern OK")
    
    print(f"\\nGenerated SDK from OpenAPI spec at: {sdk_path}")

if __name__ == "__main__":
    generate_libraries()
