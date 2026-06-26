import os
import pytest
import sys
import types

node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
adaptive_prompts_dir = os.path.abspath(os.path.join(node_root, "..", "comfyui-adaptiveprompts"))

sys.modules["py"] = types.ModuleType("py")
sys.modules["py"].__path__ = [
    os.path.join(node_root, "py"),
    os.path.join(adaptive_prompts_dir, "py")
]

from py.prompt_stack_loader import PromptStackLoader

def test_context_override_lazy_evaluation(tmp_path, monkeypatch):
    """
    Tests that override_context=True correctly purges old bucket values
    DURING evaluation of a file, rather than after.
    
    If it purges after evaluation, then a file that overrides a variable
    and then immediately reads it might accidentally read the old value.
    """
    monkeypatch.setattr(sys.modules["py.prompt_stack_loader"], "__file__", str(tmp_path / "py" / "loader.py"))
    base_dir = str(tmp_path / "prompts")
    os.makedirs(base_dir, exist_ok=True)
    
    file1_path = os.path.join(base_dir, "file1.txt")
    with open(file1_path, "w", encoding="utf-8") as f:
        f.write("{OLD_VALUE}^var")
    
    file2_path = os.path.join(base_dir, "file2.txt")
    with open(file2_path, "w", encoding="utf-8") as f:
        f.write("{NEW_VALUE}^var\n{__^var__}^body")

    inline_stack = "file1.txt\nfile2.txt"

    loader = PromptStackLoader()
    
    # We test multiple times with different seeds to ensure RNG never picks the OLD_VALUE.
    for seed in range(50):
        final_prompt, current_context, lora_string = loader.process(
            seed=seed,
            base_dir=str(base_dir),
            stack_file="",
            inline_stack=inline_stack,
            override_context=True,
            context=None
        )
        
        # We just assert OLD_VALUE is completely absent from the assigned body.
        assert "body" in current_context
        body_value = list(current_context["body"].values())[0]
        
        assert "OLD_VALUE" not in body_value, f"Seed {seed} leaked OLD_VALUE into body: {body_value}"
        assert "NEW_VALUE" in body_value, f"Seed {seed} missing NEW_VALUE in body: {body_value}"
        
        # Check context
        assert "var" in current_context
        # The bucket should only have the new value.
        bucket_values = list(current_context["var"].values())
        assert "OLD_VALUE" not in bucket_values
        assert "NEW_VALUE" in bucket_values
