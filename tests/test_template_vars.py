import os
import sys
import time
import types

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


def test_set_basic_prototype_and_multi_slot_substitution(tmp_path, monkeypatch):
    """
    Verifies `set:<name>=<value>` with `${var}`, `$var`, `$$` literal escape,
    multi-line substitution from one variable, and expanding a variable into
    a `random:` command.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    char_dir = prompts_dir / "subjects" / "hero"
    char_dir.mkdir(parents=True)
    (char_dir / "identity.txt").write_text("hero_id", encoding="utf-8")
    (char_dir / "outfit.txt").write_text("hero_outfit", encoding="utf-8")

    action_dir = prompts_dir / "actions" / "reading"
    action_dir.mkdir(parents=True)
    (action_dir / "pose.txt").write_text("reading_pose", encoding="utf-8")

    (prompts_dir / "footer_v1.txt").write_text("masterpiece", encoding="utf-8")
    (prompts_dir / "cost_$5.txt").write_text("literal_dollar", encoding="utf-8")

    stacks_dir = prompts_dir / "stacks"
    stacks_dir.mkdir()
    (stacks_dir / "_proto.stack.txt").write_text(
        "subjects/${subject}/identity.txt\n"
        "subjects/${subject}/outfit.txt\n"
        "${action}\n"
        "cost_$$5.txt\n"
        "$footer\n",
        encoding="utf-8",
    )

    (stacks_dir / "instance.stack.txt").write_text(
        "set:subject=hero\n"
        "set:action=random:actions/reading\n"
        "set:footer=footer_v1.txt\n"
        "import:./_proto.stack.txt\n",
        encoding="utf-8",
    )

    loader = PromptStackLoader()
    raw = loader._resolve_paths(str(prompts_dir), "stacks/instance.stack.txt", "")
    files = loader._build_final_file_list(raw, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == [
        "identity.txt",
        "outfit.txt",
        "pose.txt",
        "cost_$5.txt",
        "footer_v1.txt",
    ]


def test_set_shell_style_scoping_inheritance_and_no_leakage(tmp_path, monkeypatch):
    """
    Verifies shell-like scoping rules:
    1. `set:` applies only to lines and imports *below* it in the same file.
    2. Sequential re-assignment passes the active snapshot to each `import:`.
    3. Nested imports inherit variables and can override them for their own subtree.
    4. Child overrides NEVER leak back up to the parent or sideways to sibling imports.
    5. `set:` in `stack_file` does not leak into `inline_stack`.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    for name in ["v_init.txt", "v_one.txt", "v_two.txt", "v_child.txt", "v_grand.txt", "v_inline.txt"]:
        (prompts_dir / name).write_text(name, encoding="utf-8")

    # Grandchild uses inherited `${x}`
    (prompts_dir / "grand.stack.txt").write_text("${x}\n", encoding="utf-8")

    # Child uses inherited `${x}`, then overrides `x` for its own grandchild and itself
    (prompts_dir / "child_override.stack.txt").write_text(
        "${x}\n"
        "set:x=v_child.txt\n"
        "import:grand.stack.txt\n"
        "set:secret_child_var=v_grand.txt\n",
        encoding="utf-8",
    )

    # Sibling uses `${x}` (must still see parent's `x`, NOT child_override's `v_child.txt`)
    (prompts_dir / "sibling.stack.txt").write_text("${x}\n", encoding="utf-8")

    # Root stack tests top-to-bottom order, sequential re-assignment, inheritance, and non-leakage
    (prompts_dir / "root.stack.txt").write_text(
        "set:x=v_init.txt\n"
        "${x}\n"
        "set:x=v_one.txt\n"
        "import:child_override.stack.txt\n"
        "import:sibling.stack.txt\n"
        "set:x=v_two.txt\n"
        "import:sibling.stack.txt\n",
        encoding="utf-8",
    )

    loader = PromptStackLoader()
    # inline_stack referencing `${x}` from stack_file must NOT see stack_file's `x`
    raw = loader._resolve_paths(
        str(prompts_dir),
        "root.stack.txt",
        "${x}\nset:x=v_inline.txt\n${x}",
    )
    files = loader._build_final_file_list(raw, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == [
        "v_init.txt",   # root before reassignment
        "v_one.txt",    # child_override before its local set:x=v_child.txt
        "v_child.txt",  # grand inside child_override
        "v_one.txt",    # sibling (no upward/sideways leak from child_override)
        "v_two.txt",    # second sibling call after root reassigned x=v_two.txt
        "v_inline.txt", # inline_stack after its own set:x (first `${x}` in inline was undefined & skipped)
    ]


def test_set_chained_definition_and_single_pass_non_recursive(tmp_path, monkeypatch):
    """
    Verifies chained `set:` definitions (`set:b=${a}/sub`) and guarantees single-pass
    non-recursive substitution (a variable value containing `$other` is NOT re-expanded).
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    sub_dir = prompts_dir / "group" / "item"
    sub_dir.mkdir(parents=True)
    (sub_dir / "a.txt").write_text("a", encoding="utf-8")
    (prompts_dir / "literal_$other.txt").write_text("lit", encoding="utf-8")
    (prompts_dir / "other.txt").write_text("other", encoding="utf-8")

    inline_stack = "\n".join([
        "set:root_dir=group",
        "set:target_dir=${root_dir}/item",
        "${target_dir}/a.txt",
        "set:other=other.txt",
        "set:esc=$$other",
        "literal_${esc}.txt",
    ])

    loader = PromptStackLoader()
    raw = loader._resolve_paths(str(prompts_dir), "", inline_stack)
    files = loader._build_final_file_list(raw, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == ["a.txt", "literal_$other.txt"]


def test_set_missing_and_invalid_placeholders_loud_warnings_and_prompt_files_untouched(tmp_path, monkeypatch, capsys):
    """
    Verifies:
    1. Undefined variables log a clear warning naming the variable and file and skip the line.
    2. Malformed placeholders (`${unclosed`, lone `$`) log a warning and skip the line.
    3. Invalid `set:` syntax (`set:no_eq`, `set:=v`, `set:1bad=v`) logs a warning and skips.
    4. Prompt `.txt` file contents are NEVER template-substituted by stack variables.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    # Prompt module contains literal `$subject` and `${subject}` — stack variables must NOT
    # be substituted into prompt `.txt` file bodies (only stack lines).
    (prompts_dir / "ok.txt").write_text("prompt with literal $subject text", encoding="utf-8")
    (prompts_dir / "proto.stack.txt").write_text(
        "${undefined_var}\n"
        "${unclosed\n"
        "lone_$_sign.txt\n"
        "ok.txt\n",
        encoding="utf-8",
    )

    inline_stack = "\n".join([
        "set:no_equals",
        "set:=missing_name",
        "set:123invalid=val",
        "set:subject=should_not_touch_prompt_body",
        "import:proto.stack.txt",
    ])

    loader = PromptStackLoader()
    prompt, ctx, lora = loader.process(
        seed=0,
        base_dir=str(prompts_dir),
        stack_file="",
        inline_stack=inline_stack,
        override_context=True,
    )

    out = capsys.readouterr().out
    assert "Invalid 'set:' directive" in out
    assert "Undefined stack variable '$undefined_var' in 'proto.stack.txt'" in out
    assert "Invalid template syntax in 'proto.stack.txt'" in out
    assert "not a valid file" not in out
    assert "should_not_touch_prompt_body" not in prompt
    assert prompt == "prompt with literal $subject text"


def test_set_interaction_with_replace_remove_and_relative_paths(tmp_path, monkeypatch):
    """
    Verifies that `${var}` works inside `replace:`, `remove:`, and `./` relative paths,
    and that `replace:old|import:proto` passes the active `set:` variables into `proto`.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    sub_dir = prompts_dir / "scene"
    sub_dir.mkdir()
    for name in ["old.txt", "new.txt", "drop.txt", "night_light.txt"]:
        (sub_dir / name).write_text(name, encoding="utf-8")

    (sub_dir / "_light_proto.stack.txt").write_text("./${mood}_light.txt\n", encoding="utf-8")
    (sub_dir / "_base.stack.txt").write_text(
        "./old.txt\n"
        "./drop.txt\n",
        encoding="utf-8",
    )
    (sub_dir / "run.stack.txt").write_text(
        "set:mood=night\n"
        "set:to_drop=drop\n"
        "import:./_base.stack.txt\n"
        "remove:./${to_drop}.txt\n"
        "replace:./old.txt|import:./_light_proto.stack.txt\n",
        encoding="utf-8",
    )

    loader = PromptStackLoader()
    raw = loader._resolve_paths(str(prompts_dir), "scene/run.stack.txt", "")
    files = loader._build_final_file_list(raw, str(prompts_dir), SeededRandom(0))

    assert [os.path.basename(f) for f in files] == ["night_light.txt"]


def test_set_security_sandbox_dos_and_injection_blocked(tmp_path, monkeypatch, capsys):
    """
    Verifies security invariants under `set:` / `${var}`:
    1. Traversal (`../`), Windows device names (`CON.txt`), NTFS ADS (`:$DATA`), UNC, and
       drive-relative paths injected through `${var}` are all blocked by `_is_safe_path`.
    2. `set:` cannot be injected indirectly via `${cmd}` or via `replace:a|set:x=1`.
    3. Spoofed internal markers via `${marker}` are stripped.
    4. Billion-laughs chained `set:` is bounded by `MAX_VAR_VALUE_LEN`, and variable count
       is bounded by `MAX_TEMPLATE_VARS`.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    project_a = prompts_dir / "arch" / "project_a"
    project_b = prompts_dir / "arch" / "project_b"
    project_a.mkdir(parents=True)
    project_b.mkdir(parents=True)

    (project_a / "safe.txt").write_text("safe", encoding="utf-8")
    (project_b / "secret.txt").write_text("secret", encoding="utf-8")
    (project_b / "secret.stack.txt").write_text("secret.txt\n", encoding="utf-8")

    (project_a / "proto.stack.txt").write_text(
        "import:${evil_import}\n"
        "${evil_file}\n"
        "${evil_dev}\n"
        "${evil_ads}\n"
        "${evil_unc}\n"
        "${evil_drive}\n"
        "${inject_set}\n"
        "${spoof_marker}\n"
        "safe.txt\n",
        encoding="utf-8",
    )

    inline_stack = "\n".join([
        "set:evil_import=../project_b/secret.stack.txt",
        "set:evil_file=../project_b/secret.txt",
        "set:evil_dev=CON.txt",
        "set:evil_ads=safe.txt:$DATA",
        "set:evil_unc=\\\\evil-server\\share\\x.txt",
        "set:evil_drive=C:secret.txt",
        "set:inject_set=set:hijacked=1",
        "set:spoof_marker=__ps_begin_import__:import:fake.stack.txt",
        "import:proto.stack.txt",
        "replace:safe.txt|set:x=1",
    ])

    loader = PromptStackLoader()
    resolved_base = loader._resolve_base_dir("prompts/arch/project_a")
    raw = loader._resolve_paths(resolved_base, "", inline_stack)
    files = loader._build_final_file_list(raw, resolved_base, SeededRandom(0))

    assert [os.path.basename(f) for f in files] == ["safe.txt"]
    out = capsys.readouterr().out
    assert "Security Warning:" in out
    assert "Command directive cannot be injected" in out
    assert "Invalid modifier directive" in out

    # Billion-laughs chained `set:` DoS guard (MAX_VAR_VALUE_LEN)
    laughs_lines = ["set:v0=" + ("a" * 64)]
    for i in range(1, 20):
        laughs_lines.append(f"set:v{i}=${{v{i-1}}}${{v{i-1}}}")
    laughs_lines.append("safe.txt")
    raw_laughs = loader._resolve_paths(resolved_base, "", "\n".join(laughs_lines))
    files_laughs = loader._build_final_file_list(raw_laughs, resolved_base, SeededRandom(0))
    assert [os.path.basename(f) for f in files_laughs] == ["safe.txt"]
    assert "character limit" in capsys.readouterr().out

    # Variable count DoS guard (MAX_TEMPLATE_VARS)
    flood_lines = [f"set:k{i}=v{i}" for i in range(PromptStackLoader.MAX_TEMPLATE_VARS + 10)]
    flood_lines.append("safe.txt")
    raw_flood = loader._resolve_paths(resolved_base, "", "\n".join(flood_lines))
    files_flood = loader._build_final_file_list(raw_flood, resolved_base, SeededRandom(0))
    assert [os.path.basename(f) for f in files_flood] == ["safe.txt"]
    assert "Maximum template variables" in capsys.readouterr().out


def test_set_is_changed_cache_invalidation(tmp_path, monkeypatch):
    """
    Verifies `IS_CHANGED` hashes files resolved through `set:` / `${var}` templates.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    target_a = prompts_dir / "target_a.txt"
    target_b = prompts_dir / "target_b.txt"
    target_a.write_text("a", encoding="utf-8")
    target_b.write_text("b", encoding="utf-8")

    (prompts_dir / "proto.stack.txt").write_text("${target}\n", encoding="utf-8")

    h1 = PromptStackLoader.IS_CHANGED(
        base_dir=str(prompts_dir),
        stack_file="",
        inline_stack="set:target=target_a.txt\nimport:proto.stack.txt",
    )

    # 1. Editing the resolved target file invalidates cache
    time.sleep(0.01)
    new_mtime = time.time() + 10.0
    os.utime(target_a, (new_mtime, new_mtime))
    h2 = PromptStackLoader.IS_CHANGED(
        base_dir=str(prompts_dir),
        stack_file="",
        inline_stack="set:target=target_a.txt\nimport:proto.stack.txt",
    )
    assert h1 != h2

    # 2. Switching `set:target` to another file changes the hash
    h3 = PromptStackLoader.IS_CHANGED(
        base_dir=str(prompts_dir),
        stack_file="",
        inline_stack="set:target=target_b.txt\nimport:proto.stack.txt",
    )
    assert h2 != h3


def test_debug_log_prints_resolved_stack(tmp_path, monkeypatch, capsys):
    """
    Verifies the optional `debug_log` toggle prints the final resolved stack list
    when enabled and stays silent when disabled (default).
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    (prompts_dir / "one.txt").write_text("one", encoding="utf-8")
    (prompts_dir / "two.txt").write_text("two", encoding="utf-8")
    (prompts_dir / "proto.stack.txt").write_text("one.txt\n${second}\n", encoding="utf-8")

    inline = "set:second=two.txt\nimport:proto.stack.txt"
    loader = PromptStackLoader()

    # Default (debug_log=False): no resolved stack dump
    loader.process(seed=0, base_dir=str(prompts_dir), stack_file="", inline_stack=inline)
    assert "Resolved stack" not in capsys.readouterr().out

    # Enabled (debug_log=True): prints numbered base_dir-relative resolved paths
    loader.process(seed=0, base_dir=str(prompts_dir), stack_file="", inline_stack=inline, debug_log=True)
    out = capsys.readouterr().out
    assert "Resolved stack (2 files" in out
    assert "01. one.txt" in out
    assert "02. two.txt" in out


def test_set_bash_style_default_values(tmp_path, monkeypatch):
    """
    Verifies `${var:-default}` Bash-style default fallback:
    1. Unset variable falls back to default.
    2. Explicitly empty variable (`set:var=`) falls back to default.
    3. Non-empty caller value overrides default.
    4. Empty default (`${optional:-}`) omits an optional line cleanly when unset.
    5. Prototype-level `set:env=${env:-dry.txt}` sets a default across multiple lines.
    6. Default string is literal (non-recursive: `${unset:-$other}` does not expand `$other`).
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    env_dir = prompts_dir / "globals" / "env"
    env_dir.mkdir(parents=True)
    for name in ["dry.txt", "wet.txt"]:
        (env_dir / name).write_text(name, encoding="utf-8")
    (prompts_dir / "extra.txt").write_text("extra", encoding="utf-8")
    (prompts_dir / "lit_$other.txt").write_text("lit", encoding="utf-8")
    (prompts_dir / "other.txt").write_text("other", encoding="utf-8")

    (prompts_dir / "proto.stack.txt").write_text(
        "set:resolved_env=${env:-dry.txt}\n"
        "globals/env/${resolved_env}\n"
        "${optional_extra:-}\n"
        "lit_${unset_lit:-$other}.txt\n",
        encoding="utf-8",
    )

    loader = PromptStackLoader()

    # Case 1: Caller sets nothing -> uses `dry.txt`, omits optional line, keeps literal `$other`
    raw1 = loader._resolve_paths(str(prompts_dir), "", "set:other=other.txt\nimport:proto.stack.txt")
    files1 = loader._build_final_file_list(raw1, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files1] == ["dry.txt", "lit_$other.txt"]

    # Case 2: Caller sets `env=` (empty) -> still falls back to `dry.txt`
    raw2 = loader._resolve_paths(str(prompts_dir), "", "set:env=\nimport:proto.stack.txt")
    files2 = loader._build_final_file_list(raw2, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files2] == ["dry.txt", "lit_$other.txt"]

    # Case 3: Caller sets `env=wet.txt` and `optional_extra=extra.txt`
    raw3 = loader._resolve_paths(
        str(prompts_dir),
        "",
        "set:env=wet.txt\nset:optional_extra=extra.txt\nimport:proto.stack.txt",
    )
    files3 = loader._build_final_file_list(raw3, str(prompts_dir), SeededRandom(0))
    assert [os.path.basename(f) for f in files3] == ["wet.txt", "extra.txt", "lit_$other.txt"]


def test_heterogeneous_injection_and_template_hardening(tmp_path, monkeypatch, capsys):
    """
    Verifies defenses against heterogeneous-language injection & template holes:
    1. Pipe (`|`) delimiter injection inside `replace:${old}|${new}` cannot split operands.
    2. Command injection (`remove:`, `replace:`, `set:`) via `${slot}` is blocked on plain lines.
    3. Malicious default values inside prototypes (`../`, `CON.txt`, `:$DATA`) hit `_is_safe_path`.
    4. Python `str.format`-style attribute traversal (`${x.__class__}`) is rejected as invalid syntax.
    5. Single-line amplification (`${big}` repeated many times) is capped by `MAX_LINE_LEN`.
    """
    prompts_dir = _setup_mock_root(tmp_path, monkeypatch)

    project_a = prompts_dir / "arch" / "project_a"
    project_b = prompts_dir / "arch" / "project_b"
    project_a.mkdir(parents=True)
    project_b.mkdir(parents=True)

    (project_a / "a.txt").write_text("a", encoding="utf-8")
    (project_a / "b.txt").write_text("b", encoding="utf-8")
    (project_a / "hijack.txt").write_text("hijack", encoding="utf-8")
    (project_b / "secret.txt").write_text("secret", encoding="utf-8")

    # 1. Pipe delimiter injection: `old` contains `a.txt|hijack.txt`, must NOT match `a.txt`
    #    and hijack the replacement into `hijack.txt`.
    pipe_stack = "\n".join([
        "a.txt",
        "b.txt",
        "set:old=a.txt|hijack.txt",
        "set:new=b.txt",
        "replace:${old}|${new}",
    ])
    loader = PromptStackLoader()
    resolved_base = loader._resolve_base_dir("prompts/arch/project_a")
    files_pipe = loader._build_final_file_list(
        loader._resolve_paths(resolved_base, "", pipe_stack), resolved_base, SeededRandom(0)
    )
    assert [os.path.basename(f) for f in files_pipe] == ["a.txt", "b.txt"]

    # 2. Command injection via `${slot}` (`remove:`, `replace:`, `set:`) must be blocked
    cmd_inject = "\n".join([
        "a.txt",
        "set:rm_cmd=remove:a.txt",
        "set:rep_cmd=replace:a.txt|hijack.txt",
        "set:set_cmd=set:x=1",
        "${rm_cmd}",
        "${rep_cmd}",
        "${set_cmd}",
    ])
    files_cmd = loader._build_final_file_list(
        loader._resolve_paths(resolved_base, "", cmd_inject), resolved_base, SeededRandom(0)
    )
    assert [os.path.basename(f) for f in files_cmd] == ["a.txt"]
    assert "Command directive cannot be injected" in capsys.readouterr().out

    # 3. Malicious prototype defaults & Python attribute-lookup syntax (`${x.__class__}`)
    (project_a / "bad_defaults.stack.txt").write_text(
        "${d1:-../project_b/secret.txt}\n"
        "import:${d2:-../project_b/secret.txt}\n"
        "${d3:-CON.txt}\n"
        "${d4:-a.txt:$DATA}\n"
        "${x.__class__}\n"
        "a.txt\n",
        encoding="utf-8",
    )
    files_def = loader._build_final_file_list(
        loader._resolve_paths(resolved_base, "bad_defaults.stack.txt", ""), resolved_base, SeededRandom(0)
    )
    assert [os.path.basename(f) for f in files_def] == ["a.txt"]
    out_def = capsys.readouterr().out
    assert "Security Warning:" in out_def
    assert "Invalid template syntax" in out_def

    # 4. Single-line amplification (`MAX_LINE_LEN`) and ReDoS sanity check
    big_val = "x" * 512
    amp_line = "${big}" * 10  # 5120 chars > MAX_LINE_LEN (2048)
    start = time.time()
    files_amp = loader._build_final_file_list(
        loader._resolve_paths(resolved_base, "", f"set:big={big_val}\n{amp_line}\na.txt"),
        resolved_base,
        SeededRandom(0),
    )
    assert [os.path.basename(f) for f in files_amp] == ["a.txt"]
    assert "character limit" in capsys.readouterr().out

    # Adversarial unclosed/repeated `$` input must finish in well under 1s (no ReDoS)
    redos_line = "${a:-" + ("x" * 5000)
    files_redos = loader._build_final_file_list(
        loader._resolve_paths(resolved_base, "", f"{redos_line}\na.txt"),
        resolved_base,
        SeededRandom(0),
    )
    assert (time.time() - start) < 2.0
    assert [os.path.basename(f) for f in files_redos] == ["a.txt"]
