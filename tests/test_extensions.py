import sys
import os
import pytest
import importlib.util

# Remove pytest's 'py'
if 'py' in sys.modules:
    del sys.modules['py']

# Load mother project modules dynamically to avoid namespace collisions
mother_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'comfyui-adaptiveprompts'))

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

plugin_registry_module = load_module("py.plugin_registry", os.path.join(mother_dir, "py", "plugin_registry.py"))
sys.modules["custom_nodes.comfyui-adaptiveprompts.py.plugin_registry"] = plugin_registry_module

config_module = load_module("py.config", os.path.join(mother_dir, "py", "config.py"))
sys.modules["custom_nodes.comfyui-adaptiveprompts.py.config"] = config_module

# Now we can safely load __init__ as a named package
spec = importlib.util.spec_from_file_location("adaptiveprompts_ext", os.path.join(os.path.dirname(__file__), '..', '__init__.py'))
ext_module = importlib.util.module_from_spec(spec)
sys.modules["adaptiveprompts_ext"] = ext_module
spec.loader.exec_module(ext_module)

generator_module = load_module("py.generator", os.path.join(mother_dir, "py", "generator.py"))
SeededRandom = generator_module.SeededRandom
resolve_wildcards = generator_module.resolve_wildcards

@pytest.fixture
def wildcard_dir(tmp_path):
    d = tmp_path / "wildcards"
    d.mkdir()
    yield str(d)

def test_switch_basic(wildcard_dir):
    prompt = "{switch(view)\n  | close-up: (face:1.2)\n  | full-body: shoes\n  | default: nothing\n  }"
    vars1 = {"view": {"origin": "close-up"}}
    res1 = resolve_wildcards(prompt, SeededRandom(0), wildcard_dir, _resolved_vars=vars1, enable_conditionals=True)
    assert res1.strip() == "(face:1.2)"

    vars2 = {"view": {"origin": "unknown"}}
    res2 = resolve_wildcards(prompt, SeededRandom(0), wildcard_dir, _resolved_vars=vars2, enable_conditionals=True)
    assert res2.strip() == "nothing"

def test_switch_lazy_default(wildcard_dir):
    prompt = "{switch(view)\n  | close-up: A\n  | default: { B | C }\n  }"
    vars1 = {"view": {"origin": "unknown"}}
    res1 = resolve_wildcards(prompt, SeededRandom(0), wildcard_dir, _resolved_vars=vars1, enable_conditionals=True)
    assert res1.strip() in ["B", "C"]

def test_switch_complex_matching(wildcard_dir):
    prompt = "{switch(view)\n  | (small breasts: 0.8): A\n  | ~large: B\n  | default: C\n  }"
    
    # Test 1: exact match
    vars1 = {"view": {"o1": "(small breasts: 0.8)"}}
    res1 = resolve_wildcards(prompt, SeededRandom(0), wildcard_dir, _resolved_vars=vars1, enable_conditionals=True)
    assert res1.strip() == "A"

    # Test 4: default
    vars4 = {"view": {"o1": "unknown"}}
    res4 = resolve_wildcards(prompt, SeededRandom(0), wildcard_dir, _resolved_vars=vars4, enable_conditionals=True)
    assert res4.strip() == "C"
