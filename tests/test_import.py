import os
import sys
import time
import types
import pytest

node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
adaptive_prompts_dir = os.path.abspath(os.path.join(node_root, "..", "comfyui-adaptiveprompts"))

sys.modules["py"] = types.ModuleType("py")
sys.modules["py"].__path__ = [
    os.path.join(node_root, "py"),
    os.path.join(adaptive_prompts_dir, "py"),
]

from py.prompt_stack_loader import PromptStackLoader, SeededRandom


def _setup_mock_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys.modules["py.prompt_stack_loader"],
        "__file__",
        str(tmp_path / "py" / "loader.py"),
    )
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    return prompts_dir


def test_import_basic_and_nested(tmp_path, monkeypatch):
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    (prompts_dir / "first.txt").write_text("first", encoding="utf-8")
    (prompts_dir / "second.txt").write_text("second", encoding="utf-8")
    (prompts_dir / "third.txt").write_text("third", encoding="utf-8")
    (prompts_dir / "fourth.txt").write_text("fourth", encoding="utf-8")

    stacks_dir = prompts_dir / "stacks"
    stacks_dir.mkdir()

    (stacks_dir / "level2.stack.txt").write_text("second.txt\n", encoding="utf-8")
    (stacks_dir / "level1.stack.txt").write_text(
        "first.txt\nimport:stacks/level2.stack.txt\nthird.txt\n",
        encoding="utf-8",
    )

    main_stack = prompts_dir / "main.stack.txt"
    main_stack.write_text("import:stacks/level1.stack.txt\n", encoding="utf-8")

    loader = PromptStackLoader()
    raw_lines = loader._resolve_paths(str(prompts_dir), "main.stack.txt", "fourth.txt")
    files = loader._build_final_file_list(raw_lines, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == [
        "first.txt",
        "second.txt",
        "third.txt",
        "fourth.txt",
    ]


def test_import_duplicate_sequential_and_diamond_allowed(tmp_path, monkeypatch):
    """
    Two identical import lines in the same stack file (or across diamond branches)
    must be evaluated in-order since they are not cyclic.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    (prompts_dir / "expr.txt").write_text("smile", encoding="utf-8")
    (prompts_dir / "step_1.txt").write_text("step1", encoding="utf-8")
    (prompts_dir / "step_2.txt").write_text("step2", encoding="utf-8")

    stacks_dir = prompts_dir / "stacks"
    stacks_dir.mkdir()
    (stacks_dir / "snippet.stack.txt").write_text("expr.txt\n", encoding="utf-8")

    inline_stack = "\n".join([
        "import:stacks/snippet.stack.txt",
        "step_1.txt",
        "import:stacks/snippet.stack.txt",
        "step_2.txt",
    ])

    loader = PromptStackLoader()
    raw_lines = loader._resolve_paths(str(prompts_dir), "", inline_stack)
    files = loader._build_final_file_list(raw_lines, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == [
        "expr.txt",
        "step_1.txt",
        "expr.txt",
        "step_2.txt",
    ]


def test_import_with_replace_and_remove(tmp_path, monkeypatch):
    """
    Verifies that `replace:` and `remove:` can modify or exclude lines that were
    pulled in via `import:`, preserving exact in-place ordering.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    for name in ["sunny.txt", "rainy.txt", "watercolor.txt", "film_grain.txt", "scene.txt"]:
        (prompts_dir / name).write_text(name, encoding="utf-8")

    stacks_dir = prompts_dir / "stacks"
    stacks_dir.mkdir()
    (stacks_dir / "preset.stack.txt").write_text(
        "sunny.txt\nwatercolor.txt\nfilm_grain.txt\n",
        encoding="utf-8",
    )

    inline_stack = "\n".join([
        "import:stacks/preset.stack.txt",
        "scene.txt",
        "replace:sunny.txt|rainy.txt",
        "remove:film_grain.txt",
    ])

    loader = PromptStackLoader()
    raw_lines = loader._resolve_paths(str(prompts_dir), "", inline_stack)
    files = loader._build_final_file_list(raw_lines, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == [
        "rainy.txt",
        "watercolor.txt",
        "scene.txt",
    ]


def test_import_cycle_prevention(tmp_path, monkeypatch, capsys):
    """
    Tests direct self-cycles (A -> A), mutual cycles (A -> B -> A),
    3-way cycles (A -> B -> C -> A), root stack_file cycles (root -> A -> root),
    and symlink cycles.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    (prompts_dir / "leaf_a.txt").write_text("a", encoding="utf-8")
    (prompts_dir / "leaf_b.txt").write_text("b", encoding="utf-8")
    (prompts_dir / "leaf_c.txt").write_text("c", encoding="utf-8")

    # 1. Direct self-cycle: self.stack.txt -> self.stack.txt
    (prompts_dir / "self.stack.txt").write_text(
        "leaf_a.txt\nimport:self.stack.txt\nleaf_b.txt\n",
        encoding="utf-8",
    )

    loader = PromptStackLoader()
    raw_self = loader._resolve_paths(str(prompts_dir), "", "import:self.stack.txt")
    files_self = loader._build_final_file_list(raw_self, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files_self] == ["leaf_a.txt", "leaf_b.txt"]
    assert "Circular import detected" in capsys.readouterr().out

    # 2. 3-node cycle: cycle_a -> cycle_b -> cycle_c -> cycle_a (and root stack_file cycle)
    (prompts_dir / "cycle_a.stack.txt").write_text(
        "leaf_a.txt\nimport:cycle_b.stack.txt\n",
        encoding="utf-8",
    )
    (prompts_dir / "cycle_b.stack.txt").write_text(
        "leaf_b.txt\nimport:cycle_c.stack.txt\n",
        encoding="utf-8",
    )
    (prompts_dir / "cycle_c.stack.txt").write_text(
        "leaf_c.txt\nimport:cycle_a.stack.txt\n",
        encoding="utf-8",
    )

    raw_cycle = loader._resolve_paths(str(prompts_dir), "cycle_a.stack.txt", "")
    files_cycle = loader._build_final_file_list(raw_cycle, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files_cycle] == [
        "leaf_a.txt",
        "leaf_b.txt",
        "leaf_c.txt",
    ]
    assert "Circular import detected" in capsys.readouterr().out

    # 3. Symlink cycle: real.stack.txt -> sym.stack.txt (which points to real.stack.txt)
    real_stack = prompts_dir / "real.stack.txt"
    sym_stack = prompts_dir / "sym.stack.txt"
    real_stack.write_text("leaf_a.txt\nimport:sym.stack.txt\n", encoding="utf-8")
    os.symlink(real_stack, sym_stack)

    raw_sym = loader._resolve_paths(str(prompts_dir), "", "import:real.stack.txt")
    files_sym = loader._build_final_file_list(raw_sym, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files_sym] == ["leaf_a.txt"]
    assert "Circular import detected" in capsys.readouterr().out


def test_import_security_strictly_constrained_to_base_dir(tmp_path, monkeypatch, capsys):
    """
    Verifies that `import:` is strictly constrained to `resolved_base_dir`:
    - Cannot escape `resolved_base_dir` via `../` even if the target is still inside `node_root` or `prompts/`.
    - Cannot use absolute paths.
    - Cannot escape `resolved_base_dir` via symlinks.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    project_a_dir = prompts_dir / "arch" / "project_a"
    project_b_dir = prompts_dir / "arch" / "project_b"
    project_a_dir.mkdir(parents=True)
    project_b_dir.mkdir(parents=True)

    (project_a_dir / "valid_leaf.txt").write_text("project a leaf", encoding="utf-8")
    (project_a_dir / "valid.stack.txt").write_text("valid_leaf.txt\n", encoding="utf-8")

    # A sibling file inside prompts/arch/project_b (inside node_root, but outside project_a base_dir)
    (project_b_dir / "outside_leaf.txt").write_text("project b leaf", encoding="utf-8")
    (project_b_dir / "outside.stack.txt").write_text("outside_leaf.txt\n", encoding="utf-8")

    # A symlink inside project_a_dir pointing to project_b/outside.stack.txt
    symlink_escape = project_a_dir / "symlink_outside.stack.txt"
    os.symlink(project_b_dir / "outside.stack.txt", symlink_escape)

    loader = PromptStackLoader()
    resolved_base = loader._resolve_base_dir("prompts/arch/project_a")
    assert resolved_base == str(project_a_dir)

    # 1. Relative traversal out of `prompts/arch/project_a` into `prompts/arch/project_b`
    raw_traversal = loader._resolve_paths(
        resolved_base,
        "",
        "import:../project_b/outside.stack.txt\nimport:valid.stack.txt",
    )
    files_traversal = loader._build_final_file_list(raw_traversal, resolved_base, SeededRandom(0))
    assert [os.path.basename(f) for f in files_traversal] == ["valid_leaf.txt"]
    assert "Security Warning: import '../project_b/outside.stack.txt' attempts path traversal" in capsys.readouterr().out

    # 2. Symlink escape out of `prompts/arch/project_a`
    raw_symlink = loader._resolve_paths(
        resolved_base,
        "",
        "import:symlink_outside.stack.txt\nimport:valid.stack.txt",
    )
    files_symlink = loader._build_final_file_list(raw_symlink, resolved_base, SeededRandom(0))
    assert [os.path.basename(f) for f in files_symlink] == ["valid_leaf.txt"]
    assert "Security Warning: import 'symlink_outside.stack.txt' attempts path traversal" in capsys.readouterr().out

    # 3. Absolute path attempt
    if os.name != "nt":
        raw_abs = loader._resolve_paths(resolved_base, "", "import:/etc/passwd")
        assert raw_abs == []
        assert "Security Warning: import '/etc/passwd' attempts path traversal" in capsys.readouterr().out


def test_import_edge_cases_comments_missing_and_size_limit(tmp_path, monkeypatch, capsys):
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    (prompts_dir / "ok.txt").write_text("ok", encoding="utf-8")
    (prompts_dir / "should_not_load.txt").write_text("bad", encoding="utf-8")
    (prompts_dir / "commented.stack.txt").write_text("should_not_load.txt\n", encoding="utf-8")

    # Create >5MB stack file
    large_stack = prompts_dir / "large.stack.txt"
    with open(large_stack, "wb") as f:
        f.seek((5 * 1024 * 1024) + 10)
        f.write(b"\0")

    sub_dir = prompts_dir / "subdir"
    sub_dir.mkdir()

    inline_stack = "\n".join([
        "#import:commented.stack.txt",
        "# import:commented.stack.txt",
        "import:",
        "import:   ",
        "import:missing.stack.txt",
        "import:subdir",
        "import:large.stack.txt",
        "ok.txt",
    ])

    loader = PromptStackLoader()
    raw_lines = loader._resolve_paths(str(prompts_dir), "", inline_stack)
    files = loader._build_final_file_list(raw_lines, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == ["ok.txt"]
    out = capsys.readouterr().out
    assert "Empty 'import:' directive" in out
    assert "is not a valid file" in out
    assert "exceeds 5MB limit" in out


def test_import_is_changed_cache_invalidation(tmp_path, monkeypatch):
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    leaf_file = prompts_dir / "leaf.txt"
    leaf_file.write_text("initial leaf", encoding="utf-8")

    alt_file = prompts_dir / "alt.txt"
    alt_file.write_text("alt leaf", encoding="utf-8")

    nested_stack = prompts_dir / "nested.stack.txt"
    nested_stack.write_text("leaf.txt\n", encoding="utf-8")

    root_stack = prompts_dir / "root.stack.txt"
    root_stack.write_text("import:nested.stack.txt\n", encoding="utf-8")

    hash1 = PromptStackLoader.IS_CHANGED(
        base_dir=str(prompts_dir),
        stack_file="root.stack.txt",
        inline_stack="",
    )

    # 1. Modify the leaf prompt file referenced inside nested.stack.txt
    time.sleep(0.01)
    mtime1 = time.time() + 10.0
    os.utime(leaf_file, (mtime1, mtime1))

    hash2 = PromptStackLoader.IS_CHANGED(
        base_dir=str(prompts_dir),
        stack_file="root.stack.txt",
        inline_stack="",
    )
    assert hash1 != hash2, "IS_CHANGED must detect changes to prompt files inside imported stacks"

    # 2. Modify the imported stack file itself
    mtime2 = time.time() + 20.0
    os.utime(nested_stack, (mtime2, mtime2))

    hash3 = PromptStackLoader.IS_CHANGED(
        base_dir=str(prompts_dir),
        stack_file="root.stack.txt",
        inline_stack="",
    )
    assert hash2 != hash3, "IS_CHANGED must detect changes to the imported stack file itself"

    # 3. Modify a replacement target file in `replace:leaf.txt|alt.txt`
    hash4 = PromptStackLoader.IS_CHANGED(
        base_dir=str(prompts_dir),
        stack_file="root.stack.txt",
        inline_stack="replace:leaf.txt|alt.txt",
    )
    mtime3 = time.time() + 30.0
    os.utime(alt_file, (mtime3, mtime3))
    hash5 = PromptStackLoader.IS_CHANGED(
        base_dir=str(prompts_dir),
        stack_file="root.stack.txt",
        inline_stack="replace:leaf.txt|alt.txt",
    )
    assert hash4 != hash5, "IS_CHANGED must detect changes to replacement target files"


def test_import_end_to_end_process(tmp_path, monkeypatch):
    """
    Verifies full `loader.process()` evaluation across imported stacks,
    including LoRA extraction, ps_switch macros, and replace: overrides.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    (prompts_dir / "lora.txt").write_text("<lora:char_lora:0.8> 1girl", encoding="utf-8")
    (prompts_dir / "lighting_sunny.txt").write_text("sunny lighting", encoding="utf-8")
    (prompts_dir / "lighting_rainy.txt").write_text("rainy lighting", encoding="utf-8")
    (prompts_dir / "footer.txt").write_text("masterpiece", encoding="utf-8")

    stacks_dir = prompts_dir / "stacks"
    stacks_dir.mkdir()
    (stacks_dir / "base.stack.txt").write_text(
        "lora.txt\nlighting_sunny.txt\n",
        encoding="utf-8",
    )
    (stacks_dir / "scene.stack.txt").write_text(
        "import:stacks/base.stack.txt\nfooter.txt\nreplace:lighting_sunny.txt|lighting_rainy.txt\n",
        encoding="utf-8",
    )

    loader = PromptStackLoader()
    prompt, ctx, lora_str = loader.process(
        seed=42,
        base_dir=str(prompts_dir),
        stack_file="stacks/scene.stack.txt",
        inline_stack="",
        override_context=True,
        context=None,
    )

    assert prompt == "1girl, rainy lighting, masterpiece"
    assert lora_str == "<lora:char_lora:0.8>"
