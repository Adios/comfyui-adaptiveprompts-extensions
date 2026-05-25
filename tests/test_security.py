import os
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

def test_resolve_base_dir_security(monkeypatch):
    # Mock node root
    node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    # Normal empty path -> default to prompts
    assert PromptStackLoader._resolve_base_dir("") == os.path.join(node_root, "prompts")
    
    # Normal relative path -> relative to node root
    assert PromptStackLoader._resolve_base_dir("my_folder") == os.path.join(node_root, "my_folder")
    assert PromptStackLoader._resolve_base_dir("./my_folder") == os.path.join(node_root, "my_folder")
    
    # Deep traversal attempt -> caught and defaults to prompts
    assert PromptStackLoader._resolve_base_dir("../../") == os.path.join(node_root, "prompts")
    
    # Name spoofing edge case -> caught and defaults to prompts
    # e.g., if node_root is /custom_nodes/extension
    # User tries ../extension_hacked -> /custom_nodes/extension_hacked
    spoofed = "../" + os.path.basename(node_root) + "_hacked"
    assert PromptStackLoader._resolve_base_dir(spoofed) == os.path.join(node_root, "prompts")
    
    # Absolute path handling
    # Since it is disabled, an absolute path won't start with node_root
    # so it should securely fallback to prompts
    if os.name != 'nt': # On Linux/Mac
        assert PromptStackLoader._resolve_base_dir("/etc/passwd") == os.path.join(node_root, "prompts")

def test_resolve_paths_security(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys.modules["py.prompt_stack_loader"], "__file__", str(tmp_path / "py" / "loader.py"))
    
    # If a stack file path breaks out of base_dir, it should be ignored (returns empty path list)
    base_dir = str(tmp_path / "prompts")
    os.makedirs(base_dir, exist_ok=True)
    
    valid_file = os.path.join(base_dir, "valid.txt")
    with open(valid_file, "w") as f:
        f.write("test_line")
        
    loader = PromptStackLoader()
        
    # Legitimate stack file
    paths = loader._resolve_paths(base_dir, "valid.txt", "")
    assert len(paths) == 1
    
    # Traversal stack file (escaping the node_root entirely)
    # The loader logs a warning and skips, meaning paths should be empty
    paths_hacked = loader._resolve_paths(base_dir, "../../../etc/passwd", "")
    assert len(paths_hacked) == 0

    # Legitimate stack file escaping base_dir but staying in node_root
    valid_sibling_dir = tmp_path / "stacks"
    valid_sibling_dir.mkdir(parents=True, exist_ok=True)
    sibling_file = valid_sibling_dir / "sibling.txt"
    with open(sibling_file, "w") as f:
        f.write("sibling_line")
        
    paths_sibling = loader._resolve_paths(base_dir, "../stacks/sibling.txt", "")
    assert len(paths_sibling) == 1

def test_file_size_limit(tmp_path, monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys.modules["py.prompt_stack_loader"], "__file__", str(tmp_path / "py" / "loader.py"))
    
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    
    large_file = prompts_dir / "large.txt"
    # Create a 5MB + 10 byte file
    with open(large_file, "wb") as f:
        f.seek((5 * 1024 * 1024) + 10)
        f.write(b"\0")
        
    loader = PromptStackLoader()
    paths = loader._resolve_paths(str(prompts_dir), "large.txt", "")
    assert len(paths) == 0
    
    captured = capsys.readouterr()
    assert "exceeds 5MB limit" in captured.out
