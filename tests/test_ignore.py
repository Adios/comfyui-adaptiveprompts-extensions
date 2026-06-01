import os
import sys
import types

node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
adaptive_prompts_dir = os.path.abspath(os.path.join(node_root, "..", "comfyui-adaptiveprompts"))

# Mock the 'py' package to include both our extension's py/ and the mother project's py/.
sys.modules["py"] = types.ModuleType("py")
sys.modules["py"].__path__ = [
    os.path.join(node_root, "py"),
    os.path.join(adaptive_prompts_dir, "py")
]

from py.prompt_stack_loader import PromptStackLoader
from py.generator import SeededRandom

def test_ignore_files_and_folders_in_random(tmp_path):
    loader = PromptStackLoader()
    base_dir = tmp_path / "prompts"
    base_dir.mkdir()
    
    # Create valid files
    (base_dir / "a.txt").write_text("valid a")
    (base_dir / "b.txt").write_text("valid b")
    
    # Create ignored files
    (base_dir / "_ignore.txt").write_text("ignore me")
    (base_dir / ".hidden.txt").write_text("hidden me")
    
    # Create valid folder and ignored folder
    sub = base_dir / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("valid c")
    
    sub_ignore = base_dir / "_sub_ignore"
    sub_ignore.mkdir()
    (sub_ignore / "d.txt").write_text("should be ignored")
    
    sub_hidden = base_dir / ".sub_hidden"
    sub_hidden.mkdir()
    (sub_hidden / "e.txt").write_text("should be hidden")
    
    # Test random selection
    rng = SeededRandom(42)
    selected = set()
    for _ in range(50):
        f = loader._get_random_file(str(base_dir), rng)
        selected.add(os.path.basename(f))
        
    assert "_ignore.txt" not in selected
    assert ".hidden.txt" not in selected
    assert "d.txt" not in selected
    assert "e.txt" not in selected
    
    assert "a.txt" in selected
    assert "b.txt" in selected
    assert "c.txt" in selected

def test_cache_validation_ignores(tmp_path, monkeypatch):
    import sys
    # Mock node_root inside the module to allow our tmp_path base_dir
    monkeypatch.setattr(sys.modules["py.prompt_stack_loader"], "__file__", str(tmp_path / "py" / "loader.py"))

    loader = PromptStackLoader()
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    
    # Create valid file
    valid_file = prompts_dir / "a.txt"
    valid_file.write_text("valid a")
    
    # Initial hash
    hash1 = loader.IS_CHANGED(str(prompts_dir), "", "random:.")
    
    # Add ignored file
    (prompts_dir / "_ignore.txt").write_text("ignore me")
    hash2 = loader.IS_CHANGED(str(prompts_dir), "", "random:.")
    assert hash1 == hash2, "Adding an ignored file should not change the hash"
    
    # Modify ignored file
    (prompts_dir / "_ignore.txt").write_text("ignore me changed")
    hash3 = loader.IS_CHANGED(str(prompts_dir), "", "random:.")
    assert hash1 == hash3, "Modifying an ignored file should not change the hash"
    
    # Add ignored folder with file
    sub_ignore = prompts_dir / "_sub_ignore"
    sub_ignore.mkdir()
    (sub_ignore / "b.txt").write_text("should be ignored")
    hash4 = loader.IS_CHANGED(str(prompts_dir), "", "random:.")
    assert hash1 == hash4, "Adding an ignored folder should not change the hash"
