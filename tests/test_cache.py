import os
import time
import pytest
import sys
import types

# Pytest installs a shim 'py.py' which breaks implicit namespace packages named 'py'.
# We mock the 'py' package to include both our extension's py/ and the mother project's py/.
node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
adaptive_prompts_dir = os.path.abspath(os.path.join(node_root, "..", "comfyui-adaptiveprompts"))

sys.modules["py"] = types.ModuleType("py")
sys.modules["py"].__path__ = [
    os.path.join(node_root, "py"),
    os.path.join(adaptive_prompts_dir, "py")
]

from py.prompt_stack_loader import PromptStackLoader

def test_is_changed_detects_mtime_changes(tmp_path, monkeypatch):
    import sys
    # Mock node_root inside the module to allow our tmp_path base_dir
    monkeypatch.setattr(sys.modules["py.prompt_stack_loader"], "__file__", str(tmp_path / "py" / "loader.py"))
    
    # Setup temporary prompt file
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    stack_file = prompts_dir / "my_stack.txt"
    stack_file.write_text("Hello World", encoding="utf-8")
    
    # Calculate initial hash
    base_dir = str(prompts_dir)
    stack_filename = "my_stack.txt"
    inline_stack = ""
    
    hash1 = PromptStackLoader.IS_CHANGED(base_dir=base_dir, stack_file=stack_filename, inline_stack=inline_stack)
    
    # Simulate a file edit
    time.sleep(0.01) # Ensure mtime changes slightly if system has high precision
    new_mtime = time.time() + 10.0
    os.utime(stack_file, (new_mtime, new_mtime))
    
    # Calculate new hash
    hash2 = PromptStackLoader.IS_CHANGED(base_dir=base_dir, stack_file=stack_filename, inline_stack=inline_stack)
    
    assert hash1 != hash2, "IS_CHANGED should return a different hash when the underlying file's mtime is modified."

def test_is_changed_recursively_detects_random_directories(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys.modules["py.prompt_stack_loader"], "__file__", str(tmp_path / "py" / "loader.py"))
    
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    random_dir = prompts_dir / "my_random_dir"
    random_dir.mkdir()
    nested_dir = random_dir / "nested"
    nested_dir.mkdir()
    
    file1 = nested_dir / "file1.txt"
    file1.write_text("test")
    
    base_dir = str(prompts_dir)
    inline_stack = "random:my_random_dir"
    
    hash1 = PromptStackLoader.IS_CHANGED(base_dir=base_dir, stack_file="", inline_stack=inline_stack)
    
    import time
    time.sleep(0.01)
    new_mtime = time.time() + 10.0
    os.utime(file1, (new_mtime, new_mtime))
    
    hash2 = PromptStackLoader.IS_CHANGED(base_dir=base_dir, stack_file="", inline_stack=inline_stack)
    assert hash1 != hash2
