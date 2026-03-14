import os
import shutil
import sys
from generator.generate import generate_libraries

def sync_to_auth():
    """Sync regenerated SDK files to am-auth repository"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repos_root = os.path.dirname(base_dir)
    am_auth_root = os.path.join(repos_root, "am-auth")
    
    if not os.path.exists(am_auth_root):
        print(f"Error: am-auth repository not found at {am_auth_root}")
        return False
        
    sync_tasks = [
        {
            "src": os.path.join(base_dir, "libraries", "python", "am-logging-sdk", "am_logging_client.py"),
            "dest": os.path.join(am_auth_root, "am", "shared", "logging", "am_logging_sdk", "am_logging_client.py")
        },
        {
            "src": os.path.join(base_dir, "libraries", "python", "am-logging-py", "am_logging", "core.py"),
            "dest": os.path.join(am_auth_root, "am", "shared", "logging", "am_logger_core.py")
        }
    ]
    
    for task in sync_tasks:
        os.makedirs(os.path.dirname(task["dest"]), exist_ok=True)
        shutil.copy2(task["src"], task["dest"])
        print(f"Synced {os.path.basename(task['src'])} -> {task['dest']}")
    
    return True

def main():
    print("--- Starting SDK Generation ---")
    generate_libraries()
    
    print("\n--- Starting SDK Sync to am-auth ---")
    if sync_to_auth():
        print("\n✅ Generation and Sync completed successfully.")
    else:
        print("\n❌ Sync failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
