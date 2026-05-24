import re
import sys

# Try to get config for bypass toggle, gracefully fail if config not available
get_config = None
for mod_name, mod in list(sys.modules.items()):
    if mod_name.endswith(".py.config") and "adaptiveprompt" in mod_name.lower():
        if hasattr(mod, "get_config"):
            get_config = mod.get_config
            break

# We need the pure function from the mother project
try:
    from py.generator import _split_top_level_pipes, resolve_wildcards
except ImportError:
    # fallback
    _split_top_level_pipes = None
    resolve_wildcards = None

def _split_case_result(choice: str) -> tuple[str, str | None]:
    paren_depth = 0
    brace_depth = 0
    bracket_depth = 0
    for i, c in enumerate(choice):
        if c == '(': paren_depth += 1
        elif c == ')': paren_depth -= 1
        elif c == '{': brace_depth += 1
        elif c == '}': brace_depth -= 1
        elif c == '[': bracket_depth += 1
        elif c == ']': bracket_depth -= 1
        elif c == ':' and paren_depth == 0 and brace_depth == 0 and bracket_depth == 0:
            return choice[:i], choice[i+1:]
    return choice, None

def conditional_bracket_handler(content: str, seeded_rng, wildcard_dir, resolved_vars: dict) -> str | None:
    if _split_top_level_pipes is None:
        return None

    raw_choices = _split_top_level_pipes(content)
    if not raw_choices:
        return None

    first_choice = raw_choices[0].strip()

    # --- SWITCH STATEMENT ---
    m_switch = re.match(r"^switch\s*\((.*?)\)$", first_choice, re.DOTALL)
    if m_switch:
        switch_var = m_switch.group(1).strip()

        if resolve_wildcards:
            switch_var = resolve_wildcards(
                switch_var,
                seeded_rng=seeded_rng,
                wildcard_dir=wildcard_dir,
                _depth=0,
                _resolved_vars=resolved_vars
            ).strip()

        var_values = []
        if resolved_vars and switch_var in resolved_vars:
            var_values = list(resolved_vars[switch_var].values())

        default_res = ""
        has_default = False

        for choice in raw_choices[1:]:
            case_val, res_val = _split_case_result(choice)
            if res_val is not None:
                case_val = case_val.strip()

                if case_val == "default":
                    default_res = res_val
                    has_default = True
                elif case_val in var_values:
                    return res_val

        if has_default:
            return default_res
        return ""

    return None

def conditional_bypass_handler(content: str) -> bool:
    if get_config is not None:
        if not get_config("enable_switch_syntax", True):
            return False

    if _split_top_level_pipes is None:
        return False

    raw_choices = _split_top_level_pipes(content)
    if not raw_choices: 
        return False
        
    first_choice = raw_choices[0].strip()
    return bool(re.match(r"^switch\s*\((.*?)\)$", first_choice, re.DOTALL))
