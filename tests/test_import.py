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

    # 4. Traversal attempts disguised inside `replace:` combinations
    evasion_stack = "\n".join([
        "import:valid.stack.txt",
        "replace:valid_leaf.txt|import:../project_b/outside.stack.txt",
        "replace:import:valid.stack.txt|import:../project_b/outside.stack.txt",
        "replace:import:valid.stack.txt|../project_b/outside_leaf.txt",
        "replace:valid_leaf.txt|random:../project_b",
    ])
    raw_evasion = loader._resolve_paths(resolved_base, "", evasion_stack)
    files_evasion = loader._build_final_file_list(raw_evasion, resolved_base, SeededRandom(0))
    assert files_evasion == []
    evasion_out = capsys.readouterr().out
    assert "Security Warning:" in evasion_out


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


def test_import_nested_with_chained_replace_and_remove(tmp_path, monkeypatch):
    """
    Tests `root -> some_other -> some_some_other` where intermediate and root stacks
    both contain `remove:` and chained `replace:` directives (including modifiers
    placed above `import:` lines).
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    for name in ["a.txt", "a_v2.txt", "a_v3.txt", "b.txt", "c.txt", "c_v2.txt", "d.txt"]:
        (prompts_dir / name).write_text(name, encoding="utf-8")

    stacks_dir = prompts_dir / "stacks"
    stacks_dir.mkdir()

    # Level 2: some_some_other
    (stacks_dir / "some_some_other.stack.txt").write_text(
        "a.txt\nb.txt\nc.txt\nd.txt\n",
        encoding="utf-8",
    )

    # Level 1: some_other (removes b.txt, replaces a.txt -> a_v2.txt and c.txt -> c_v2.txt)
    (stacks_dir / "some_other.stack.txt").write_text(
        "import:stacks/some_some_other.stack.txt\n"
        "remove:b.txt\n"
        "replace:a.txt|a_v2.txt\n"
        "replace:c.txt|c_v2.txt\n",
        encoding="utf-8",
    )

    # Level 0: root (places modifiers even BEFORE import:, chaining a_v2.txt -> a_v3.txt and removing c_v2.txt)
    (stacks_dir / "root.stack.txt").write_text(
        "replace:a_v2.txt|a_v3.txt\n"
        "remove:c_v2.txt\n"
        "import:stacks/some_other.stack.txt\n",
        encoding="utf-8",
    )

    loader = PromptStackLoader()
    raw_lines = loader._resolve_paths(str(prompts_dir), "stacks/root.stack.txt", "")
    files = loader._build_final_file_list(raw_lines, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == ["a_v3.txt", "d.txt"]


def test_import_replace_self_and_roundtrip_swap(tmp_path, monkeypatch):
    """
    Verifies that replacing a file with itself (`replace:a.txt|a.txt`) or
    swapping `a.txt -> b.txt` in an imported stack and `b.txt -> a.txt` in the
    parent stack never causes an infinite loop.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    (prompts_dir / "a.txt").write_text("a", encoding="utf-8")
    (prompts_dir / "b.txt").write_text("b", encoding="utf-8")

    stacks_dir = prompts_dir / "stacks"
    stacks_dir.mkdir()

    (stacks_dir / "child.stack.txt").write_text(
        "a.txt\nreplace:a.txt|a.txt\nreplace:a.txt|b.txt\n",
        encoding="utf-8",
    )

    inline_stack = "\n".join([
        "import:stacks/child.stack.txt",
        "replace:b.txt|a.txt",
        "replace:a.txt|a.txt",
    ])

    loader = PromptStackLoader()
    raw_lines = loader._resolve_paths(str(prompts_dir), "", inline_stack)
    files = loader._build_final_file_list(raw_lines, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == ["a.txt"]


def test_import_max_depth_limit(tmp_path, monkeypatch, capsys):
    """
    Verifies that non-cyclic deep import chains exceeding MAX_IMPORT_DEPTH (32)
    stop gracefully with a warning instead of raising RecursionError.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)
    (prompts_dir / "leaf.txt").write_text("leaf", encoding="utf-8")

    total_chain = PromptStackLoader.MAX_IMPORT_DEPTH + 5
    for i in range(total_chain):
        next_import = f"import:depth_{i + 1}.stack.txt\n" if i + 1 < total_chain else ""
        (prompts_dir / f"depth_{i}.stack.txt").write_text(
            f"leaf.txt\n{next_import}",
            encoding="utf-8",
        )

    loader = PromptStackLoader()
    raw_lines = loader._resolve_paths(str(prompts_dir), "depth_0.stack.txt", "")
    files = loader._build_final_file_list(raw_lines, str(prompts_dir), SeededRandom(0))

    assert len(files) == PromptStackLoader.MAX_IMPORT_DEPTH
    assert f"Maximum import depth ({PromptStackLoader.MAX_IMPORT_DEPTH}) exceeded" in capsys.readouterr().out


def test_import_sibling_scope_isolation_and_parent_readdition(tmp_path, monkeypatch):
    """
    Verifies that `remove:` and `replace:` inside an imported file (`branch1`)
    are strictly scoped to `branch1`'s own subtree and do NOT leak out to
    mutate sibling imports (`branch2`) or lines added later in the parent stack.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    for name in ["a.txt", "b.txt", "b1.txt"]:
        (prompts_dir / name).write_text(name, encoding="utf-8")

    stacks_dir = prompts_dir / "stacks"
    stacks_dir.mkdir()

    (stacks_dir / "common.stack.txt").write_text("a.txt\nb.txt\n", encoding="utf-8")

    # branch1 removes a.txt and replaces b.txt -> b1.txt within its own subtree
    (stacks_dir / "branch1.stack.txt").write_text(
        "import:stacks/common.stack.txt\n"
        "remove:a.txt\n"
        "replace:b.txt|b1.txt\n",
        encoding="utf-8",
    )

    # branch2 imports common.stack.txt unmodified
    (stacks_dir / "branch2.stack.txt").write_text(
        "import:stacks/common.stack.txt\n",
        encoding="utf-8",
    )

    # root imports branch1, branch2, and also adds a.txt at the end
    (stacks_dir / "root.stack.txt").write_text(
        "import:stacks/branch1.stack.txt\n"
        "import:stacks/branch2.stack.txt\n"
        "a.txt\n",
        encoding="utf-8",
    )

    loader = PromptStackLoader()
    raw_lines = loader._resolve_paths(str(prompts_dir), "stacks/root.stack.txt", "")
    files = loader._build_final_file_list(raw_lines, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == [
        "b1.txt",  # from branch1
        "a.txt",   # from branch2 (untouched by branch1's remove:a.txt)
        "b.txt",   # from branch2 (untouched by branch1's replace:b.txt|b1.txt)
        "a.txt",   # from root (untouched by branch1's remove:a.txt)
    ]


def test_import_modifier_combinations_targeting_and_replacing_with_import(tmp_path, monkeypatch, capsys):
    """
    Covers all combinations of `remove:` and `replace:` interacting with `import:` targets:
    1. `remove:import:<stack>` (both same-scope and parent removing an inner child's import block)
    2. `replace:import:<s1>|import:<s2>` (swapping one imported stack for another in-place)
    3. `replace:import:<s1>|<file>` (swapping an entire imported stack block for a single file)
    4. `replace:<file>|import:<s2>` (expanding a single file into a multi-file imported stack)
    5. Cycle protection when `replace:<file>|import:<self>` attempts a circular import via replace.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    for name in ["start.txt", "g1.txt", "g2.txt", "alt1.txt", "alt2.txt", "single.txt", "end.txt"]:
        (prompts_dir / name).write_text(name, encoding="utf-8")

    stacks_dir = prompts_dir / "stacks"
    stacks_dir.mkdir()

    (stacks_dir / "grand1.stack.txt").write_text("g1.txt\ng2.txt\n", encoding="utf-8")
    (stacks_dir / "grand2.stack.txt").write_text("alt1.txt\nalt2.txt\n", encoding="utf-8")
    (stacks_dir / "child.stack.txt").write_text(
        "start.txt\nimport:stacks/grand1.stack.txt\nend.txt\n",
        encoding="utf-8",
    )

    loader = PromptStackLoader()

    # 1. Parent removes an inner child's `import:stacks/grand1.stack.txt` block completely
    raw1 = loader._resolve_paths(
        str(prompts_dir),
        "stacks/child.stack.txt",
        "remove:import:stacks/grand1.stack.txt",
    )
    files1 = loader._build_final_file_list(raw1, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files1] == ["start.txt", "end.txt"]

    # 2. Outer stack swaps an inner child's `import:stacks/grand1.stack.txt` with `import:stacks/grand2.stack.txt`
    (stacks_dir / "parent_swap_import.stack.txt").write_text(
        "import:stacks/child.stack.txt\n"
        "replace:import:stacks/grand1.stack.txt|import:stacks/grand2.stack.txt\n",
        encoding="utf-8",
    )
    raw2 = loader._resolve_paths(str(prompts_dir), "stacks/parent_swap_import.stack.txt", "")
    files2 = loader._build_final_file_list(raw2, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files2] == ["start.txt", "alt1.txt", "alt2.txt", "end.txt"]

    # 3. Outer stack swaps an inner child's `import:stacks/grand1.stack.txt` with a single file `single.txt`
    (stacks_dir / "parent_collapse_import.stack.txt").write_text(
        "import:stacks/child.stack.txt\n"
        "replace:import:stacks/grand1.stack.txt|single.txt\n",
        encoding="utf-8",
    )
    raw3 = loader._resolve_paths(str(prompts_dir), "stacks/parent_collapse_import.stack.txt", "")
    files3 = loader._build_final_file_list(raw3, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files3] == ["start.txt", "single.txt", "end.txt"]

    # 4. Outer stack swaps a single file `g1.txt` inside an imported stack with `import:stacks/grand2.stack.txt`
    (stacks_dir / "parent_expand_file.stack.txt").write_text(
        "import:stacks/child.stack.txt\n"
        "replace:g1.txt|import:stacks/grand2.stack.txt\n",
        encoding="utf-8",
    )
    raw4 = loader._resolve_paths(str(prompts_dir), "stacks/parent_expand_file.stack.txt", "")
    files4 = loader._build_final_file_list(raw4, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files4] == ["start.txt", "alt1.txt", "alt2.txt", "g2.txt", "end.txt"]

    # 5. Cyclic import attempted via `replace:start.txt|import:stacks/cycle_replace.stack.txt`
    (stacks_dir / "cycle_replace.stack.txt").write_text(
        "start.txt\n"
        "replace:start.txt|import:stacks/cycle_replace.stack.txt\n"
        "end.txt\n",
        encoding="utf-8",
    )
    raw5 = loader._resolve_paths(str(prompts_dir), "stacks/cycle_replace.stack.txt", "")
    files5 = loader._build_final_file_list(raw5, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files5] == ["end.txt"]
    assert "Circular import detected" in capsys.readouterr().out


def test_import_current_file_relative_paths_and_nested_traversal(tmp_path, monkeypatch):
    """
    Verifies that `./` and `../` paths (in `import:`, normal file lines, `random:`,
    `remove:`, and `replace:`) resolve relative to the directory of the stack file
    where they are written, while paths without `./` or `../` resolve relative to `base_dir`.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    # Global preset at base_dir/stacks/global_preset.stack.txt
    stacks_dir = prompts_dir / "stacks"
    stacks_dir.mkdir()
    (prompts_dir / "global_item.txt").write_text("global", encoding="utf-8")
    (stacks_dir / "global_preset.stack.txt").write_text("global_item.txt\n", encoding="utf-8")

    # Deep project structure: base_dir/projects/group/scene/phase/
    scene_dir = prompts_dir / "projects" / "group" / "scene"
    phase_dir = scene_dir / "phase"
    local_pool = phase_dir / "local_pool"
    local_pool.mkdir(parents=True)

    (scene_dir / "scene_init.txt").write_text("scene init", encoding="utf-8")
    (scene_dir / "scene_extra.txt").write_text("scene extra", encoding="utf-8")
    (phase_dir / "local_prompt.txt").write_text("local prompt", encoding="utf-8")
    (phase_dir / "alt_prompt.txt").write_text("alt prompt", encoding="utf-8")
    (local_pool / "pool_only.txt").write_text("pool item", encoding="utf-8")

    # scene/_scene_base.stack.txt uses `./` relative to `scene/` and base-root `stacks/...`
    (scene_dir / "_scene_base.stack.txt").write_text(
        "import:stacks/global_preset.stack.txt\n"
        "./scene_init.txt\n"
        "./scene_extra.txt\n",
        encoding="utf-8",
    )

    # phase/_phase_base.stack.txt imports `../_scene_base.stack.txt` and uses `./` relative to `phase/`
    (phase_dir / "_phase_base.stack.txt").write_text(
        "import:../_scene_base.stack.txt\n"
        "./local_prompt.txt\n"
        "random:./local_pool\n",
        encoding="utf-8",
    )

    # phase/01.stack.txt imports `./_phase_base.stack.txt` and uses relative `replace:` and `remove:`
    (phase_dir / "01.stack.txt").write_text(
        "import:./_phase_base.stack.txt\n"
        "replace:./local_prompt.txt|./alt_prompt.txt\n"
        "remove:../scene_extra.txt\n",
        encoding="utf-8",
    )

    loader = PromptStackLoader()
    raw_lines = loader._resolve_paths(
        str(prompts_dir),
        "projects/group/scene/phase/01.stack.txt",
        "",
    )
    files = loader._build_final_file_list(raw_lines, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == [
        "global_item.txt",
        "scene_init.txt",
        "alt_prompt.txt",
        "pool_only.txt",
    ]


def test_import_relative_path_security_cannot_escape_base_dir(tmp_path, monkeypatch, capsys):
    """
    Verifies that `../` relative paths inside deeply nested stack files (or inside
    an external `stack_file` outside `base_dir`) can never escape `resolved_base_dir`.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    project_a = prompts_dir / "arch" / "project_a"
    deep_dir = project_a / "sub1" / "sub2"
    deep_dir.mkdir(parents=True)

    project_b = prompts_dir / "arch" / "project_b"
    project_b.mkdir(parents=True)
    (project_b / "secret.stack.txt").write_text("secret.txt\n", encoding="utf-8")
    (project_b / "secret.txt").write_text("secret", encoding="utf-8")

    (deep_dir / "valid.txt").write_text("valid", encoding="utf-8")
    (deep_dir / "leaf.stack.txt").write_text(
        "import:../../../project_b/secret.stack.txt\n"
        "../../../project_b/secret.txt\n"
        "./valid.txt\n",
        encoding="utf-8",
    )

    loader = PromptStackLoader()
    resolved_base = loader._resolve_base_dir("prompts/arch/project_a")

    raw = loader._resolve_paths(resolved_base, "sub1/sub2/leaf.stack.txt", "")
    files = loader._build_final_file_list(raw, resolved_base, SeededRandom(0))

    assert [os.path.basename(f) for f in files] == ["valid.txt"]
    out = capsys.readouterr().out
    assert "Security Warning:" in out


def test_cross_platform_backslash_and_slash_interoperability(tmp_path, monkeypatch):
    """
    Verifies that Windows-style backslashes (`\\`) and POSIX forward slashes (`/`)
    can be freely mixed across `stack_file`, `import:`, regular file paths, `random:`,
    `remove:`, and `replace:` (including `.\\` and `..\\` relative paths).
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    stacks_dir = prompts_dir / "stacks"
    scene_dir = prompts_dir / "projects" / "scene"
    sub_dir = scene_dir / "sub"
    pool_dir = sub_dir / "pool"
    stacks_dir.mkdir(parents=True)
    pool_dir.mkdir(parents=True)

    (prompts_dir / "global_a.txt").write_text("ga", encoding="utf-8")
    (stacks_dir / "preset.stack.txt").write_text("global_a.txt\n", encoding="utf-8")
    (scene_dir / "parent_item.txt").write_text("pi", encoding="utf-8")
    (scene_dir / "remove_me.txt").write_text("rm", encoding="utf-8")
    (sub_dir / "local_old.txt").write_text("old", encoding="utf-8")
    (sub_dir / "local_new.txt").write_text("new", encoding="utf-8")
    (pool_dir / "rand_item.txt").write_text("ri", encoding="utf-8")

    (scene_dir / "_parent.stack.txt").write_text(
        "import:stacks\\preset.stack.txt\n"
        ".\\parent_item.txt\n"
        ".\\remove_me.txt\n",
        encoding="utf-8",
    )

    (sub_dir / "01.stack.txt").write_text(
        "import:..\\_parent.stack.txt\n"
        ".\\local_old.txt\n"
        "random:.\\pool\n"
        "replace:.\\local_old.txt|.\\local_new.txt\n"
        "remove:..\\remove_me.txt\n",
        encoding="utf-8",
    )

    loader = PromptStackLoader()
    raw = loader._resolve_paths(str(prompts_dir), "projects\\scene\\sub\\01.stack.txt", "")
    files = loader._build_final_file_list(raw, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == [
        "global_a.txt",
        "parent_item.txt",
        "local_new.txt",
        "rand_item.txt",
    ]


def test_classical_cross_platform_security_hazards(tmp_path, monkeypatch):
    """
    Tests classical cross-platform path security hazards:
    1. Symlink inside a `random:` directory pointing outside `base_dir`.
    2. Windows reserved device names (`CON.txt`, `AUX.txt`, `NUL.txt`, `COM1.txt`, `LPT1.txt`).
    3. Windows drive-relative paths (`C:secret.txt`, `C:\\secret.txt`) and NTFS
       Alternate Data Streams (`valid.txt:$DATA`, `valid.txt:hidden`).
    4. Null-byte injection (`valid.txt\\x00`).
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_secret = outside_dir / "outside_secret.txt"
    outside_secret.write_text("leaked secret", encoding="utf-8")

    # 1. Symlink inside a valid `random:` directory pointing outside `base_dir`
    pool_dir = prompts_dir / "random_pool"
    pool_dir.mkdir()
    os.symlink(outside_secret, pool_dir / "escaped_link.txt")

    # Create files with names that would be dangerous on Windows/NTFS if allowed
    (prompts_dir / "valid.txt").write_text("safe", encoding="utf-8")

    inline_stack = "\n".join([
        "random:random_pool",
        "import:CON.txt",
        "CON.txt",
        "sub/AUX.txt",
        "NUL.txt",
        "COM1.txt",
        "LPT9.txt",
        "import:C:secret.txt",
        "C:secret.txt",
        "C:\\secret.txt",
        "valid.txt:$DATA",
        "valid.txt:hidden_stream.txt",
        "valid.txt\x00.txt",
        "valid.txt",
    ])

    loader = PromptStackLoader()
    raw = loader._resolve_paths(str(prompts_dir), "", inline_stack)
    files = loader._build_final_file_list(raw, str(prompts_dir), SeededRandom(0))

    # Only `valid.txt` is allowed; the symlinked file in `random_pool` and all
    # device/ADS/drive/null-byte paths are blocked.
    assert [os.path.basename(f) for f in files] == ["valid.txt"]
