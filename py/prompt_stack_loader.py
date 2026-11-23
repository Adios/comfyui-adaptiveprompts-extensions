import os
import re
import glob
from typing import List, Optional, Tuple
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
_WINDOWS_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "COM\u00b9", "COM\u00b2", "COM\u00b3",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    "LPT\u00b9", "LPT\u00b2", "LPT\u00b3",
})


def _is_device_namespace_path(path: str) -> bool:
    """Returns True for Windows device namespace paths (`\\\\.\\...`, `\\\\?\\...`, `//./...`, `//?/...`)."""
    norm = path.replace("\\", "/")
    return norm.startswith("//./") or norm.startswith("//?/") or norm in ("//.", "//?")


def _is_unc_path(path: str) -> bool:
    """Returns True for UNC network paths (`\\\\server\\share`, `//server/share`, or mixed `/\\`)."""
    return path.replace("\\", "/").startswith("//")


def _unc_share_root(path: str) -> str:
    """Extracts normalized `//server/share` prefix for UNC comparison, or empty string."""
    norm = path.replace("\\", "/")
    if not norm.startswith("//"):
        return ""
    parts = [p for p in norm[2:].split("/") if p]
    if len(parts) < 2:
        return ""
    return f"//{parts[0].lower()}/{parts[1].lower()}"


def _is_drive_relative_path(path: str) -> bool:
    """Returns True for Windows drive-relative paths like `C:foo.txt` or bare `C:` (no slash after colon)."""
    return len(path) >= 2 and path[1] == ":" and (len(path) == 2 or path[2] not in ("/", "\\"))


def _has_unsafe_relative_segments(rel_path: str) -> bool:
    """Checks a forward-slash relative path for colons or Windows reserved device names."""
    if ":" in rel_path:
        return True
    for segment in rel_path.split("/"):
        if not segment or segment in (".", ".."):
            continue
        stem = segment.split(".", 1)[0].rstrip(" .").strip().upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            return True
    return False


def _is_safe_relative_ref(path_str: str) -> bool:
    """
    Validates that a path written inside a stack file or `inline_stack` is strictly
    relative (never absolute, UNC, drive-qualified, ADS, null-byte, or reserved-device).
    Checked BEFORE `os.path.join` so Windows `ntpath.join` cannot silently strip
    same-drive prefixes (`C:file.txt`) or discard `base_dir` for absolute/UNC paths.
    """
    if not path_str or "\x00" in path_str or ":" in path_str:
        return False
    norm = path_str.replace("\\", "/")
    if norm.startswith("/"):
        return False
    return not _has_unsafe_relative_segments(norm)


def _path_in_active_stack(candidate: str, active_stack: List[str]) -> bool:
    """
    Returns True if `candidate` refers to the same file as any entry in `active_stack`.
    Uses normalized `realpath` plus `os.path.samefile` so cycles via symlinks,
    hard links, and case-insensitive filesystems (macOS APFS / Windows) are all caught.
    """
    norm_candidate = os.path.normcase(os.path.realpath(candidate))
    for active in active_stack:
        if os.path.normcase(active) == norm_candidate:
            return True
        try:
            if os.path.samefile(candidate, active):
                return True
        except OSError:
            continue
    return False


def _is_safe_path(base: str, path: str) -> bool:
    """
    Validates that `path` is securely contained within `base` across all platforms.

    Defends against:
    1. Path traversal (`../../`) and symlink escapes via `os.path.realpath`.
    2. Case-insensitive filesystem mismatches (Windows/macOS) via `os.path.normcase`.
    3. Null-byte injection (`\\x00`).
    4. UNC paths (`\\\\server\\share`, `//server/share`) and Windows device namespaces
       (`\\\\.\\`, `\\\\?\\`) BEFORE calling `os.path.realpath` (prevents SMB/NTLM leaks
       and device-open hangs on Windows).
    5. Windows drive-relative paths (`C:foo.txt`) and NTFS Alternate Data Streams
       (`file.txt:$DATA` or `file.txt:stream`) via colon rejection in relative subpaths.
    6. Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `CONIN$`, `CONOUT$`,
       `COM1`-`COM9`, `LPT1`-`LPT9`, superscript variants, with or without extensions or
       trailing spaces/dots) checked BEFORE `os.path.realpath`.
    """
    if not base or not path or "\x00" in base or "\x00" in path:
        return False
    if _is_device_namespace_path(base) or _is_device_namespace_path(path):
        return False
    if _is_drive_relative_path(base) or _is_drive_relative_path(path):
        return False
    if _is_unc_path(path):
        if not _is_unc_path(base):
            return False
        base_share = _unc_share_root(base)
        path_share = _unc_share_root(path)
        if not base_share or base_share != path_share:
            return False
    try:
        # Lexical pre-check BEFORE `os.path.realpath` so Windows never opens reserved
        # device names (e.g., `CON.txt`) or ADS streams during handle resolution.
        abs_base = os.path.abspath(base)
        abs_path = os.path.abspath(path)
        raw_tail = os.path.relpath(abs_path, abs_base).replace("\\", "/")
        if _has_unsafe_relative_segments(raw_tail):
            return False

        real_base = os.path.realpath(base)
        real_path = os.path.realpath(path)
        norm_base = os.path.normcase(real_base)
        norm_path = os.path.normcase(real_path)
        if os.path.commonpath([norm_base, norm_path]) != norm_base:
            return False

        rel_subpath = os.path.relpath(real_path, real_base).replace("\\", "/")
        if _has_unsafe_relative_segments(rel_subpath):
            return False

        return True
    except (ValueError, OSError):
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
                    "tooltip": "List of text paths to load sequentially (relative resolve against base_dir).\n- Use 'set:name=value' and '${name}' for template placeholders.\n- Use 'import:path' to include another stack file.\n- Use 'random:folder' to pick a random file.\n- Prefix with 'remove:' to exclude a path.\n- Use 'replace:old|new' to swap a path in-place.\n- Lines starting with # are ignored."
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
                "debug_log": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "When enabled, prints the final resolved stack file list to the console for debugging."
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
        raw_base_dir = base_dir.strip()
        node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if not raw_base_dir:
            resolved_base_dir = os.path.join(node_root, "prompts")
        elif (
            "\x00" in raw_base_dir
            or _is_device_namespace_path(raw_base_dir)
            or _is_unc_path(raw_base_dir)
            or _is_drive_relative_path(raw_base_dir)
        ):
            print(f"[Adaptive Prompts] Security Warning: base_dir '{base_dir}' attempts path traversal. Defaulting to 'prompts'.")
            return os.path.join(node_root, "prompts")
        else:
            resolved_base_dir = os.path.normpath(os.path.join(node_root, raw_base_dir.replace("\\", "/")))

        if not _is_safe_path(node_root, resolved_base_dir):
            print(f"[Adaptive Prompts] Security Warning: base_dir '{base_dir}' attempts path traversal. Defaulting to 'prompts'.")
            resolved_base_dir = os.path.join(node_root, "prompts")
        return resolved_base_dir

    MAX_IMPORT_DEPTH = 32
    MAX_TOTAL_IMPORTS = 512
    MAX_EXPANDED_LINES = 10000
    # Each modifier is a linear pass over up to MAX_EXPANDED_LINES lines, so the total
    # number of applied modifiers must be bounded to keep the cost O(MAX_TOTAL_MODIFIERS * N)
    # instead of letting a huge `inline_stack` / stack file stall the server (CPU DoS).
    MAX_TOTAL_MODIFIERS = 256
    MAX_TEMPLATE_VARS = 128
    MAX_VAR_VALUE_LEN = 1024
    MAX_LINE_LEN = 2048
    _VAR_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    # Single-pass O(N) template tokenizer (disjoint branches, no nested quantifiers -> ReDoS-safe).
    # Supports: `$$` (literal `$`), `$name`, `${name}`, and Bash-style `${name:-default}`.
    _TEMPLATE_TOKEN_RE = re.compile(
        r"\$(?:"
        r"(?P<escaped>\$)"
        r"|(?P<named>[a-zA-Z_][a-zA-Z0-9_]*)"
        r"|\{(?P<braced>[a-zA-Z_][a-zA-Z0-9_]*)(?::-(?P<default>[^}]*))?\}"
        r"|(?P<invalid>)"
        r")"
    )
    _IMPORT_BEGIN_PREFIX = "__ps_begin_import__:"
    _IMPORT_END_PREFIX = "__ps_end_import__:"

    @classmethod
    def _new_expansion_budget(cls) -> dict:
        return {
            "imports": 0,
            "modifiers": 0,
            "import_limit_warned": False,
            "line_limit_warned": False,
            "modifier_limit_warned": False,
        }

    @classmethod
    def _is_current_dir_relative(cls, path_str: str) -> bool:
        """
        Returns True if `path_str` is explicitly relative to the current stack file's
        directory (starts with `./` or `../`, or is `.` / `..`).
        Paths without a leading `./` or `../` resolve relative to `resolved_base_dir`.
        """
        norm_slashes = path_str.strip().replace("\\", "/")
        return (
            norm_slashes == "."
            or norm_slashes == ".."
            or norm_slashes.startswith("./")
            or norm_slashes.startswith("../")
        )

    @classmethod
    def _canonicalize_scoped_ref(cls, ref_str: str, current_dir: str, resolved_base_dir: str) -> str:
        """
        Resolves a file reference, `random:<dir>`, or `import:<stack>` that starts with
        `./` or `../` against `current_dir` (the directory of the stack file where the
        line is written) and converts it into a `resolved_base_dir`-relative path with
        normalized forward slashes for cross-platform compatibility.

        Security: This function only translates relative coordinates; it does NOT grant
        access. Any path that resolves outside `resolved_base_dir` retains a leading
        `../` relative to `resolved_base_dir` and is strictly blocked by `_is_safe_path`.
        """
        s = ref_str.strip()
        prefix = ""
        core = s
        for pfx in ("random:", "import:"):
            if s.startswith(pfx):
                prefix = pfx
                core = s[len(pfx):].strip()
                break

        if not core:
            return prefix

        if "\x00" in core or ":" in core:
            return f"{prefix}{core}"

        core_norm = core.replace("\\", "/")
        if current_dir and cls._is_current_dir_relative(core_norm):
            abs_target = os.path.normpath(os.path.join(current_dir, core_norm))
            try:
                rel_to_base = os.path.relpath(abs_target, resolved_base_dir).replace("\\", "/")
            except ValueError:
                # On Windows, relpath raises ValueError if paths span different drive letters
                rel_to_base = abs_target.replace("\\", "/")
            return f"{prefix}{rel_to_base}"

        return f"{prefix}{core_norm}"

    @classmethod
    def _canonicalize_scoped_line(cls, raw_line: str, current_dir: str, resolved_base_dir: str) -> str:
        """
        Canonicalizes any stack line (`<file>`, `random:`, `import:`, `remove:`, `replace:`)
        so that `./` and `../` paths are anchored to `current_dir`.
        """
        clean = raw_line.strip()
        if not clean or clean.startswith("#") or cls._is_import_marker(clean):
            return raw_line
        if clean.startswith("remove:"):
            target = clean[len("remove:"):].strip()
            if not target:
                return clean
            return f"remove:{cls._canonicalize_scoped_ref(target, current_dir, resolved_base_dir)}"
        if clean.startswith("replace:"):
            parts = clean[len("replace:"):].split("|", 1)
            if len(parts) == 2:
                old_p = parts[0].strip()
                new_p = parts[1].strip()
                if old_p and new_p:
                    c_old = cls._canonicalize_scoped_ref(old_p, current_dir, resolved_base_dir)
                    c_new = cls._canonicalize_scoped_ref(new_p, current_dir, resolved_base_dir)
                    return f"replace:{c_old}|{c_new}"
            return clean
        return cls._canonicalize_scoped_ref(clean, current_dir, resolved_base_dir)

    @classmethod
    def _normalize_match(cls, p: str) -> str:
        """
        Normalizes slashes and redundant relative segments (e.g., `./`) for
        cross-platform matching across file paths, `random:`, and `import:`.
        """
        p = p.strip()
        if not p:
            return ""
        for prefix in ("random:", "import:"):
            if p.startswith(prefix):
                core = p[len(prefix):].strip().replace("\\", "/")
                if not core:
                    return prefix
                core = os.path.normpath(core).replace("\\", "/")
                return f"{prefix}{core}"
        return os.path.normpath(p.replace("\\", "/")).replace("\\", "/")

    @classmethod
    def _is_set_line(cls, clean_line: str) -> bool:
        return clean_line.startswith("set:")

    @classmethod
    def _parse_set_directive(cls, clean_line: str) -> Optional[Tuple[str, str]]:
        """
        Parses a `set:<name>=<value>` line into `(name, raw_value)`, or returns None
        if the directive is malformed or the variable name is not a valid identifier.
        """
        if not clean_line.startswith("set:"):
            return None
        parts = clean_line[len("set:"):].split("=", 1)
        if len(parts) != 2:
            return None
        name = parts[0].strip()
        raw_value = parts[1].strip()
        if not name or not cls._VAR_NAME_RE.match(name):
            return None
        return (name, raw_value)

    @classmethod
    def _substitute_template_line(
        cls,
        text: str,
        template_vars: dict,
        source_label: str,
        log_warnings: bool = True,
    ) -> Optional[str]:
        """
        Performs a single non-recursive O(N) template substitution against `template_vars`:
        - `$var` and `${var}`: required variable (warns and skips line if undefined).
        - `${var:-default}`: optional variable with Bash-style default fallback used when
          `var` is unset or empty (`""`). `default` is treated as a literal string (never
          recursively expanded).
        - `$$`: literal `$`.

        Security:
        - Pure dictionary lookup (no `str.format`, no attribute/index lookup, no `eval`).
        - Strictly single-pass (substituted values and defaults are never re-parsed).
        - Disjoint linear-time regex (immune to ReDoS).
        - Rejects control characters (`\\n`, `\\r`, `\\x00`) and enforces `MAX_LINE_LEN`.
        """
        if len(text) > cls.MAX_LINE_LEN:
            if log_warnings:
                print(f"[Adaptive Prompts] Warning: Stack line exceeds {cls.MAX_LINE_LEN} character limit in '{source_label}'. Skipping.")
            return None

        if "$" not in text:
            return text

        out_parts = []
        total_len = 0
        pos = 0
        for m in cls._TEMPLATE_TOKEN_RE.finditer(text):
            literal_chunk = text[pos:m.start()]
            if literal_chunk:
                total_len += len(literal_chunk)
                if total_len > cls.MAX_LINE_LEN:
                    if log_warnings:
                        print(f"[Adaptive Prompts] Warning: Substituted line exceeds {cls.MAX_LINE_LEN} character limit in '{source_label}'. Skipping.")
                    return None
                out_parts.append(literal_chunk)

            if m.group("escaped") is not None:
                replacement = "$"
            elif m.group("named") is not None:
                var_name = m.group("named")
                if var_name not in template_vars:
                    if log_warnings:
                        print(f"[Adaptive Prompts] Warning: Undefined stack variable '${var_name}' in '{source_label}' (line: '{text}'). Skipping.")
                    return None
                replacement = template_vars[var_name]
            elif m.group("braced") is not None:
                var_name = m.group("braced")
                default_val = m.group("default")
                val = template_vars.get(var_name)
                if val is not None and val != "":
                    replacement = val
                elif default_val is not None:
                    replacement = default_val
                else:
                    if log_warnings:
                        print(f"[Adaptive Prompts] Warning: Undefined stack variable '${var_name}' in '{source_label}' (line: '{text}'). Skipping.")
                    return None
            else:
                if log_warnings:
                    print(f"[Adaptive Prompts] Warning: Invalid template syntax in '{source_label}' (line: '{text}'): invalid placeholder at position {m.start()}. Use '$$' for a literal '$'. Skipping.")
                return None

            total_len += len(replacement)
            if total_len > cls.MAX_LINE_LEN:
                if log_warnings:
                    print(f"[Adaptive Prompts] Warning: Substituted line exceeds {cls.MAX_LINE_LEN} character limit in '{source_label}'. Skipping.")
                return None
            out_parts.append(replacement)
            pos = m.end()

        tail = text[pos:]
        if tail:
            total_len += len(tail)
            if total_len > cls.MAX_LINE_LEN:
                if log_warnings:
                    print(f"[Adaptive Prompts] Warning: Substituted line exceeds {cls.MAX_LINE_LEN} character limit in '{source_label}'. Skipping.")
                return None
            out_parts.append(tail)

        substituted = "".join(out_parts)
        if "\n" in substituted or "\r" in substituted or "\x00" in substituted:
            if log_warnings:
                print(f"[Adaptive Prompts] Warning: Substituted line contains control characters in '{source_label}'. Skipping.")
            return None
        return substituted

    @classmethod
    def _is_modifier_line(cls, clean_line: str) -> bool:
        return clean_line.startswith("remove:") or clean_line.startswith("replace:")

    @classmethod
    def _is_valid_modifier_operand(cls, operand: str) -> bool:
        return bool(
            operand
            and not operand.startswith("#")
            and not cls._is_import_marker(operand)
            and not cls._is_modifier_line(operand)
            and not cls._is_set_line(operand)
        )

    @classmethod
    def _parse_modifier_parts(cls, mod_type: str, old_path: str, new_path: Optional[str] = None):
        """
        Validates already-split modifier operands into `(mod_type, normalized_target_key, new_path)`.
        Splitting before template substitution prevents delimiter (`|`) injection from variable values.
        """
        old_clean = old_path.strip()
        if mod_type == "remove":
            if not cls._is_valid_modifier_operand(old_clean):
                return None
            return ("remove", cls._normalize_match(old_clean), None)
        if mod_type == "replace" and new_path is not None:
            new_clean = new_path.strip()
            if cls._is_valid_modifier_operand(old_clean) and cls._is_valid_modifier_operand(new_clean):
                return ("replace", cls._normalize_match(old_clean), new_clean)
        return None

    @classmethod
    def _parse_modifier(cls, clean_line: str):
        """
        Parses a `remove:<target>` or `replace:<old>|<new>` line into a tuple
        `(mod_type, normalized_target_key, new_path_or_none)`, or returns None
        if the line is not a valid modifier command.

        Security: Rejects internal import boundary markers, nested modifier prefixes
        (`remove:`, `replace:`, `set:`), and comments as targets or replacement payloads
        to prevent marker spoofing and second-stage directive injection.
        """
        if clean_line.startswith("remove:"):
            return cls._parse_modifier_parts("remove", clean_line[len("remove:"):])
        if clean_line.startswith("replace:"):
            parts = clean_line[len("replace:"):].split("|", 1)
            if len(parts) == 2:
                return cls._parse_modifier_parts("replace", parts[0], parts[1])
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
        budget: dict = None,
        modifier_vars_list: Optional[List[dict]] = None,
    ) -> List[str]:
        """
        Applies `remove:` and `replace:` modifiers sequentially to `lines`.

        Supported combinations:
        1. File / `random:` targets (`remove:a.txt`, `replace:a.txt|b.txt`, `replace:random:dir|a.txt`):
           Matches individual lines in-place.
        2. Replacing a line with an `import:` (`replace:a.txt|import:sub.stack.txt`):
           Lazily expands the replacement `import:` on first match with full sandbox,
           cycle, depth, and resource-budget checks.
        3. Targeting an `import:` directive (`remove:import:sub.stack.txt`,
           `replace:import:sub.stack.txt|import:alt.stack.txt`, `replace:import:sub.stack.txt|a.txt`):
           Matches either an unexpanded `import:` line or the entire block of lines
           produced by an expanded `import:` (delimited by internal begin/end markers).
        4. Self-replacement (`replace:a.txt|a.txt`) and round-trip swaps (`a -> b` then `b -> a`):
           Each modifier executes in a single linear pass over `current_lines`, so self-swaps
           and round-trip swaps terminate in O(N) without infinite loops.
        """
        if active_stack is None:
            active_stack = []
        if budget is None:
            budget = cls._new_expansion_budget()

        # Memoize line normalization across passes: `os.path.normpath` per line per
        # modifier would otherwise dominate the cost of every pass.
        norm_cache = {}

        def _match_key(stripped_line: str) -> str:
            key = norm_cache.get(stripped_line)
            if key is None:
                key = cls._normalize_match(stripped_line)
                norm_cache[stripped_line] = key
            return key

        def _present_keys(line_list):
            keys = set()
            for line_item in line_list:
                s = line_item.strip()
                keys.add(s if cls._is_import_marker(s) else _match_key(s))
            return keys

        current_lines = list(lines)
        present = None
        for mod_idx, (mod_type, target_key, new_path) in enumerate(modifiers):
            if budget.get("modifiers", 0) >= cls.MAX_TOTAL_MODIFIERS:
                if not budget.get("modifier_limit_warned"):
                    print(f"[Adaptive Prompts] Warning: Maximum total modifiers ({cls.MAX_TOTAL_MODIFIERS}) exceeded. Skipping remaining 'remove:'/'replace:' directives.")
                    budget["modifier_limit_warned"] = True
                break
            budget["modifiers"] = budget.get("modifiers", 0) + 1

            # Fast path: a modifier whose target is absent cannot change the lines, so
            # skip the O(N) rebuild (the key set is only recomputed after a match).
            if present is None:
                present = _present_keys(current_lines)
            if target_key not in present and f"{cls._IMPORT_BEGIN_PREFIX}{target_key}" not in present:
                if log_unused:
                    if mod_type == "remove":
                        print(f"[Adaptive Prompts] Debug: Exclusion target '{target_key}' did not match any loaded paths.")
                    elif mod_type == "replace":
                        print(f"[Adaptive Prompts] Debug: Swap target '{target_key}' -> '{new_path}' did not match any loaded paths.")
                continue

            matched = False
            next_lines = []
            replacement_payload = None
            mod_vars = modifier_vars_list[mod_idx] if modifier_vars_list is not None else None

            def _get_replacement_payload():
                nonlocal replacement_payload
                if replacement_payload is not None:
                    return replacement_payload
                if expand_replacement_imports and new_path.strip().startswith("import:"):
                    replacement_payload = cls._expand_import_lines(
                        [new_path],
                        resolved_base_dir,
                        active_stack=active_stack,
                        visited_stack_files=visited_stack_files,
                        apply_scope_modifiers=True,
                        log_unused=log_unused,
                        budget=budget,
                        template_vars=mod_vars,
                        source_label=new_path,
                    )
                else:
                    replacement_payload = [new_path]
                return replacement_payload

            def _append_replacement():
                payload = _get_replacement_payload()
                if len(next_lines) + len(payload) <= cls.MAX_EXPANDED_LINES:
                    next_lines.extend(payload)
                elif not budget["line_limit_warned"]:
                    print(f"[Adaptive Prompts] Warning: Maximum expanded stack lines ({cls.MAX_EXPANDED_LINES}) exceeded. Truncating excess lines.")
                    budget["line_limit_warned"] = True

            def _append_existing(item_line: str, is_marker: bool):
                if is_marker or len(next_lines) < cls.MAX_EXPANDED_LINES:
                    next_lines.append(item_line)
                elif not budget["line_limit_warned"]:
                    print(f"[Adaptive Prompts] Warning: Maximum expanded stack lines ({cls.MAX_EXPANDED_LINES}) exceeded. Truncating excess lines.")
                    budget["line_limit_warned"] = True

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
                                _append_replacement()
                        block_depth += 1
                        continue
                    if stripped == end_tag:
                        if block_depth > 0:
                            block_depth -= 1
                            continue
                    if block_depth > 0:
                        continue

                    # Case B: Matches an unexpanded `import:` line directly
                    is_marker = cls._is_import_marker(stripped)
                    if not is_marker and _match_key(stripped) == target_key:
                        matched = True
                        if mod_type == "replace":
                            _append_replacement()
                        continue

                    _append_existing(item, is_marker)
            else:
                for item in current_lines:
                    stripped = item.strip()
                    if cls._is_import_marker(stripped):
                        _append_existing(item, True)
                        continue
                    if _match_key(stripped) == target_key:
                        matched = True
                        if mod_type == "replace":
                            _append_replacement()
                    else:
                        _append_existing(item, False)

            if not matched and log_unused:
                if mod_type == "remove":
                    print(f"[Adaptive Prompts] Debug: Exclusion target '{target_key}' did not match any loaded paths.")
                elif mod_type == "replace":
                    print(f"[Adaptive Prompts] Debug: Swap target '{target_key}' -> '{new_path}' did not match any loaded paths.")

            current_lines = next_lines
            if matched:
                present = None

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
        current_dir: str = None,
        prefix_lines: List[str] = None,
        budget: dict = None,
        template_vars: Optional[dict] = None,
        source_label: str = "stack",
    ) -> List[str]:
        """
        Recursively expands `import:<relative_path>` directives in-place with shell-style
        `set:<name>=<value>` / `${name}` template substitution, subtree-scoped modifier
        evaluation, and current-file-relative (`./` and `../`) path resolution.

        How nested scopes work:
        1. Template Variables & Sequential Expansion: `local_vars` starts as a shallow copy
           of inherited `template_vars` from the parent scope. As lines are scanned top-to-bottom:
           - `set:name=value` updates `local_vars` for lines and imports *below* it in this file
             (and any nested imports they open), without leaking upward to the parent or sideways
             into sibling imports.
           - All other lines run single-pass `string.Template` substitution (`$var`/`${var}`,
             `$$` for literal `$`) before relative canonicalization, modifier parsing, and
             security validation. Undefined or malformed placeholders log a warning and skip the line.
        2. Recursive Import Pass: Each `import:` line is validated against:
           - `_is_safe_relative_ref(...)` to reject absolute/UNC/drive/ADS/reserved paths.
           - `MAX_IMPORT_DEPTH` (32) and `MAX_TOTAL_IMPORTS` (512) / `MAX_EXPANDED_LINES`
             (10000) to prevent recursion errors and exponential DAG amplification DoS.
           - `_is_safe_path(resolved_base_dir, ...)` to enforce the `base_dir` sandbox.
           - `_path_in_active_stack(...)` to prevent circular imports (`A -> A`, `A -> B -> A`,
             symlink loops, hard-link loops, and case-insensitive FS cycles) while allowing
             sequential non-cyclic reuse of the same snippet.
           - The 5MB file size limit.
           Each imported file inherits the snapshot of `local_vars` active at that `import:` line,
           with `apply_scope_modifiers=True` and `current_dir=os.path.dirname(resolved_import)`.
        3. Post-Import Modifier Pass: When `apply_scope_modifiers=True`, the current scope's
           `remove:` and `replace:` modifiers execute in exact written order over the expanded
           lines (including any trusted `prefix_lines` and both direct and nested imports).
        """
        if active_stack is None:
            active_stack = []
        if current_dir is None:
            current_dir = resolved_base_dir
        if budget is None:
            budget = cls._new_expansion_budget()

        # Copy inherited variables so child `set:` directives never leak upward or sideways.
        local_vars = dict(template_vars) if template_vars else {}
        raw_modifier_lines = []
        parsed_modifiers = []
        modifier_vars_list = []

        # Step 1: Start from any already-expanded trusted prefix lines (e.g., from stack_file)
        # and process `set:`, template substitution, normal paths, modifiers, and `import:` lines
        # in top-to-bottom order so each `import:` sees the exact `local_vars` defined above it.
        expanded = list(prefix_lines) if prefix_lines else []
        for line in raw_lines:
            clean_line = line.strip()
            if not clean_line or clean_line.startswith("#") or cls._is_import_marker(clean_line):
                continue

            if cls._is_set_line(clean_line):
                parsed_set = cls._parse_set_directive(clean_line)
                if parsed_set is None:
                    if log_unused:
                        print(f"[Adaptive Prompts] Warning: Invalid 'set:' directive '{clean_line}' in '{source_label}'. Expected 'set:name=value'. Skipping.")
                    continue
                var_name, raw_val = parsed_set
                if var_name not in local_vars and len(local_vars) >= cls.MAX_TEMPLATE_VARS:
                    if log_unused:
                        print(f"[Adaptive Prompts] Warning: Maximum template variables ({cls.MAX_TEMPLATE_VARS}) exceeded at '{var_name}' in '{source_label}'. Skipping.")
                    continue
                resolved_val = cls._substitute_template_line(raw_val, local_vars, source_label, log_warnings=log_unused)
                if resolved_val is None:
                    continue
                if len(resolved_val) > cls.MAX_VAR_VALUE_LEN:
                    if log_unused:
                        print(f"[Adaptive Prompts] Warning: Value for stack variable '{var_name}' exceeds {cls.MAX_VAR_VALUE_LEN} character limit in '{source_label}'. Skipping.")
                    continue
                local_vars[var_name] = resolved_val
                continue

            # Parse command structure BEFORE template substitution (prepared-statement
            # discipline) so variable values cannot inject delimiters (`|`, `=`) or
            # hijack non-modifier lines into `set:`, `remove:`, or `replace:` commands.
            if cls._is_modifier_line(clean_line):
                if clean_line.startswith("remove:"):
                    raw_target = clean_line[len("remove:"):].strip()
                    sub_target = cls._substitute_template_line(raw_target, local_vars, source_label, log_warnings=log_unused)
                    if sub_target is None:
                        continue
                    c_target = cls._canonicalize_scoped_ref(sub_target, current_dir, resolved_base_dir)
                    mod = cls._parse_modifier_parts("remove", c_target)
                    raw_mod_str = f"remove:{c_target}"
                else:
                    parts = clean_line[len("replace:"):].split("|", 1)
                    if len(parts) != 2:
                        if log_unused:
                            print(f"[Adaptive Prompts] Warning: Invalid modifier directive '{clean_line}'. Skipping.")
                        continue
                    sub_old = cls._substitute_template_line(parts[0].strip(), local_vars, source_label, log_warnings=log_unused)
                    if sub_old is None:
                        continue
                    sub_new = cls._substitute_template_line(parts[1].strip(), local_vars, source_label, log_warnings=log_unused)
                    if sub_new is None:
                        continue
                    c_old = cls._canonicalize_scoped_ref(sub_old, current_dir, resolved_base_dir)
                    c_new = cls._canonicalize_scoped_ref(sub_new, current_dir, resolved_base_dir)
                    mod = cls._parse_modifier_parts("replace", c_old, c_new)
                    raw_mod_str = f"replace:{c_old}|{c_new}"

                if mod is not None:
                    raw_modifier_lines.append(raw_mod_str)
                    parsed_modifiers.append(mod)
                    modifier_vars_list.append(dict(local_vars))
                else:
                    if log_unused:
                        print(f"[Adaptive Prompts] Warning: Invalid modifier directive '{clean_line}'. Skipping.")
                continue

            substituted = cls._substitute_template_line(clean_line, local_vars, source_label, log_warnings=log_unused)
            if substituted is None:
                continue
            substituted = substituted.strip()
            if not substituted or substituted.startswith("#") or cls._is_import_marker(substituted):
                continue
            if cls._is_set_line(substituted) or cls._is_modifier_line(substituted):
                if log_unused:
                    print(f"[Adaptive Prompts] Warning: Command directive cannot be injected via template substitution in '{source_label}' (line: '{clean_line}'). Skipping.")
                continue

            scoped_line = cls._canonicalize_scoped_ref(substituted, current_dir, resolved_base_dir)

            if not scoped_line.startswith("import:"):
                if len(expanded) < cls.MAX_EXPANDED_LINES:
                    expanded.append(scoped_line)
                elif not budget["line_limit_warned"]:
                    print(f"[Adaptive Prompts] Warning: Maximum expanded stack lines ({cls.MAX_EXPANDED_LINES}) exceeded. Truncating excess lines.")
                    budget["line_limit_warned"] = True
                continue

            import_target = scoped_line[len("import:"):].strip().replace("\\", "/")
            if not import_target:
                print("[Adaptive Prompts] Warning: Empty 'import:' directive. Skipping.")
                continue

            if len(active_stack) >= cls.MAX_IMPORT_DEPTH:
                print(f"[Adaptive Prompts] Warning: Maximum import depth ({cls.MAX_IMPORT_DEPTH}) exceeded at '{import_target}'. Skipping.")
                continue

            if not _is_safe_relative_ref(import_target):
                print(f"[Adaptive Prompts] Security Warning: import '{import_target}' attempts path traversal. Skipping.")
                continue

            resolved_import = os.path.normpath(os.path.join(resolved_base_dir, import_target))
            if not _is_safe_path(resolved_base_dir, resolved_import):
                print(f"[Adaptive Prompts] Security Warning: import '{import_target}' attempts path traversal. Skipping.")
                continue

            if not os.path.isfile(resolved_import):
                print(f"[Adaptive Prompts] Warning: Imported stack file '{resolved_import}' is not a valid file. Skipping.")
                continue

            real_import = os.path.realpath(resolved_import)
            if _path_in_active_stack(real_import, active_stack):
                print(f"[Adaptive Prompts] Warning: Circular import detected for '{import_target}'. Skipping.")
                continue

            if budget["imports"] >= cls.MAX_TOTAL_IMPORTS:
                if not budget["import_limit_warned"]:
                    print(f"[Adaptive Prompts] Warning: Maximum total import expansions ({cls.MAX_TOTAL_IMPORTS}) exceeded at '{import_target}'. Skipping remaining imports.")
                    budget["import_limit_warned"] = True
                continue

            if len(expanded) >= cls.MAX_EXPANDED_LINES:
                if not budget["line_limit_warned"]:
                    print(f"[Adaptive Prompts] Warning: Maximum expanded stack lines ({cls.MAX_EXPANDED_LINES}) exceeded. Truncating excess lines.")
                    budget["line_limit_warned"] = True
                continue

            try:
                if os.path.getsize(resolved_import) > 5 * 1024 * 1024:
                    print(f"[Adaptive Prompts] Warning: File '{resolved_import}' exceeds 5MB limit. Skipping.")
                    continue
                with open(resolved_import, "r", encoding="utf-8-sig") as f:
                    imported_lines = f.readlines()
            except (OSError, UnicodeDecodeError) as e:
                print(f"[Adaptive Prompts] Warning: Could not read imported stack file '{resolved_import}': {e}")
                continue

            budget["imports"] += 1
            if visited_stack_files is not None and resolved_import not in visited_stack_files:
                visited_stack_files.append(resolved_import)

            norm_import_key = cls._normalize_match(scoped_line)
            child_expanded = cls._expand_import_lines(
                imported_lines,
                resolved_base_dir,
                active_stack=active_stack + [real_import],
                visited_stack_files=visited_stack_files,
                apply_scope_modifiers=True,
                log_unused=log_unused,
                current_dir=os.path.dirname(resolved_import),
                budget=budget,
                template_vars=local_vars,
                source_label=import_target,
            )
            if len(expanded) + len(child_expanded) + 2 <= cls.MAX_EXPANDED_LINES:
                expanded.append(f"{cls._IMPORT_BEGIN_PREFIX}{norm_import_key}")
                expanded.extend(child_expanded)
                expanded.append(f"{cls._IMPORT_END_PREFIX}{norm_import_key}")
            elif not budget["line_limit_warned"]:
                print(f"[Adaptive Prompts] Warning: Maximum expanded stack lines ({cls.MAX_EXPANDED_LINES}) exceeded. Truncating excess lines.")
                budget["line_limit_warned"] = True

        # Step 2: Apply this scope's modifiers in exact written order to the expanded lines.
        if apply_scope_modifiers:
            return cls._apply_modifiers_to_lines(
                expanded,
                parsed_modifiers,
                resolved_base_dir,
                active_stack=active_stack,
                visited_stack_files=visited_stack_files,
                expand_replacement_imports=True,
                log_unused=log_unused,
                budget=budget,
                modifier_vars_list=modifier_vars_list,
            )

        return expanded + raw_modifier_lines

    @classmethod
    def _resolve_root_lines(
        cls,
        resolved_base_dir: str,
        stack_file: str,
        inline_stack: str,
        visited_stack_files: List[str] = None,
        log_unused: bool = True,
        log_stack_warnings: bool = True,
    ) -> List[str]:
        """
        Shared root-level stack resolution for both `_resolve_paths()` and `IS_CHANGED()`.

        Evaluates `stack_file` in its own subtree scope (so its `./` and `../` paths
        anchor to `stack_file`'s own directory when inside `resolved_base_dir`, and its
        internal `remove:`/`replace:` modifiers do not leak onto `inline_stack`), then
        evaluates `inline_stack` as an outer scope anchored to `resolved_base_dir` that
        can still modify lines and `import:` blocks produced by `stack_file`.
        """
        node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        budget = cls._new_expansion_budget()
        stack_expanded = []

        if stack_file:
            raw_stack_file = stack_file.strip()
            if (
                "\x00" in raw_stack_file
                or _is_device_namespace_path(raw_stack_file)
                or _is_unc_path(raw_stack_file)
                or _is_drive_relative_path(raw_stack_file)
            ):
                if log_stack_warnings:
                    print(f"[Adaptive Prompts] Security Warning: stack_file '{stack_file}' attempts path traversal. Skipping.")
            else:
                stack_path = os.path.normpath(os.path.join(resolved_base_dir, raw_stack_file.replace("\\", "/")))
                if not _is_safe_path(node_root, stack_path):
                    if log_stack_warnings:
                        print(f"[Adaptive Prompts] Security Warning: stack_file '{stack_file}' attempts path traversal. Skipping.")
                elif os.path.isfile(stack_path):
                    if visited_stack_files is not None and stack_path not in visited_stack_files:
                        visited_stack_files.append(stack_path)
                    try:
                        if os.path.getsize(stack_path) > 5 * 1024 * 1024:
                            if log_stack_warnings:
                                print(f"[Adaptive Prompts] Warning: File '{stack_path}' exceeds 5MB limit. Skipping.")
                            else:
                                print(f"[Adaptive Prompts] Warning: File '{stack_path}' exceeds 5MB limit. Skipping read in IS_CHANGED.")
                        else:
                            with open(stack_path, "r", encoding="utf-8-sig") as f:
                                stack_raw_lines = f.readlines()
                            stack_current_dir = (
                                os.path.dirname(stack_path)
                                if _is_safe_path(resolved_base_dir, stack_path)
                                else resolved_base_dir
                            )
                            stack_expanded = cls._expand_import_lines(
                                stack_raw_lines,
                                resolved_base_dir,
                                active_stack=[os.path.realpath(stack_path)],
                                visited_stack_files=visited_stack_files,
                                apply_scope_modifiers=True,
                                log_unused=log_unused,
                                current_dir=stack_current_dir,
                                budget=budget,
                                source_label=raw_stack_file,
                            )
                    except (OSError, UnicodeDecodeError) as e:
                        if log_stack_warnings:
                            print(f"[Adaptive Prompts] Warning: Could not read stack file '{stack_path}': {e}")
                elif log_stack_warnings:
                    print(f"[Adaptive Prompts] Warning: Could not read stack file '{stack_path}': not a valid file.")

        if inline_stack:
            return cls._expand_import_lines(
                inline_stack.splitlines(),
                resolved_base_dir,
                active_stack=[],
                visited_stack_files=visited_stack_files,
                apply_scope_modifiers=True,
                log_unused=log_unused,
                current_dir=resolved_base_dir,
                prefix_lines=stack_expanded,
                budget=budget,
                source_label="inline_stack",
            )

        return stack_expanded

    @classmethod
    def IS_CHANGED(cls, base_dir, stack_file, inline_stack, override_context=False, seed=0, context=None, debug_log=False):
        import hashlib
        m = hashlib.sha256()

        resolved_base_dir = cls._resolve_base_dir(base_dir)

        config_path = os.path.join(adaptive_prompts_dir, "config.json")
        if os.path.isfile(config_path):
            try:
                m.update(f"cfg:{os.path.getmtime(config_path)}\0".encode("utf-8"))
            except OSError:
                pass

        visited_stack_files = []
        paths = cls._resolve_root_lines(
            resolved_base_dir,
            stack_file,
            inline_stack,
            visited_stack_files=visited_stack_files,
            log_unused=False,
            log_stack_warnings=False,
        )

        for imp_path in visited_stack_files:
            try:
                m.update(f"stack:{imp_path}:{os.path.getmtime(imp_path)}\0".encode("utf-8"))
            except OSError:
                pass

        for line in paths:
            line = line.strip()
            if not line or line.startswith("#") or cls._is_import_marker(line) or cls._is_modifier_line(line):
                continue
            is_random = line.startswith("random:")
            path_str = (line[len("random:"):].strip() if is_random else line).replace("\\", "/")
            if not path_str or not _is_safe_relative_ref(path_str):
                continue

            resolved_path = os.path.normpath(os.path.join(resolved_base_dir, path_str))

            if not _is_safe_path(resolved_base_dir, resolved_path):
                continue

            if is_random and os.path.isdir(resolved_path):
                for root, dirs, files in os.walk(resolved_path):
                    # Filter out "hidden" directories (used for local overrides/drafts)
                    dirs[:] = sorted(d for d in dirs if not (d.startswith('_') or d.startswith('.')))
                    for file in sorted(files):
                        # Filter out "hidden" files so they don't trigger cache invalidation
                        if file.endswith(".txt") and not (file.startswith('_') or file.startswith('.')):
                            try:
                                fpath = os.path.join(root, file)
                                if _is_safe_path(resolved_path, fpath) and os.path.isfile(fpath):
                                    m.update(f"rand:{fpath}:{os.path.getmtime(fpath)}\0".encode("utf-8"))
                            except OSError:
                                pass
            elif not is_random and os.path.isfile(resolved_path):
                try:
                    m.update(f"file:{resolved_path}:{os.path.getmtime(resolved_path)}\0".encode("utf-8"))
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
        return self._resolve_root_lines(
            base_dir,
            stack_file,
            inline_stack,
            visited_stack_files=None,
            log_unused=True,
            log_stack_warnings=True,
        )

    def _get_random_file(self, dir_path: str, rng: SeededRandom) -> str:
        candidates = []
        for root, dirs, files in os.walk(dir_path):
            # Exclude "hidden" directories to avoid picking up overrides or drafts
            dirs[:] = [d for d in dirs if not (d.startswith('_') or d.startswith('.'))]
            for file in files:
                # Exclude "hidden" files and verify each candidate is a regular file within dir_path
                if file.endswith(".txt") and not (file.startswith('_') or file.startswith('.')):
                    candidate = os.path.join(root, file)
                    if _is_safe_path(dir_path, candidate) and os.path.isfile(candidate):
                        candidates.append(candidate)
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
            if self._is_modifier_line(clean_line):
                mod = self._parse_modifier(clean_line)
                if mod is not None:
                    modifiers.append(mod)
                else:
                    print(f"[Adaptive Prompts] Warning: Invalid modifier directive '{clean_line}'. Skipping.")
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
                budget=self._new_expansion_budget(),
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
            path_str = (line[len("random:"):].strip() if is_random else line).replace("\\", "/")
            if is_random and not path_str:
                print("[Adaptive Prompts] Warning: Empty 'random:' directive. Skipping.")
                continue

            if not _is_safe_relative_ref(path_str):
                print(f"[Adaptive Prompts] Security Warning: path '{path_str}' attempts path traversal. Skipping.")
                continue

            resolved_path = os.path.normpath(os.path.join(resolved_base_dir, path_str))
            if not _is_safe_path(resolved_base_dir, resolved_path):
                print(f"[Adaptive Prompts] Security Warning: path '{path_str}' attempts path traversal. Skipping.")
                continue

            if is_random:
                if os.path.isdir(resolved_path):
                    chosen_file = self._get_random_file(resolved_path, rng)
                    if chosen_file:
                        files_to_process.append(chosen_file)
                else:
                    print(f"[Adaptive Prompts] Warning: Random path '{resolved_path}' is not a valid directory.")
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
            with open(filepath, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError) as e:
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

    def process(self, seed: int, base_dir: str, stack_file: str, inline_stack: str, override_context: bool = False, context: dict = None, debug_log: bool = False):
        _sync_base_config()
        rng = SeededRandom(seed)

        resolved_base_dir = self._resolve_base_dir(base_dir)

        raw_lines = self._resolve_paths(resolved_base_dir, stack_file, inline_stack)
        files_to_process = self._build_final_file_list(raw_lines, resolved_base_dir, rng)

        if debug_log:
            print(f"[Adaptive Prompts] Resolved stack ({len(files_to_process)} files, base_dir='{resolved_base_dir}'):")
            for idx, fpath in enumerate(files_to_process, start=1):
                try:
                    rel_display = os.path.relpath(fpath, resolved_base_dir).replace("\\", "/")
                except ValueError:
                    rel_display = fpath
                print(f"  {idx:02d}. {rel_display}")

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
