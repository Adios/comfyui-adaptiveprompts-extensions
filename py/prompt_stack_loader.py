import os
import re
import glob
from typing import List
import sys

# Ensure comfyui-adaptiveprompts is available
generator_mod = None
for mod_name, mod in list(sys.modules.items()):
    if mod_name.endswith(".py.generator") and "adaptiveprompt" in mod_name.lower():
        if hasattr(mod, "SeededRandom"):
            generator_mod = mod
            break

if generator_mod is not None:
    SeededRandom = generator_mod.SeededRandom
    DEFAULT_WILDCARD_ROOT = generator_mod.DEFAULT_WILDCARD_ROOT
else:
    # Fallback to sys.path import
    adaptive_prompts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "comfyui-adaptiveprompts"))
    if adaptive_prompts_dir not in sys.path:
        sys.path.insert(0, adaptive_prompts_dir)
    from py.generator import SeededRandom, DEFAULT_WILDCARD_ROOT
try:
    if generator_mod is not None:
        try:
            evaluate_prompt_core = generator_mod.evaluate_prompt_core
        except AttributeError:
            resolve_wildcards = generator_mod.resolve_wildcards
            def evaluate_prompt_core(prompt: str, rng, wildcard_dir: str, resolved_vars: dict, hide_comments: bool = True) -> str:
                comment_blocks = re.findall(r"##(.*?)##", prompt, flags=re.DOTALL)
                for block in comment_blocks:
                    _ = resolve_wildcards(block, rng, wildcard_dir, _resolved_vars=resolved_vars)
                if hide_comments:
                    prompt = re.sub(r"##.*?##", "", prompt, flags=re.DOTALL)
                return resolve_wildcards(prompt, rng, wildcard_dir, _resolved_vars=resolved_vars)
    else:
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

# --- Context Helper Functions ---
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
                    "tooltip": "The root directory for your stack. Absolute paths work. Relative paths anchor to this custom node's root directory. Leave empty to default to the 'prompts' folder."
                }),
                "stack_file": ("STRING", {
                    "default": "",
                    "tooltip": "Optional. Path to a .txt file containing your stack. Absolute paths work. Relative paths (e.g., 'stacks/my_stack.txt') resolve against base_dir."
                }),
                "inline_stack": ("STRING", {
                    "multiline": True, 
                    "default": "",
                    "tooltip": "List of text paths to load sequentially (absolute paths work, relative resolve against base_dir).\n- Use 'random:folder' to pick a random file.\n- Prefix with 'remove:' to exclude a path.\n- Use 'replace:old|new' to swap a path in-place.\n- Lines starting with # are ignored."
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
        
    RETURN_TYPES = ("STRING", "DICT", "STRING")
    RETURN_NAMES = ("prompt", "context", "lora_string")
    FUNCTION = "process"
    CATEGORY = "adaptiveprompts/generation"

    def _resolve_paths(self, base_dir: str, stack_file: str, inline_stack: str) -> List[str]:
        paths = []
        if stack_file:
            # Use the variable 'stack_path' for stack configuration files
            stack_path = stack_file
            if not os.path.isabs(stack_path) and base_dir:
                stack_path = os.path.normpath(os.path.join(base_dir, stack_path))
            try:
                with open(stack_path, "r", encoding="utf-8") as f:
                    paths.extend(f.readlines())
            except OSError as e:
                print(f"[Adaptive Prompts] Warning: Could not read stack file '{stack_path}': {e}")

        if inline_stack:
            paths.extend(inline_stack.splitlines())
            
        return paths

    def _get_random_file(self, dir_path: str, rng: SeededRandom) -> str:
        candidates = glob.glob(os.path.join(dir_path, "**", "*.txt"), recursive=True)
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
            
            resolved_path = path_str
            if not os.path.isabs(resolved_path):
                resolved_path = os.path.normpath(os.path.join(resolved_base_dir, resolved_path))
                
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

    def _process_single_file(self, filepath: str, current_context: dict, rng: SeededRandom, override_context: bool) -> tuple[str, List[str]]:
        lora_regex = re.compile(r"<lora:[^>]+>")
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            print(f"[Adaptive Prompts] Warning: Failed to read file '{filepath}': {e}")
            return "", []

        # Extract LoRA tags directly from raw string
        found_loras = lora_regex.findall(content)
        cleaned_content = lora_regex.sub("", content)

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
        
        resolved_base_dir = base_dir.strip()
        if not resolved_base_dir:
            resolved_base_dir = DEFAULT_PROMPT_ROOT
        elif not os.path.isabs(resolved_base_dir):
            # Anchor relative base_dir to the custom node's root directory.
            # This allows portable paths like "prompts/arch" to resolve properly.
            node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            resolved_base_dir = os.path.normpath(os.path.join(node_root, resolved_base_dir))
            
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
