import os
import re
import glob
from typing import List, Tuple
from collections import UserDict
import sys
import importlib
import importlib.util
import types

adaptive_prompts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "comfyui-adaptiveprompts"))

# The base pack's modules live in a directory named `py`, which is not a unique
# top-level package name: several other custom node packs ship their own `py/`
# directory too. A regular package (one with an __init__.py) is resolved ahead of any
# namespace portion regardless of sys.path order, and the winner is cached in
# sys.modules for the life of the process, so importing the base pack as bare `py`
# can silently yield another pack's modules. Load it under a private name instead.
_BASE_PACKAGE = "_adaptiveprompts_core"


def _import_base_generator():
    """Return the base pack's py/generator module without relying on the name `py`."""
    base_py_dir = os.path.join(adaptive_prompts_dir, "py")
    generator_path = os.path.join(base_py_dir, "generator.py")

    # Reuse a copy the host application already executed, when the base pack is loaded.
    # Prefer the host pack's own module over our synthetic fallback if both exist.
    fallback_module = None
    for name, module in list(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if not origin:
            continue
        try:
            if os.path.samefile(origin, generator_path):
                if not name.startswith(_BASE_PACKAGE):
                    return module
                fallback_module = module
        except OSError:
            continue

    if fallback_module is not None:
        return fallback_module

    if not os.path.isfile(generator_path):
        raise ImportError("The 'comfyui-adaptiveprompts-extensions' node requires the 'comfyui-adaptiveprompts' custom node to be installed.")

    if _BASE_PACKAGE not in sys.modules:
        package = types.ModuleType(_BASE_PACKAGE)
        package.__path__ = [base_py_dir]
        sys.modules[_BASE_PACKAGE] = package

    return importlib.import_module(f"{_BASE_PACKAGE}.generator")


def _sync_base_config():
    """Keep the base pack's config singleton synchronized when this extension loads before the base pack."""
    base_config_path = os.path.join(adaptive_prompts_dir, "py", "config.py")
    synth_config = sys.modules.get(f"{_BASE_PACKAGE}.config")
    host_config = None

    for name, module in list(sys.modules.items()):
        if name.startswith(_BASE_PACKAGE):
            continue
        origin = getattr(module, "__file__", None)
        if not origin:
            continue
        try:
            if os.path.samefile(origin, base_config_path):
                host_config = module
                break
        except OSError:
            continue

    if synth_config is not None:
        if host_config is not None and hasattr(host_config, "config_instance"):
            synth_config.config_instance = host_config.config_instance
        elif hasattr(synth_config, "config_instance") and hasattr(synth_config.config_instance, "load"):
            config_json = getattr(synth_config, "CONFIG_PATH", os.path.join(adaptive_prompts_dir, "config.json"))
            if os.path.isfile(config_json):
                synth_config.config_instance.load()


_generator = _import_base_generator()
SeededRandom = _generator.SeededRandom
DEFAULT_WILDCARD_ROOT = _generator.DEFAULT_WILDCARD_ROOT

try:
    evaluate_prompt_core = _generator.evaluate_prompt_core
except AttributeError:
    _resolve_wildcards = _generator.resolve_wildcards

    def evaluate_prompt_core(prompt: str, rng, wildcard_dir: str, resolved_vars: dict, hide_comments: bool = True) -> str:
        """Fallback implementation for older core versions (like the 'main' branch)."""
        comment_blocks = re.findall(r"##(.*?)##", prompt, flags=re.DOTALL)
        for block in comment_blocks:
            _ = _resolve_wildcards(block, rng, wildcard_dir, _resolved_vars=resolved_vars)
        if hide_comments:
            prompt = re.sub(r"##.*?##", "", prompt, flags=re.DOTALL)
        return _resolve_wildcards(prompt, rng, wildcard_dir, _resolved_vars=resolved_vars)

# --- Context Helper Functions ---
def _is_safe_path(base: str, path: str) -> bool:
    """
    Validates that `path` is securely contained within `base`.

    This function prevents path traversal (e.g., using `../../`) and arbitrary file
    read vulnerabilities. It fully resolves all symlinks via `os.path.realpath`
    to ensure that symlink-based evasion attacks cannot escape the sandbox boundary.
    It returns True if the path is safe, and False if it attempts to escape.
    """
    try:
        real_base = os.path.realpath(base)
        real_path = os.path.realpath(path)
        return os.path.commonpath([real_base, real_path]) == real_base
    except ValueError:
        return False

def _ensure_bucket_dict(bucket_like):
    if bucket_like is None:
        return {}
    if isinstance(bucket_like, dict):
        out = {}
        for k, v in bucket_like.items():
            out[str(k)] = str(v)
        return out
    if isinstance(bucket_like, (list, tuple, set)):
        out = {}
        i = 0
        for v in bucket_like:
            out[f"__combined_{i}"] = str(v)
            i += 1
        return out
    return {"__combined_0": str(bucket_like)}

def _normalize_input_context(ctx):
    if not ctx:
        return {}
    normalized = {}
    for var, bucket in ctx.items():
        normalized[var] = _ensure_bucket_dict(bucket)
    return normalized

class _OverrideBucket(UserDict):
    def __init__(self, initial_data):
        super().__init__()
        self.data = dict(initial_data)
        self.cleared = False

    def __setitem__(self, key, value):
        if not self.cleared:
            self.data.clear()
            self.cleared = True
        super().__setitem__(key, value)
# --------------------------------

DEFAULT_PROMPT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "prompts")
)

class PromptStackLoader:
    """
    Sequentially loads, parses, and evaluates a stack of text files to build a complex prompt and context.

    This node acts as a batch processor that orchestrates prompt generation across multiple files:
    1. It resolves file paths, expands nested stack imports (import:), processes inline commands (remove:, replace:, random:), and collects a final list of files.
    2. For each file, it extracts and isolates LoRA tags (`<lora:...>`) directly from the raw string.
    3. It delegates the remaining text to the core evaluation logic to cleanly handle wildcards,
       comment blocks (`##...##`), and formatting.
    4. Variables defined in one file (context) are accumulated and sequentially merged (or overridden)
       into the next, allowing complex cross-file prompt building.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "base_dir": ("STRING", {
                    "default": "",
                    "tooltip": "The root directory for your stack. Relative paths anchor to this custom node's root directory. Leave empty to default to the 'prompts' folder."
                }),
                "stack_file": ("STRING", {
                    "default": "",
                    "tooltip": "Optional. Path to a .txt file containing your stack. Relative paths (e.g., 'stacks/my_stack.txt') resolve against base_dir."
                }),
                "inline_stack": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "List of text paths to load sequentially (relative resolve against base_dir).\n- Use 'import:path' to include another stack file.\n- Use 'random:folder' to pick a random file.\n- Prefix with 'remove:' to exclude a path.\n- Use 'replace:old|new' to swap a path in-place.\n- Lines starting with # are ignored."
                }),
                "override_context": ("BOOLEAN", {
                    "default": False,
                    "label_on": "Override",
                    "label_off": "Merge",
                    "tooltip": "Override: New variables replace old ones. Merge: New variables are combined into existing buckets."
                }),
                "seed": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 0xffffffffffffffff,
                    "tooltip": "Controls the random file selection for 'random:' paths and internal wildcards."
                }),
            },
            "optional": {
                "context": ("DICT", {
                    "tooltip": "Optional upstream context to build upon."
                }),
            },
        }

    @classmethod
    def _resolve_base_dir(cls, base_dir: str) -> str:
        """
        Resolves the user's `base_dir` input into an absolute path and validates it.

        Security: The `base_dir` establishes the foundational sandbox for all internal
        stack lines (e.g., character traits or replacements). It is strictly validated
        to ensure it does not escape the overall extension folder (`node_root`).
        If it fails the security check, it safely defaults to the 'prompts' folder.
        """
        resolved_base_dir = base_dir.strip()
        node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if not resolved_base_dir:
            resolved_base_dir = os.path.join(node_root, "prompts")
        else:
            resolved_base_dir = os.path.normpath(os.path.join(node_root, resolved_base_dir))

        if not _is_safe_path(node_root, resolved_base_dir):
            print(f"[Adaptive Prompts] Security Warning: base_dir '{base_dir}' attempts path traversal. Defaulting to 'prompts'.")
            resolved_base_dir = os.path.join(node_root, "prompts")
        return resolved_base_dir

    MAX_IMPORT_DEPTH = 32
    _IMPORT_BEGIN_PREFIX = "__ps_begin_import__:"
    _IMPORT_END_PREFIX = "__ps_end_import__:"

    @classmethod
    def _normalize_match(cls, p: str) -> str:
        """
        Normalizes slashes and redundant relative segments (e.g., `./`) for
        cross-platform matching across file paths, `random:`, and `import:`.
        """
        p = p.strip()
        for prefix in ("random:", "import:"):
            if p.startswith(prefix):
                core = p[len(prefix):].strip().replace("\\", "/")
                if not core:
                    return prefix
                core = os.path.normpath(core).replace("\\", "/")
                return f"{prefix}{core}"
        return os.path.normpath(p.replace("\\", "/")).replace("\\", "/")

    @classmethod
    def _parse_modifier(cls, clean_line: str):
        """
        Parses a `remove:<target>` or `replace:<old>|<new>` line into a tuple
        `(mod_type, normalized_target_key, new_path_or_none)`, or returns None
        if the line is not a valid modifier command.
        """
        if clean_line.startswith("remove:"):
            path_to_exclude = clean_line[len("remove:"):].strip()
            if path_to_exclude:
                return ("remove", cls._normalize_match(path_to_exclude), None)
            return None
        if clean_line.startswith("replace:"):
            parts = clean_line[len("replace:"):].split("|", 1)
            if len(parts) == 2:
                old_path = parts[0].strip()
                new_path = parts[1].strip()
                if old_path and new_path:
                    return ("replace", cls._normalize_match(old_path), new_path)
            return None
        return None

    @classmethod
    def _is_import_marker(cls, line: str) -> bool:
        return line.startswith(cls._IMPORT_BEGIN_PREFIX) or line.startswith(cls._IMPORT_END_PREFIX)

    @classmethod
    def _apply_modifiers_to_lines(
        cls,
        lines: List[str],
        modifiers: List[Tuple[str, str, str]],
        resolved_base_dir: str,
        active_stack: List[str] = None,
        visited_stack_files: List[str] = None,
        expand_replacement_imports: bool = True,
        log_unused: bool = True,
    ) -> List[str]:
        """
        Applies `remove:` and `replace:` modifiers sequentially to `lines`.

        Supported combinations:
        1. File / `random:` targets (`remove:a.txt`, `replace:a.txt|b.txt`, `replace:random:dir|a.txt`):
           Matches individual lines in-place.
        2. Replacing a line with an `import:` (`replace:a.txt|import:sub.stack.txt`):
           Expands the replacement `import:` in-place with full sandbox, cycle, and depth checks.
        3. Targeting an `import:` directive (`remove:import:sub.stack.txt`,
           `replace:import:sub.stack.txt|import:alt.stack.txt`, `replace:import:sub.stack.txt|a.txt`):
           Matches either an unexpanded `import:` line (pre-expansion) or the entire block of
           lines produced by an inner `import:` (delimited by internal begin/end markers).
        4. Self-replacement (`replace:a.txt|a.txt`) and round-trip swaps (`a -> b` then `b -> a`):
           Each modifier executes in a single linear pass over `current_lines`, so self-swaps
           and round-trip swaps terminate in O(N) without infinite loops.
        """
        if active_stack is None:
            active_stack = []

        current_lines = list(lines)
        for mod_type, target_key, new_path in modifiers:
            matched = False
            next_lines = []

            # Prepare replacement payload once if this is a `replace:` modifier.
            replacement_payload = []
            if mod_type == "replace":
                if expand_replacement_imports and new_path.strip().startswith("import:"):
                    replacement_payload = cls._expand_import_lines(
                        [new_path],
                        resolved_base_dir,
                        active_stack=active_stack,
                        visited_stack_files=visited_stack_files,
                        apply_scope_modifiers=True,
                        log_unused=log_unused,
                    )
                else:
                    replacement_payload = [new_path]

            if target_key.startswith("import:"):
                begin_tag = f"{cls._IMPORT_BEGIN_PREFIX}{target_key}"
                end_tag = f"{cls._IMPORT_END_PREFIX}{target_key}"
                block_depth = 0

                for item in current_lines:
                    stripped = item.strip()
                    # Case A: Matches an already-expanded inner import block via boundary markers
                    if stripped == begin_tag:
                        if block_depth == 0:
                            matched = True
                            if mod_type == "replace":
                                next_lines.extend(replacement_payload)
                        block_depth += 1
                        continue
                    if stripped == end_tag:
                        if block_depth > 0:
                            block_depth -= 1
                            continue
                    if block_depth > 0:
                        continue

                    # Case B: Matches an unexpanded `import:` line directly
                    if not cls._is_import_marker(stripped) and cls._normalize_match(stripped) == target_key:
                        matched = True
                        if mod_type == "replace":
                            next_lines.extend(replacement_payload)
                        continue

                    next_lines.append(item)
            else:
                for item in current_lines:
                    stripped = item.strip()
                    if cls._is_import_marker(stripped):
                        next_lines.append(item)
                        continue
                    if cls._normalize_match(stripped) == target_key:
                        matched = True
                        if mod_type == "replace":
                            next_lines.extend(replacement_payload)
                    else:
                        next_lines.append(item)

            if not matched and log_unused:
                if mod_type == "remove":
                    print(f"[Adaptive Prompts] Debug: Exclusion target '{target_key}' did not match any loaded paths.")
                elif mod_type == "replace":
                    print(f"[Adaptive Prompts] Debug: Swap target '{target_key}' -> '{new_path}' did not match any loaded paths.")

            current_lines = next_lines

        return current_lines

    @classmethod
    def _expand_import_lines(
        cls,
        raw_lines: List[str],
        resolved_base_dir: str,
        active_stack: List[str] = None,
        visited_stack_files: List[str] = None,
        apply_scope_modifiers: bool = False,
        log_unused: bool = True,
    ) -> List[str]:
        """
        Recursively expands `import:<relative_path>` directives in-place with subtree-scoped
        modifier evaluation.

        How nested scopes work:
        1. Pre-Import Pass: Any modifier in the current scope that targets a direct `import:`
           line in the same scope (e.g., `remove:import:foo.txt` or `replace:import:foo.txt|...`)
           is applied before opening files from disk.
        2. Recursive Import Pass: Each remaining `import:` line is validated against:
           - `MAX_IMPORT_DEPTH` (32) to prevent deep recursion errors.
           - `_is_safe_path(resolved_base_dir, ...)` to enforce the `base_dir` sandbox.
           - `active_stack` (`os.path.realpath`) to prevent circular imports (`A -> A`, `A -> B -> A`)
             while allowing sequential non-cyclic reuse of the same snippet.
           - The 5MB file size limit.
           Each imported file is resolved with `apply_scope_modifiers=True`, meaning its own
           internal `remove:` and `replace:` commands only affect its own subtree and cannot
           leak into sibling imports or later lines in the parent stack.
        3. Post-Import Pass: When `apply_scope_modifiers=True`, the current scope's remaining
           `remove:` and `replace:` modifiers are applied to the expanded lines of this scope.
           When `apply_scope_modifiers=False` (used by `_resolve_paths` for the root scope),
           the root scope's post-import modifiers are preserved at the end of the returned list
           to be applied by `_build_final_file_list`.
        """
        if active_stack is None:
            active_stack = []

        content_and_imports = []
        raw_modifier_lines = []
        parsed_modifiers = []

        for line in raw_lines:
            clean_line = line.strip()
            if not clean_line or clean_line.startswith("#"):
                continue
            mod = cls._parse_modifier(clean_line)
            if mod is not None:
                raw_modifier_lines.append(clean_line)
                parsed_modifiers.append(mod)
            else:
                content_and_imports.append(line)

        # Step 1: Pre-Import interception for modifiers targeting a direct `import:` line
        # in the current scope (avoids reading a removed/replaced stack file from disk).
        remaining_raw_modifiers = []
        remaining_parsed_modifiers = []
        direct_import_keys = {
            cls._normalize_match(l.strip())
            for l in content_and_imports
            if l.strip().startswith("import:")
        }

        for raw_mod, parsed_mod in zip(raw_modifier_lines, parsed_modifiers):
            mod_type, target_key, new_path = parsed_mod
            if target_key.startswith("import:") and target_key in direct_import_keys:
                content_and_imports = cls._apply_modifiers_to_lines(
                    content_and_imports,
                    [parsed_mod],
                    resolved_base_dir,
                    active_stack=active_stack,
                    visited_stack_files=visited_stack_files,
                    expand_replacement_imports=False,
                    log_unused=log_unused,
                )
                # Refresh direct_import_keys in case a replace swapped `import:A` -> `import:B`
                direct_import_keys = {
                    cls._normalize_match(l.strip())
                    for l in content_and_imports
                    if l.strip().startswith("import:")
                }
            else:
                remaining_raw_modifiers.append(raw_mod)
                remaining_parsed_modifiers.append(parsed_mod)

        # Step 2: Expand all remaining `import:` lines in-place.
        expanded = []
        for line in content_and_imports:
            clean_line = line.strip()
            if not clean_line.startswith("import:"):
                expanded.append(line)
                continue

            import_target = clean_line[len("import:"):].strip().replace("\\", "/")
            if not import_target:
                print("[Adaptive Prompts] Warning: Empty 'import:' directive. Skipping.")
                continue

            if len(active_stack) >= cls.MAX_IMPORT_DEPTH:
                print(f"[Adaptive Prompts] Warning: Maximum import depth ({cls.MAX_IMPORT_DEPTH}) exceeded at '{import_target}'. Skipping.")
                continue

            resolved_import = os.path.normpath(os.path.join(resolved_base_dir, import_target))
            if not _is_safe_path(resolved_base_dir, resolved_import):
                print(f"[Adaptive Prompts] Security Warning: import '{import_target}' attempts path traversal. Skipping.")
                continue

            if not os.path.isfile(resolved_import):
                print(f"[Adaptive Prompts] Warning: Imported stack file '{resolved_import}' is not a valid file. Skipping.")
                continue

            real_import = os.path.realpath(resolved_import)
            if real_import in active_stack:
                print(f"[Adaptive Prompts] Warning: Circular import detected for '{import_target}'. Skipping.")
                continue

            try:
                if os.path.getsize(resolved_import) > 5 * 1024 * 1024:
                    print(f"[Adaptive Prompts] Warning: File '{resolved_import}' exceeds 5MB limit. Skipping.")
                    continue
                with open(resolved_import, "r", encoding="utf-8") as f:
                    imported_lines = f.readlines()
            except OSError as e:
                print(f"[Adaptive Prompts] Warning: Could not read imported stack file '{resolved_import}': {e}")
                continue

            if visited_stack_files is not None:
                visited_stack_files.append(resolved_import)

            norm_import_key = cls._normalize_match(clean_line)
            child_expanded = cls._expand_import_lines(
                imported_lines,
                resolved_base_dir,
                active_stack=active_stack + [real_import],
                visited_stack_files=visited_stack_files,
                apply_scope_modifiers=True,
                log_unused=log_unused,
            )
            expanded.append(f"{cls._IMPORT_BEGIN_PREFIX}{norm_import_key}")
            expanded.extend(child_expanded)
            expanded.append(f"{cls._IMPORT_END_PREFIX}{norm_import_key}")

        # Step 3: Apply this scope's post-import modifiers to the expanded lines of this scope.
        if apply_scope_modifiers:
            return cls._apply_modifiers_to_lines(
                expanded,
                remaining_parsed_modifiers,
                resolved_base_dir,
                active_stack=active_stack,
                visited_stack_files=visited_stack_files,
                expand_replacement_imports=True,
                log_unused=log_unused,
            )

        return expanded + remaining_raw_modifiers

    @classmethod
    def IS_CHANGED(cls, base_dir, stack_file, inline_stack, override_context=False, seed=0, context=None):
        import hashlib
        import os
        m = hashlib.sha256()

        resolved_base_dir = cls._resolve_base_dir(base_dir)
        node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

        config_path = os.path.join(adaptive_prompts_dir, "config.json")
        if os.path.isfile(config_path):
            try:
                m.update(str(os.path.getmtime(config_path)).encode("utf-8"))
            except OSError:
                pass

        raw_root_lines = []
        root_active_stack = []
        visited_stack_files = []
        if stack_file:
            stack_path = os.path.normpath(os.path.join(resolved_base_dir, stack_file))
            if _is_safe_path(node_root, stack_path) and os.path.exists(stack_path):
                m.update(str(os.path.getmtime(stack_path)).encode("utf-8"))
                try:
                    if os.path.getsize(stack_path) > 5 * 1024 * 1024:
                        print(f"[Adaptive Prompts] Warning: File '{stack_path}' exceeds 5MB limit. Skipping read in IS_CHANGED.")
                    else:
                        with open(stack_path, "r", encoding="utf-8") as f:
                            raw_root_lines.extend(f.readlines())
                        root_active_stack.append(os.path.realpath(stack_path))
                except OSError:
                    pass
        if inline_stack:
            raw_root_lines.extend(inline_stack.splitlines())

        paths = cls._expand_import_lines(
            raw_root_lines,
            resolved_base_dir,
            active_stack=root_active_stack,
            visited_stack_files=visited_stack_files,
            apply_scope_modifiers=True,
            log_unused=False,
        )

        for imp_path in visited_stack_files:
            try:
                m.update(str(os.path.getmtime(imp_path)).encode("utf-8"))
            except OSError:
                pass

        for line in paths:
            line = line.strip()
            if not line or line.startswith("#") or cls._is_import_marker(line) or line.startswith("remove:"):
                continue
            if line.startswith("replace:"):
                parts = line[8:].split("|", 1)
                if len(parts) == 2 and parts[1].strip():
                    line = parts[1].strip()
                else:
                    continue
            is_random = line.startswith("random:")
            path_str = line[len("random:"):].strip() if is_random else line

            resolved_path = os.path.normpath(os.path.join(resolved_base_dir, path_str))

            if _is_safe_path(resolved_base_dir, resolved_path) and os.path.exists(resolved_path):
                if not os.path.isdir(resolved_path):
                    m.update(str(os.path.getmtime(resolved_path)).encode("utf-8"))
                else:
                    for root, dirs, files in os.walk(resolved_path):
                        # Filter out "hidden" directories (used for local overrides/drafts)
                        dirs[:] = [d for d in dirs if not (d.startswith('_') or d.startswith('.'))]
                        for file in files:
                            # Filter out "hidden" files so they don't trigger cache invalidation
                            if file.endswith(".txt") and not (file.startswith('_') or file.startswith('.')):
                                try:
                                    fpath = os.path.join(root, file)
                                    m.update(str(os.path.getmtime(fpath)).encode("utf-8"))
                                except OSError:
                                    pass
        return m.hexdigest()

    RETURN_TYPES = ("STRING", "DICT", "STRING")
    RETURN_NAMES = ("prompt", "context", "lora_string")
    FUNCTION = "process"
    CATEGORY = "adaptiveprompts/generation"

    def _resolve_paths(self, base_dir: str, stack_file: str, inline_stack: str) -> List[str]:
        """
        Resolves and loads the contents of the `stack_file` and `inline_stack`,
        recursively expanding any `import:` directives in-place.

        Security Exception: The `stack_file` itself acts as an orchestrator and may
        legitimately live outside the `base_dir` (e.g., in a sibling 'stacks' folder).
        Therefore, `stack_file` is sandboxed against the broader `node_root` (the
        extension directory), NOT the `base_dir`.

        However, the internal lines parsed *from* the stack file (including `import:`
        directives) remain strictly sandboxed against the `base_dir` during the final
        file resolution phase.
        """
        raw_root_lines = []
        root_active_stack = []
        if stack_file:
            # Use the variable 'stack_path' for stack configuration files
            stack_path = os.path.normpath(os.path.join(base_dir, stack_file))
            node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            if not _is_safe_path(node_root, stack_path):
                print(f"[Adaptive Prompts] Security Warning: stack_file '{stack_file}' attempts path traversal. Skipping.")
            else:
                try:
                    if os.path.getsize(stack_path) > 5 * 1024 * 1024:
                        print(f"[Adaptive Prompts] Warning: File '{stack_path}' exceeds 5MB limit. Skipping.")
                    else:
                        with open(stack_path, "r", encoding="utf-8") as f:
                            raw_root_lines.extend(f.readlines())
                        root_active_stack.append(os.path.realpath(stack_path))
                except OSError as e:
                    print(f"[Adaptive Prompts] Warning: Could not read stack file '{stack_path}': {e}")

        if inline_stack:
            raw_root_lines.extend(inline_stack.splitlines())

        if not raw_root_lines:
            return []

        return self._expand_import_lines(
            raw_root_lines,
            base_dir,
            active_stack=root_active_stack,
            apply_scope_modifiers=True,
            log_unused=True,
        )

    def _get_random_file(self, dir_path: str, rng: SeededRandom) -> str:
        candidates = []
        for root, dirs, files in os.walk(dir_path):
            # Exclude "hidden" directories to avoid picking up overrides or drafts
            dirs[:] = [d for d in dirs if not (d.startswith('_') or d.startswith('.'))]
            for file in files:
                # Exclude "hidden" files
                if file.endswith(".txt") and not (file.startswith('_') or file.startswith('.')):
                    candidates.append(os.path.join(root, file))
        if candidates:
            candidates.sort() # Ensure deterministic order across different OSs
            return rng.choice(candidates)
        return ""

    def _build_final_file_list(self, raw_lines: List[str], resolved_base_dir: str, rng: SeededRandom) -> List[str]:
        # Separate content lines and any remaining unapplied modifiers (e.g., if
        # `_build_final_file_list` is invoked directly with raw lines in tests).
        content_lines = []
        modifiers = []
        for line in raw_lines:
            clean_line = line.strip()
            if not clean_line or clean_line.startswith("#"):
                continue
            mod = self._parse_modifier(clean_line)
            if mod is not None:
                modifiers.append(mod)
            else:
                content_lines.append(clean_line)

        if modifiers:
            valid_lines = self._apply_modifiers_to_lines(
                content_lines,
                modifiers,
                resolved_base_dir,
                active_stack=[],
                expand_replacement_imports=True,
                log_unused=True,
            )
        else:
            valid_lines = content_lines

        files_to_process = []
        for line in valid_lines:
            line = line.strip()
            # Filter empty lines, comments, and internal import boundary markers
            if not line or line.startswith("#") or self._is_import_marker(line):
                continue

            is_random = line.startswith("random:")
            path_str = line[len("random:"):].strip() if is_random else line

            resolved_path = os.path.normpath(os.path.join(resolved_base_dir, path_str))
            if not _is_safe_path(resolved_base_dir, resolved_path):
                print(f"[Adaptive Prompts] Security Warning: path '{path_str}' attempts path traversal. Skipping.")
                continue

            if is_random and os.path.isdir(resolved_path):
                chosen_file = self._get_random_file(resolved_path, rng)
                if chosen_file:
                    files_to_process.append(chosen_file)
            else:
                if os.path.isfile(resolved_path):
                    files_to_process.append(resolved_path)
                else:
                    print(f"[Adaptive Prompts] Warning: Path '{resolved_path}' is not a valid file.")

        return files_to_process

    def _resolve_switch_macros(self, text: str, context: dict) -> str:
        def split_pipes(s):
            parts, curr = [], []
            paren_depth = brace_depth = bracket_depth = 0
            for c in s:
                if c == '(': paren_depth += 1
                elif c == ')': paren_depth -= 1
                elif c == '{': brace_depth += 1
                elif c == '}': brace_depth -= 1
                elif c == '[': bracket_depth += 1
                elif c == ']': bracket_depth -= 1
                elif c == '|' and paren_depth == 0 and brace_depth == 0 and bracket_depth == 0:
                    parts.append("".join(curr))
                    curr = []
                    continue
                curr.append(c)
            parts.append("".join(curr))
            return parts

        def split_case(s):
            paren_depth = brace_depth = bracket_depth = 0
            for i, c in enumerate(s):
                if c == '(': paren_depth += 1
                elif c == ')': paren_depth -= 1
                elif c == '{': brace_depth += 1
                elif c == '}': brace_depth -= 1
                elif c == '[': bracket_depth += 1
                elif c == ']': bracket_depth -= 1
                elif c == ':' and paren_depth == 0 and brace_depth == 0 and bracket_depth == 0:
                    return s[:i], s[i+1:]
            return s, None

        out = []
        idx = 0
        while idx < len(text):
            start = text.find("{ps_switch(", idx)
            if start == -1:
                out.append(text[idx:])
                break

            out.append(text[idx:start])

            brace_count = 0
            end = -1
            for i in range(start, len(text)):
                if text[i] == '{': brace_count += 1
                elif text[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end = i
                        break

            if end == -1:
                out.append(text[start:start+11])
                idx = start + 11
                continue

            block = text[start+1:end]
            choices = split_pipes(block)
            first_choice = choices[0].strip()

            m = re.match(r"^ps_switch\s*\((.*?)\)$", first_choice, re.DOTALL)
            if not m:
                out.append(text[start:end+1])
                idx = end + 1
                continue

            var_name = m.group(1).strip()
            var_values = [str(v).strip() for v in context.get(var_name, {}).values()]

            matched_res = ""
            default_res = ""
            has_default = False

            accumulated_cases = []
            default_pending = False

            for choice in choices[1:]:
                case_val, res_val = split_case(choice)
                if res_val is None:
                    # Fallthrough case: e.g. "a" in "| a | b: result"
                    case_val_stripped = case_val.strip()
                    if case_val_stripped == "default":
                        default_pending = True
                    else:
                        accumulated_cases.append(case_val_stripped)
                else:
                    case_val = case_val.strip()
                    if case_val == "default":
                        default_res = res_val
                        has_default = True
                        accumulated_cases = [] # Prevent leapfrog leakage
                        default_pending = False
                    else:
                        accumulated_cases.append(case_val)
                        if any(k in var_values for k in accumulated_cases):
                            matched_res = res_val
                            break

                        if default_pending:
                            default_res = res_val
                            has_default = True
                            default_pending = False

                        accumulated_cases = [] # Reset after a result block

            final_res = matched_res if matched_res else (default_res if has_default else "")
            if "{ps_switch(" in final_res:
                final_res = self._resolve_switch_macros(final_res, context)

            out.append(final_res)
            idx = end + 1

        return "".join(out)

    def _process_single_file(self, filepath: str, current_context: dict, rng: SeededRandom, override_context: bool) -> tuple[str, List[str]]:
        lora_regex = re.compile(r"<lora:[^>]+>")
        try:
            if os.path.getsize(filepath) > 5 * 1024 * 1024:
                print(f"[Adaptive Prompts] Warning: File '{filepath}' exceeds 5MB limit. Skipping.")
                return "", []
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            print(f"[Adaptive Prompts] Warning: Failed to read file '{filepath}': {e}")
            return "", []

        # Extract LoRA tags directly from raw string
        found_loras = lora_regex.findall(content)
        cleaned_content = lora_regex.sub("", content)

        # Apply switch macros
        cleaned_content = self._resolve_switch_macros(cleaned_content, current_context)

        if override_context:
            for k, v in current_context.items():
                if isinstance(v, dict):
                    current_context[k] = _OverrideBucket(v)

        # Replicability and Random Isolation:
        # We generate a unique 'sub_seed' for this specific file using our master RNG.
        # This isolates wildcard evaluation per file so adding wildcards to file A doesn't shift file B.
        sub_seed = rng.randint(0, 0xffffffffffffffff)
        sub_rng = SeededRandom(sub_seed)

        # Evaluate text using core logic
        evaluated_text = evaluate_prompt_core(
            cleaned_content,
            rng=sub_rng,
            wildcard_dir=DEFAULT_WILDCARD_ROOT,
            resolved_vars=current_context,
            hide_comments=True
        )

        return evaluated_text.strip(), found_loras

    def process(self, seed: int, base_dir: str, stack_file: str, inline_stack: str, override_context: bool = False, context: dict = None):
        _sync_base_config()
        rng = SeededRandom(seed)

        resolved_base_dir = self._resolve_base_dir(base_dir)

        raw_lines = self._resolve_paths(resolved_base_dir, stack_file, inline_stack)
        files_to_process = self._build_final_file_list(raw_lines, resolved_base_dir, rng)

        current_context = {}
        if context is not None:
            current_context = _normalize_input_context(context)

        lora_tags = []
        accumulated_prompts = []

        for filepath in files_to_process:
            evaluated_text, file_loras = self._process_single_file(filepath, current_context, rng, override_context)
            if evaluated_text:
                accumulated_prompts.append(evaluated_text)
            lora_tags.extend(file_loras)

        # Ensure context buckets are normalized
        for k, v in list(current_context.items()):
            if isinstance(v, _OverrideBucket):
                current_context[k] = v.data
            elif type(v) is not dict:
                if isinstance(v, dict):
                    current_context[k] = dict(v)
                else:
                    current_context[k] = _ensure_bucket_dict(v)

        lora_string = " ".join(lora_tags)
        final_prompt_string = ", ".join(accumulated_prompts)

        return (final_prompt_string, current_context, lora_string)
