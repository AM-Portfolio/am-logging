import os
import yaml
from jinja2 import Template

def generate_libraries():
    # Load pattern definition
    pattern_file = "/workspaces/am-repos/am-logging/docs/logging/pattern_definition.yaml"
    with open(pattern_file, "r") as f:
        pattern_config = yaml.safe_load(f)
    
    output_format = pattern_config['output_format']
    
    # 1. Generate Python
    py_template_path = "/workspaces/am-repos/am-logging/generator/python_logger.py.j2"
    py_output_path = "/workspaces/am-repos/am-logging/libraries/python/am-logging-py/am_logging/core.py"
    
    with open(py_template_path, "r") as f:
        py_template = Template(f.read())
    
    py_code = py_template.render(output_format=output_format)
    os.makedirs(os.path.dirname(py_output_path), exist_ok=True)
    with open(py_output_path, "w") as f:
        f.write(py_code)
    print(f"Generated Python AMLogger at {py_output_path}")

    # 2. Generate Java
    java_template_path = "/workspaces/am-repos/am-logging/generator/java_logger.java.j2"
    java_output_path = "/workspaces/am-repos/am-logging/libraries/java/am-logging-java/src/main/java/com/am/logging/AMLogger.java"
    
    with open(java_template_path, "r") as f:
        java_template = Template(f.read())
    
    java_code = java_template.render(output_format=output_format)
    os.makedirs(os.path.dirname(java_output_path), exist_ok=True)
    with open(java_output_path, "w") as f:
        f.write(java_code)
    with open(java_output_path, "w") as f:
        f.write(java_code)
    print(f"Generated Java AMLogger at {java_output_path}")

    # 3. Generate Dart/Flutter
    dart_template_path = "/workspaces/am-repos/am-logging/generator/dart_logger.dart.j2"
    dart_output_path = "/workspaces/am-repos/am-logging/libraries/dart/am-logging-dart/lib/am_logging.dart"
    
    with open(dart_template_path, "r") as f:
        dart_template = Template(f.read())
    
    dart_code = dart_template.render(output_format=output_format)
    os.makedirs(os.path.dirname(dart_output_path), exist_ok=True)
    with open(dart_output_path, "w") as f:
        f.write(dart_code)
    print(f"Generated Dart AMLogger at {dart_output_path}")

    print("\nValidation: Comparing output formats...")
    print(f"Master Pattern: {output_format}")
    # Simple check to ensure the pattern string exists in all files
    for path, lang in [(py_output_path, "Python"), (java_output_path, "Java"), (dart_output_path, "Dart")]:
        with open(path, "r") as f:
            if output_format in f.read(): print(f"{lang} pattern OK")

if __name__ == "__main__":
    generate_libraries()
