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

    # 4. Traversal attempts disguised inside `replace:` combinations (tested independently
    #    so earlier replacements cannot mask later ones)
    evasion_cases = [
        "import:valid.stack.txt\nreplace:valid_leaf.txt|import:../project_b/outside.stack.txt",
        "import:valid.stack.txt\nreplace:import:valid.stack.txt|import:../project_b/outside.stack.txt",
        "import:valid.stack.txt\nreplace:import:valid.stack.txt|../project_b/outside_leaf.txt",
        "import:valid.stack.txt\nreplace:valid_leaf.txt|random:../project_b",
    ]
    for case in evasion_cases:
        raw_evasion = loader._resolve_paths(resolved_base, "", case)
        files_evasion = loader._build_final_file_list(raw_evasion, resolved_base, SeededRandom(0))
        assert files_evasion == []
        assert "Security Warning:" in capsys.readouterr().out

    # 5. Absolute paths inside stack lines are blocked even when pointing inside base_dir
    abs_inside_stack = str(project_a_dir / "valid.stack.txt")
    abs_inside_leaf = str(project_a_dir / "valid_leaf.txt")
    abs_inside_dir = str(project_a_dir)
    raw_abs_inside = loader._resolve_paths(
        resolved_base,
        "",
        f"import:{abs_inside_stack}\n{abs_inside_leaf}\nrandom:{abs_inside_dir}",
    )
    files_abs_inside = loader._build_final_file_list(raw_abs_inside, resolved_base, SeededRandom(0))
    assert files_abs_inside == []
    assert "Security Warning:" in capsys.readouterr().out


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


def test_security_marker_spoofing_and_modifier_injection_via_replace(tmp_path, monkeypatch, capsys):
    """
    Verifies that internal boundary markers (`__ps_begin_import__:`, `__ps_end_import__:`),
    nested modifier directives (`remove:`, `replace:`), and malformed modifiers cannot be
    injected via `replace:` right-hand sides or raw stack lines.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    for name in ["a.txt", "b.txt", "c.txt", "legit_leaf.txt"]:
        (prompts_dir / name).write_text(name, encoding="utf-8")
    (prompts_dir / "legit.stack.txt").write_text("legit_leaf.txt\n", encoding="utf-8")

    # 1. Marker spoofing via `replace:` RHS must not swallow subsequent `b.txt`
    spoof_stack = "\n".join([
        "import:legit.stack.txt",
        "a.txt",
        "b.txt",
        "replace:a.txt|__ps_begin_import__:import:legit.stack.txt",
        "remove:import:legit.stack.txt",
    ])
    loader = PromptStackLoader()
    raw1 = loader._resolve_paths(str(prompts_dir), "", spoof_stack)
    files1 = loader._build_final_file_list(raw1, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files1] == ["a.txt", "b.txt"]
    assert "Invalid modifier directive" in capsys.readouterr().out

    # 2. Second-stage modifier injection (`replace:a.txt|remove:b.txt`) must not execute in `_build_final_file_list`
    inject_stack = "\n".join([
        "a.txt",
        "b.txt",
        "replace:a.txt|remove:b.txt",
        "replace:a.txt|replace:b.txt|c.txt",
        "replace:a.txt|#comment",
        "remove:",
        "replace:a.txt",
        "replace:a.txt|",
        "replace:|b.txt",
    ])
    raw2 = loader._resolve_paths(str(prompts_dir), "", inject_stack)
    files2 = loader._build_final_file_list(raw2, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files2] == ["a.txt", "b.txt"]
    out2 = capsys.readouterr().out
    assert "Invalid modifier directive" in out2
    assert "Security Warning:" not in out2

    # 3. Cycle bypass attempt via `replace:a.txt|replace:b.txt|import:self_bypass.stack.txt`
    (prompts_dir / "self_bypass.stack.txt").write_text(
        "a.txt\n"
        "replace:a.txt|replace:b.txt|import:self_bypass.stack.txt\n"
        "b.txt\n",
        encoding="utf-8",
    )
    raw3 = loader._resolve_paths(str(prompts_dir), "self_bypass.stack.txt", "")
    files3 = loader._build_final_file_list(raw3, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files3] == ["a.txt", "b.txt"]


def test_security_dag_import_bomb_and_replace_amplification_dos(tmp_path, monkeypatch, capsys):
    """
    Verifies that non-cyclic exponential DAG import bombs ("Billion Laughs") and
    repeated `replace:` amplification are stopped cleanly by total-import and
    expanded-line budgets without hanging or exhausting memory.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)
    (prompts_dir / "leaf.txt").write_text("leaf", encoding="utf-8")

    # 1. Binary DAG bomb of depth 20 (would expand 2^20 = 1,048,576 times without budget)
    for i in range(20):
        (prompts_dir / f"dag_{i}.stack.txt").write_text(
            f"import:dag_{i + 1}.stack.txt\nimport:dag_{i + 1}.stack.txt\n",
            encoding="utf-8",
        )
    (prompts_dir / "dag_20.stack.txt").write_text("leaf.txt\n", encoding="utf-8")

    loader = PromptStackLoader()
    raw_dag = loader._resolve_paths(str(prompts_dir), "dag_0.stack.txt", "")
    files_dag = loader._build_final_file_list(raw_dag, str(prompts_dir), SeededRandom(0))
    assert len(files_dag) <= PromptStackLoader.MAX_TOTAL_IMPORTS
    assert "Maximum total import expansions" in capsys.readouterr().out

    # 2. Repeated `replace:` line amplification bomb (50^6 = 15B lines without budget)
    (prompts_dir / "dup50.stack.txt").write_text("leaf.txt\n" * 50, encoding="utf-8")
    amp_stack = "leaf.txt\n" + ("replace:leaf.txt|import:dup50.stack.txt\n" * 6)
    raw_amp = loader._resolve_paths(str(prompts_dir), "", amp_stack)
    files_amp = loader._build_final_file_list(raw_amp, str(prompts_dir), SeededRandom(0))
    assert len(files_amp) <= PromptStackLoader.MAX_EXPANDED_LINES
    assert "Maximum expanded stack lines" in capsys.readouterr().out


def test_modifier_count_budget_prevents_cpu_dos(tmp_path, monkeypatch, capsys):
    """
    Each `remove:`/`replace:` is a linear pass over the expanded lines, so an unbounded
    number of modifiers (e.g., 100k lines pasted into `inline_stack`) would make
    resolution O(M * N) and stall the server. Verifies the MAX_TOTAL_MODIFIERS budget
    caps applied modifiers, absent targets are skipped cheaply, and in-budget modifiers
    still apply in written order.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)
    for name in ("a", "b", "c"):
        (prompts_dir / f"{name}.txt").write_text(name, encoding="utf-8")

    loader = PromptStackLoader()

    # 1. In-budget modifiers still chain in written order.
    raw = loader._resolve_paths(str(prompts_dir), "", "a.txt\nb.txt\nreplace:a.txt|c.txt\nremove:b.txt")
    files = loader._build_final_file_list(raw, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files] == ["c.txt"]
    capsys.readouterr()

    # 2. Huge numbers of non-matching and alternating matching modifiers stay bounded.
    flood = "\n".join(
        ["a.txt"] * 2000
        + [f"remove:missing_{i}.txt" for i in range(20000)]
        + ["replace:a.txt|b.txt", "replace:b.txt|a.txt"] * 5000
    )
    start = time.time()
    raw_flood = loader._resolve_paths(str(prompts_dir), "", flood)
    PromptStackLoader.IS_CHANGED("", "", flood)
    elapsed = time.time() - start
    assert "Maximum total modifiers" in capsys.readouterr().out
    assert elapsed < 20
    assert len(raw_flood) == 2000


def test_security_windows_pre_realpath_unc_device_and_drive_relative(tmp_path, monkeypatch):
    """
    Verifies that `_is_safe_path`, `_resolve_base_dir`, and `stack_file` block UNC paths,
    Windows device namespaces, drive-relative paths (`C:file.txt`), and extended reserved
    device names (`CONIN$`, `CONOUT$`, `COM¹`, trailing space/dot variants) BEFORE calling
    `os.path.realpath` (preventing SMB/NTLM leaks and device-open hangs on Windows).
    """
    from py.prompt_stack_loader import _is_safe_path, _is_safe_relative_ref

    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)
    base_str = str(prompts_dir)

    real_realpath = os.path.realpath
    realpath_calls = []

    def tracking_realpath(p, *args, **kwargs):
        realpath_calls.append(p)
        return real_realpath(p, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", tracking_realpath)

    pre_realpath_blocked = [
        "\\\\evil-server\\share\\secret.txt",
        "//evil-server/share/secret.txt",
        "/\\evil-server/share/secret.txt",
        "\\/evil-server\\share\\secret.txt",
        "\\\\.\\CON",
        "\\\\.",
        "\\\\?\\C:\\Windows\\win.ini",
        "C:valid.txt",
        "C:",
        os.path.join(base_str, "CON.txt"),
        os.path.join(base_str, "CON .txt"),
        os.path.join(base_str, "CON."),
        os.path.join(base_str, "CONIN$.txt"),
        os.path.join(base_str, "CONOUT$.txt"),
        os.path.join(base_str, "COM\u00b9.txt"),
        os.path.join(base_str, "LPT\u00b3.txt"),
        os.path.join(base_str, "valid.txt:$DATA"),
    ]
    for bad in pre_realpath_blocked:
        realpath_calls.clear()
        assert _is_safe_path(base_str, bad) is False, f"Expected blocked: {bad!r}"
        assert realpath_calls == [], f"os.path.realpath must NOT be called before rejecting {bad!r}"

    # Verify `_is_safe_relative_ref` blocks same-drive, absolute, UNC, ADS, and reserved paths
    for bad_rel in [
        "C:valid.txt",
        "C:\\valid.txt",
        "/etc/passwd",
        "\\windows\\win.ini",
        "//evil/share/x.txt",
        "valid.txt:$DATA",
        "valid.txt\x00.txt",
        "sub/CONIN$.txt",
        "sub/CON .txt",
        "sub/COM\u00b2.txt",
    ]:
        assert _is_safe_relative_ref(bad_rel) is False, f"Expected invalid relative ref: {bad_rel!r}"

    # `_resolve_base_dir` rejects UNC / device / drive-relative without touching realpath
    for bad_base in ["\\\\evil\\share", "//evil/share", "\\\\.\\CON", "C:prompts"]:
        realpath_calls.clear()
        assert PromptStackLoader._resolve_base_dir(bad_base) == str(prompts_dir)
        assert realpath_calls == []


def test_security_hardlink_cycle_detection(tmp_path, monkeypatch, capsys):
    """
    Verifies that circular imports formed via hard links (distinct paths sharing
    an inode, where `os.path.realpath` returns different strings) are detected.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)
    (prompts_dir / "leaf.txt").write_text("leaf", encoding="utf-8")

    orig = prompts_dir / "orig.stack.txt"
    hardlink = prompts_dir / "hardlink.stack.txt"
    orig.write_text("leaf.txt\nimport:hardlink.stack.txt\n", encoding="utf-8")
    os.link(orig, hardlink)

    loader = PromptStackLoader()
    raw = loader._resolve_paths(str(prompts_dir), "orig.stack.txt", "")
    files = loader._build_final_file_list(raw, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files] == ["leaf.txt"]
    assert "Circular import detected" in capsys.readouterr().out


def test_logic_inline_stack_scoping_and_current_dir_isolation(tmp_path, monkeypatch):
    """
    Verifies that when `stack_file` lives in a subdirectory:
    1. `stack_file`'s `./` paths resolve relative to `stack_file`'s directory, while
       `inline_stack`'s `./` and `random:.` paths resolve relative to `base_dir`.
    2. `remove:`/`replace:` inside `stack_file` do NOT leak out to mutate lines added
       in `inline_stack`.
    3. `inline_stack` can still `remove:`/`replace:` lines and `import:` blocks from `stack_file`,
       and can sequentially re-import `stack_file` without a false cycle error.
    4. `IS_CHANGED` also anchors `inline_stack` relative to `base_dir`.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)
    sub_dir = prompts_dir / "projects" / "scene"
    sub_dir.mkdir(parents=True)

    (prompts_dir / "root_leaf.txt").write_text("root leaf", encoding="utf-8")
    (sub_dir / "local_leaf.txt").write_text("local leaf", encoding="utf-8")
    (sub_dir / "drop_me.txt").write_text("drop me", encoding="utf-8")
    (sub_dir / "sub_inc.stack.txt").write_text("local_leaf.txt\n", encoding="utf-8")
    (prompts_dir / "local_leaf.txt").write_text("base local leaf", encoding="utf-8")

    (sub_dir / "01.stack.txt").write_text(
        "import:./sub_inc.stack.txt\n"
        "./local_leaf.txt\n"
        "./drop_me.txt\n"
        "remove:./drop_me.txt\n",
        encoding="utf-8",
    )

    inline_stack = "\n".join([
        "./root_leaf.txt",
        "projects/scene/drop_me.txt",
        "remove:import:projects/scene/sub_inc.stack.txt",
    ])

    loader = PromptStackLoader()
    raw = loader._resolve_paths(str(prompts_dir), "projects/scene/01.stack.txt", inline_stack)
    files = loader._build_final_file_list(raw, str(prompts_dir), SeededRandom(0))

    assert files == [
        str(sub_dir / "local_leaf.txt"),
        str(prompts_dir / "root_leaf.txt"),
        str(sub_dir / "drop_me.txt"),
    ]

    # Sequential reuse: `inline_stack` importing the same file as `stack_file` is not cyclic
    (prompts_dir / "reusable.stack.txt").write_text("root_leaf.txt\n", encoding="utf-8")
    raw_reuse = loader._resolve_paths(str(prompts_dir), "reusable.stack.txt", "import:reusable.stack.txt")
    files_reuse = loader._build_final_file_list(raw_reuse, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files_reuse] == ["root_leaf.txt", "root_leaf.txt"]

    # IS_CHANGED must detect edits to `./root_leaf.txt` referenced in `inline_stack`
    h1 = PromptStackLoader.IS_CHANGED(str(prompts_dir), "projects/scene/01.stack.txt", "./root_leaf.txt")
    mtime = time.time() + 15.0
    os.utime(prompts_dir / "root_leaf.txt", (mtime, mtime))
    h2 = PromptStackLoader.IS_CHANGED(str(prompts_dir), "projects/scene/01.stack.txt", "./root_leaf.txt")
    assert h1 != h2


def test_logic_direct_and_nested_import_modifiers_and_ordering(tmp_path, monkeypatch):
    """
    Verifies that:
    1. `remove:import:shared` removes BOTH direct and nested occurrences of `import:shared`.
    2. Modifier execution preserves exact written order across mixed `import:` and file swaps.
    3. `replace:import:s2|import:s1_s2` (where `s1_s2` itself imports `s2`) replaces both
       direct and nested `import:s2` exactly once without double-expanding.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)
    for name in ["s1.txt", "s2.txt", "c.txt", "a.txt", "b.txt", "x.txt", "y.txt"]:
        (prompts_dir / name).write_text(name, encoding="utf-8")

    (prompts_dir / "shared.stack.txt").write_text("s2.txt\n", encoding="utf-8")
    (prompts_dir / "child.stack.txt").write_text("import:shared.stack.txt\nc.txt\n", encoding="utf-8")
    (prompts_dir / "s1_s2.stack.txt").write_text("s1.txt\nimport:shared.stack.txt\n", encoding="utf-8")
    (prompts_dir / "a.stack.txt").write_text("a.txt\n", encoding="utf-8")
    (prompts_dir / "b.stack.txt").write_text("b.txt\n", encoding="utf-8")

    loader = PromptStackLoader()

    # 1. Remove both direct and nested `import:shared.stack.txt`
    stack1 = "import:shared.stack.txt\nimport:child.stack.txt\nremove:import:shared.stack.txt"
    files1 = loader._build_final_file_list(
        loader._resolve_paths(str(prompts_dir), "", stack1), str(prompts_dir), SeededRandom(0)
    )
    assert [os.path.basename(f) for f in files1] == ["c.txt"]

    # 2. Exact modifier ordering: `import:a -> x`, `x -> import:b`, `import:b -> y`
    stack2 = "\n".join([
        "import:a.stack.txt",
        "import:b.stack.txt",
        "replace:import:a.stack.txt|x.txt",
        "replace:x.txt|import:b.stack.txt",
        "replace:import:b.stack.txt|y.txt",
    ])
    files2 = loader._build_final_file_list(
        loader._resolve_paths(str(prompts_dir), "", stack2), str(prompts_dir), SeededRandom(0)
    )
    assert [os.path.basename(f) for f in files2] == ["y.txt", "y.txt"]

    # 3. Self-wrapping import replacement (`shared -> s1_s2` where `s1_s2` imports `shared`)
    #    must expand both direct and nested `shared` exactly once (no double expansion).
    stack3 = "\n".join([
        "import:shared.stack.txt",
        "import:child.stack.txt",
        "replace:import:shared.stack.txt|import:s1_s2.stack.txt",
    ])
    files3 = loader._build_final_file_list(
        loader._resolve_paths(str(prompts_dir), "", stack3), str(prompts_dir), SeededRandom(0)
    )
    assert [os.path.basename(f) for f in files3] == ["s1.txt", "s2.txt", "s1.txt", "s2.txt", "c.txt"]


def test_logic_lazy_replace_import_evaluation(tmp_path, monkeypatch, capsys):
    """
    Verifies that `replace:unmatched|import:<other>` does NOT open or warn about `<other>`
    and does NOT pollute `IS_CHANGED` when the target never matches.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)
    (prompts_dir / "keep.txt").write_text("keep", encoding="utf-8")
    unused_stack = prompts_dir / "unused.stack.txt"
    unused_stack.write_text("keep.txt\n", encoding="utf-8")

    inline = "\n".join([
        "keep.txt",
        "replace:nonexistent.txt|import:missing_never_opened.stack.txt",
        "replace:nonexistent.txt|import:unused.stack.txt",
    ])
    loader = PromptStackLoader()
    raw = loader._resolve_paths(str(prompts_dir), "", inline)
    files = loader._build_final_file_list(raw, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files] == ["keep.txt"]
    out = capsys.readouterr().out
    assert "is not a valid file" not in out

    # Modifying `unused.stack.txt` must not invalidate `IS_CHANGED` since it was never matched
    h1 = PromptStackLoader.IS_CHANGED(str(prompts_dir), "", inline)
    mtime = time.time() + 25.0
    os.utime(unused_stack, (mtime, mtime))
    h2 = PromptStackLoader.IS_CHANGED(str(prompts_dir), "", inline)
    assert h1 == h2


def test_logic_utf8_bom_corrupt_encoding_and_random_edge_cases(tmp_path, monkeypatch, capsys):
    """
    Verifies:
    1. Stack files and prompt files saved with UTF-8 BOM (`\\ufeff`) parse directives cleanly.
    2. Non-UTF-8 binary stack files are skipped gracefully without raising UnicodeDecodeError.
    3. Empty `random:` warns and skips cleanly.
    4. `_get_random_file` and `IS_CHANGED` ignore broken symlinks, directory symlinks named
       `*.txt`, and symlinks escaping the target `random:` folder into sibling folders.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)
    (prompts_dir / "ok.txt").write_bytes("\ufeffhello world".encode("utf-8"))
    (prompts_dir / "drop.txt").write_text("drop", encoding="utf-8")
    (prompts_dir / "bom_child.stack.txt").write_bytes("\ufeffok.txt\ndrop.txt\n".encode("utf-8"))
    (prompts_dir / "bom_root.stack.txt").write_bytes(
        "\ufeffimport:bom_child.stack.txt\nremove:drop.txt\n".encode("utf-8")
    )
    (prompts_dir / "corrupt.stack.txt").write_bytes(b"\xff\xfe\x80\x81invalid")

    loader = PromptStackLoader()
    prompt, _, _ = loader.process(
        seed=0,
        base_dir=str(prompts_dir),
        stack_file="bom_root.stack.txt",
        inline_stack="import:corrupt.stack.txt\nrandom:",
    )
    assert prompt == "hello world"
    out = capsys.readouterr().out
    assert "Could not read imported stack file" in out
    assert "Empty 'random:' directive" in out

    # `random:` directory with broken symlink, directory symlink named `*.txt`, and sibling symlink
    pool = prompts_dir / "pool"
    sibling = prompts_dir / "sibling"
    pool.mkdir()
    sibling.mkdir()
    (pool / "real.txt").write_text("real", encoding="utf-8")
    sibling_target = sibling / "sibling_file.txt"
    sibling_target.write_text("sibling", encoding="utf-8")

    os.symlink(pool / "does_not_exist.txt", pool / "broken.txt")
    os.symlink(sibling, pool / "dir_symlink.txt")
    os.symlink(sibling_target, pool / "escaped_sibling.txt")

    rng = SeededRandom(0)
    for _ in range(20):
        chosen = loader._get_random_file(str(pool), rng)
        assert os.path.basename(chosen) == "real.txt"

    # Modifying `sibling_target` must not change `IS_CHANGED` for `random:pool`
    h1 = PromptStackLoader.IS_CHANGED(str(prompts_dir), "", "random:pool")
    mtime = time.time() + 30.0
    os.utime(sibling_target, (mtime, mtime))
    h2 = PromptStackLoader.IS_CHANGED(str(prompts_dir), "", "random:pool")
    assert h1 == h2
