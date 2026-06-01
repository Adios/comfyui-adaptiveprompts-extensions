import os
import re
import glob
from typing import List
import sys
import importlib.util

adaptive_prompts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "comfyui-adaptiveprompts"))

added_to_sys_path = False
if adaptive_prompts_dir not in sys.path:
    sys.path.insert(0, adaptive_prompts_dir)
    added_to_sys_path = True

try:
    from py.generator import SeededRandom, DEFAULT_WILDCARD_ROOT
    try:
        from py.generator import evaluate_prompt_core
    except ImportError:
        from py.generator import resolve_wildcards
        def evaluate_prompt_core(prompt: str, rng, wildcard_dir: str, resolved_vars: dict, hide_comments: bool = True) -> str:
            """Fallback implementation for older core versions (like the 'main' branch)."""
            comment_blocks = re.findall(r"##(.*?)##", prompt, flags=re.DOTALL)
            for block in comment_blocks:
                _ = resolve_wildcards(block, rng, wildcard_dir, _resolved_vars=resolved_vars)
            if hide_comments:
                prompt = re.sub(r"##.*?##", "", prompt, flags=re.DOTALL)
            return resolve_wildcards(prompt, rng, wildcard_dir, _resolved_vars=resolved_vars)
except ImportError:
    raise ImportError("The 'comfyui-adaptiveprompts-extensions' node requires the 'comfyui-adaptiveprompts' custom node to be installed.")
finally:
    if added_to_sys_path:
        sys.path.remove(adaptive_prompts_dir)

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

def _snapshot_context(context: dict) -> dict:
    snapshot = {}
    for k, v in context.items():
        if isinstance(v, dict):
            snapshot[k] = list(v.keys())
    return snapshot

def _apply_context_override(context: dict, snapshot: dict) -> None:
    for k, old_keys in snapshot.items():
        if k in context and isinstance(context[k], dict):
            current_keys = list(context[k].keys())
            new_keys = [key for key in current_keys if key not in old_keys]
            if new_keys:
                for old_k in old_keys:
                    del context[k][old_k]
# --------------------------------

DEFAULT_PROMPT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "prompts")
)

class PromptStackLoader:
    """
    Sequentially loads, parses, and evaluates a stack of text files to build a complex prompt and context.
    
    This node acts as a batch processor that orchestrates prompt generation across multiple files:
    1. It resolves file paths, processes inline commands (remove:, replace:, random:), and collects a final list of files.
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
                    "tooltip": "List of text paths to load sequentially (relative resolve against base_dir).\n- Use 'random:folder' to pick a random file.\n- Prefix with 'remove:' to exclude a path.\n- Use 'replace:old|new' to swap a path in-place.\n- Lines starting with # are ignored."
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

    @classmethod
    def IS_CHANGED(cls, base_dir, stack_file, inline_stack, override_context=False, seed=0, context=None):
        import hashlib
        import os
        m = hashlib.sha256()
        
        resolved_base_dir = cls._resolve_base_dir(base_dir)
        node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

        paths = []
        if stack_file:
            stack_path = os.path.normpath(os.path.join(resolved_base_dir, stack_file))
            if _is_safe_path(node_root, stack_path) and os.path.exists(stack_path):
                m.update(str(os.path.getmtime(stack_path)).encode("utf-8"))
                try:
                    if os.path.getsize(stack_path) > 5 * 1024 * 1024:
                        print(f"[Adaptive Prompts] Warning: File '{stack_path}' exceeds 5MB limit. Skipping read in IS_CHANGED.")
                    else:
                        with open(stack_path, "r", encoding="utf-8") as f:
                            paths.extend(f.readlines())
                except OSError:
                    pass
        if inline_stack:
            paths.extend(inline_stack.splitlines())
            
        for line in paths:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("remove:") or line.startswith("replace:"):
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
        Resolves and loads the contents of the `stack_file` and `inline_stack`.
        
        Security Exception: The `stack_file` itself acts as an orchestrator and may 
        legitimately live outside the `base_dir` (e.g., in a sibling 'stacks' folder). 
        Therefore, `stack_file` is sandboxed against the broader `node_root` (the 
        extension directory), NOT the `base_dir`. 
        
        However, the internal lines parsed *from* the stack file remain strictly 
        sandboxed against the `base_dir` during the final file resolution phase.
        """
        paths = []
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
                            paths.extend(f.readlines())
                except OSError as e:
                    print(f"[Adaptive Prompts] Warning: Could not read stack file '{stack_path}': {e}")

        if inline_stack:
            paths.extend(inline_stack.splitlines())
            
        return paths

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
        def _normalize_match(p: str) -> str:
            # Normalizes slashes and redundant ./ for cross-platform string matching.
            is_rand = p.startswith("random:")
            core = p[7:].strip() if is_rand else p
            core = os.path.normpath(core).replace("\\", "/")
            return f"random:{core}" if is_rand else core
        
        # Pass 1 (Identify Modifiers)
        exclusions = set()
        swaps = {}
        for line in raw_lines:
            clean_line = line.strip()
            if clean_line.startswith("remove:"):
                path_to_exclude = clean_line[7:].strip()
                if path_to_exclude:
                    exclusions.add(_normalize_match(path_to_exclude))
            elif clean_line.startswith("replace:"):
                # Slice off 'replace:' and split by the first pipe
                parts = clean_line[8:].split("|", 1)
                if len(parts) == 2:
                    old_path = parts[0].strip()
                    new_path = parts[1].strip()
                    if old_path and new_path:
                        swaps[_normalize_match(old_path)] = new_path

        # Pass 2 (Filter and Swap the Stack)
        valid_lines = []
        unused_exclusions = exclusions.copy()
        unused_swaps = set(swaps.keys())
        
        for line in raw_lines:
            clean_line = line.strip()
            # Skip empty lines, comments, and the modifier commands themselves
            if not clean_line or clean_line.startswith("#") or clean_line.startswith("remove:") or clean_line.startswith("replace:"):
                continue
                
            match_key = _normalize_match(clean_line)
                
            # Handle Exclusions
            if match_key in exclusions:
                if match_key in unused_exclusions:
                    unused_exclusions.remove(match_key)
                continue
                
            # Handle Swaps
            if match_key in swaps:
                valid_lines.append(swaps[match_key])
                if match_key in unused_swaps:
                    unused_swaps.remove(match_key)
                continue
                
            # If not modified, keep the original line
            valid_lines.append(line)

        # Pass 3 (Typo Check / Debug Warning)
        for unused in unused_exclusions:
            print(f"[Adaptive Prompts] Debug: Exclusion target '{unused}' did not match any loaded paths.")
        for unused in unused_swaps:
            print(f"[Adaptive Prompts] Debug: Swap target '{unused}' -> '{swaps[unused]}' did not match any loaded paths.")

        files_to_process = []
        for line in valid_lines:
            line = line.strip()
            # We already filtered empty and # lines, but we re-strip for safety
            if not line or line.startswith("#"):
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

        snapshot = {}
        if override_context:
            snapshot = _snapshot_context(current_context)

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

        if override_context:
            _apply_context_override(current_context, snapshot)

        return evaluated_text.strip(), found_loras

    def process(self, seed: int, base_dir: str, stack_file: str, inline_stack: str, override_context: bool = False, context: dict = None):
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
            if not isinstance(v, dict):
                current_context[k] = _ensure_bucket_dict(v)

        lora_string = " ".join(lora_tags)
        final_prompt_string = ", ".join(accumulated_prompts)
        
        return (final_prompt_string, current_context, lora_string)
