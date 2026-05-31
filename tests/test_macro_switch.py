import os
import sys
import pytest
import types

node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
adaptive_prompts_dir = os.path.abspath(os.path.join(node_root, "..", "comfyui-adaptiveprompts"))

# Mock the 'py' package to include both our extension's py/ and the mother project's py/.
sys.modules["py"] = types.ModuleType("py")
sys.modules["py"].__path__ = [
    os.path.join(node_root, "py"),
    os.path.join(adaptive_prompts_dir, "py")
]
from py.prompt_stack_loader import PromptStackLoader

def test_macro_switch_basic():
    loader = PromptStackLoader()
    prompt = "{ps_switch(view)\n  | close-up: (face:1.2)\n  | full-body: shoes\n  | default: nothing\n  }"
    
    # Matching case
    vars1 = {"view": {"o1": "close-up"}}
    res1 = loader._resolve_switch_macros(prompt, vars1)
    assert res1.strip() == "(face:1.2)"

    # Default case
    vars2 = {"view": {"o1": "unknown"}}
    res2 = loader._resolve_switch_macros(prompt, vars2)
    assert res2.strip() == "nothing"

def test_macro_switch_lazy_default():
    loader = PromptStackLoader()
    prompt = "{ps_switch(view)\n  | close-up: A\n  | default: { B | C }\n  }"
    vars1 = {"view": {"o1": "unknown"}}
    res1 = loader._resolve_switch_macros(prompt, vars1)
    assert res1.strip() == "{ B | C }"

def test_macro_switch_complex_matching():
    loader = PromptStackLoader()
    prompt = "{ps_switch(view)\n  | (small breasts: 0.8): A\n  | ~large: B\n  | default: C\n  }"
    
    vars1 = {"view": {"o1": "(small breasts: 0.8)"}}
    res1 = loader._resolve_switch_macros(prompt, vars1)
    assert res1.strip() == "A"

    vars2 = {"view": {"o1": "unknown"}}
    res2 = loader._resolve_switch_macros(prompt, vars2)
    assert res2.strip() == "C"

def test_macro_switch_nested():
    loader = PromptStackLoader()
    prompt = "{ps_switch(view) | close-up: {ps_switch(time) | day: bright | night: dark} | default: nothing}"
    
    # Should resolve inner switch
    vars1 = {"view": {"o1": "close-up"}, "time": {"o1": "day"}}
    res1 = loader._resolve_switch_macros(prompt, vars1)
    assert res1.strip() == "bright"

def test_macro_switch_empty_when_missing_context():
    loader = PromptStackLoader()
    prompt = "{ps_switch(dynamic_view) | a: b}"
    
    # Missing variable completely should resolve to empty string since there's no default
    vars1 = {"other_var": {"o1": "value"}}
    res1 = loader._resolve_switch_macros(prompt, vars1)
    assert res1 == ""

def test_macro_switch_default_when_missing_context():
    loader = PromptStackLoader()
    prompt = "{ps_switch(dynamic_view) | a: b | default: c}"
    
    # Missing variable completely should resolve to default if provided
    vars1 = {"other_var": {"o1": "value"}}
    res1 = loader._resolve_switch_macros(prompt, vars1)
    assert res1.strip() == "c"

def test_macro_switch_fallthrough():
    loader = PromptStackLoader()
    prompt = "{ps_switch(view) | a | b | c: same thing | default: nothing}"
    
    # Matches 'a'
    vars1 = {"view": {"o1": "a"}}
    assert loader._resolve_switch_macros(prompt, vars1).strip() == "same thing"
    
    # Matches 'b'
    vars2 = {"view": {"o1": "b"}}
    assert loader._resolve_switch_macros(prompt, vars2).strip() == "same thing"
    
    # Matches 'c'
    vars3 = {"view": {"o1": "c"}}
    assert loader._resolve_switch_macros(prompt, vars3).strip() == "same thing"
    
    # Matches default
    vars4 = {"view": {"o1": "d"}}
    assert loader._resolve_switch_macros(prompt, vars4).strip() == "nothing"

def test_macro_switch_leapfrog_leakage():
    loader = PromptStackLoader()
    # Test that 'a' doesn't leak past default: to 'b:'
    prompt = "{ps_switch(view) | a | default: nothing | b: something}"
    
    vars1 = {"view": {"o1": "a"}}
    assert loader._resolve_switch_macros(prompt, vars1).strip() == "nothing"
    
def test_macro_switch_naked_default():
    loader = PromptStackLoader()
    # Test that default can be used as a fallthrough naked pipe
    prompt = "{ps_switch(view) | default | a: res}"
    
    vars1 = {"view": {"o1": "unknown_var_value"}}
    assert loader._resolve_switch_macros(prompt, vars1).strip() == "res"
    
    vars2 = {"view": {"o1": "a"}}
    assert loader._resolve_switch_macros(prompt, vars2).strip() == "res"

def test_macro_switch_space_agnostic():
    loader = PromptStackLoader()
    # Test that multi-line or padded variables match cleanly
    prompt = "{ps_switch(scene) | haha: found it | default: missed}"
    
    # Context variable comes in with newlines and spaces
    vars1 = {"scene": {"o1": "\n  haha\n"}}
    assert loader._resolve_switch_macros(prompt, vars1).strip() == "found it"
