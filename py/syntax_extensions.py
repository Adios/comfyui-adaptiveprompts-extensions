import re

# We import resolve_wildcards from the core to evaluate dynamic variables inside conditional statements
try:
    from py.generator import resolve_wildcards
    from py.string_utils import PromptCleanup
except ImportError:
    resolve_wildcards = None
    PromptCleanup = None
COND_REGEX = re.compile(r"^([?!])?([A-Za-z0-9_\-\*]+)(?:\s*(>=|<=|==|!=|>|<|!~|~)\s*(.*))?$")

def _parse_statement(s: str, _depth: int = 0) -> tuple[str, float]:
    """
    Parses a prompt statement to extract its base text and structural weight recursively.
    """
    s = s.strip()
    if not s or _depth >= 12:
        return s, 1.0

    def is_fully_wrapped(text: str, open_char: str, close_char: str) -> bool:
        if not (text.startswith(open_char) and text.endswith(close_char)):
            return False
        d = 0
        for i, char in enumerate(text):
            if char == open_char: d += 1
            elif char == close_char: d -= 1
            if d == 0 and i < len(text) - 1:
                return False
        return d == 0

    if is_fully_wrapped(s, '(', ')'):
        inner = s[1:-1].strip()
        weight = 1.1
        d = 0
        colon_pos = -1
        for i, char in enumerate(inner):
            if char == '(': d += 1
            elif char == ')': d -= 1
            elif char == ':' and d == 0:
                colon_pos = i
        if colon_pos != -1:
            try:
                weight_str = inner[colon_pos+1:].strip()
                weight = float(weight_str)
                inner = inner[:colon_pos].strip()
            except ValueError:
                pass
        inner_text, inner_weight = _parse_statement(inner, _depth=_depth + 1)
        return inner_text, weight * inner_weight

    if is_fully_wrapped(s, '[', ']'):
        inner = s[1:-1].strip()
        inner_text, inner_weight = _parse_statement(inner, _depth=_depth + 1)
        return inner_text, 0.9 * inner_weight

    return s, 1.0

def _get_statement_weight(s: str, _depth: int = 0) -> float:
    return _parse_statement(s, _depth)[1]

def evaluate_condition_string(c_str: str, resolved_vars: dict) -> bool:
    """
    Evaluates a lightweight conditional statement against the current variable context.
    """
    c_str = c_str.strip()
    if not resolved_vars:
        return False
        
    m = COND_REGEX.match(c_str)
    if not m:
        return False
        
    prefix, vn, op, req_str = m.groups()
    
    if prefix == '?':
        return vn in resolved_vars and bool(resolved_vars[vn])
    if prefix == '!':
        return vn not in resolved_vars or not bool(resolved_vars[vn])
        
    if not op:
        return vn in resolved_vars and bool(resolved_vars[vn])
        
    if vn not in resolved_vars:
        return op == '!=' or op == '!~'
        
    values = list(resolved_vars[vn].values())
    
    is_numeric = False
    req_val = 0.0
    if op in ('>', '<', '>=', '<=', '==', '!='):
        try:
            req_val = float(req_str)
            is_numeric = True
        except ValueError:
            pass

    if is_numeric:
        for val in values:
            w = _get_statement_weight(val)
            if op == '>' and w > req_val: return True
            if op == '>=' and w >= req_val: return True
            if op == '<' and w < req_val: return True
            if op == '<=' and w <= req_val: return True
            if op == '==' and w == req_val: return True
        
        if op == '!=':
            return all(_get_statement_weight(val) != req_val for val in values)
        return False
    else:
        if op == '~':
            return any(req_str in val for val in values)
        if op == '!~':
            return all(req_str not in val for val in values)
        if op == '==':
            return any(req_str == val for val in values)
        if op == '!=':
            return all(req_str != val for val in values)
            
    return False

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

def _split_top_level_pipes(s: str) -> list[str]:
    result = []
    paren_depth = brace_depth = bracket_depth = 0
    current = []
    for c in s:
        if c == '(': paren_depth += 1
        elif c == ')': paren_depth -= 1
        elif c == '{': brace_depth += 1
        elif c == '}': brace_depth -= 1
        elif c == '[': bracket_depth += 1
        elif c == ']': bracket_depth -= 1
        elif c == '|' and paren_depth == 0 and brace_depth == 0 and bracket_depth == 0:
            result.append("".join(current))
            current = []
            continue
        current.append(c)
    result.append("".join(current))
    return result

def conditional_bracket_handler(content: str, seeded_rng, wildcard_dir, resolved_vars: dict) -> str | None:
    """
    Hooks into bracket parsing to evaluate {if(cond)|A|B} and {switch(var)} syntax.
    """
    raw_choices = _split_top_level_pipes(content)
    if not raw_choices:
        return None
        
    first_choice = raw_choices[0].strip()
    
    # --- IF STATEMENT ---
    m_if = re.match(r"^if\s*\((.*?)\)$", first_choice, re.DOTALL)
    if m_if:
        cond_str = m_if.group(1).strip()
        
        # evaluate dynamic variables in condition
        if resolve_wildcards:
            cond_str = resolve_wildcards(
                cond_str, 
                seeded_rng=seeded_rng, 
                wildcard_dir=wildcard_dir, 
                _depth=0, 
                _resolved_vars=resolved_vars
            ).strip()

        is_true = evaluate_condition_string(cond_str, resolved_vars)
        if is_true and len(raw_choices) > 1:
            return raw_choices[1]
        elif not is_true and len(raw_choices) > 2:
            return raw_choices[2]
        return ""

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
