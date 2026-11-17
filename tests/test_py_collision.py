"""Regression tests for the top-level `py` package collision.

Several custom node packs ship their own `py/` directory. A regular package (one with
an ``__init__.py``) is resolved ahead of namespace portions regardless of ``sys.path``
order, and the winner is cached in ``sys.modules`` for the life of the process, so
importing the base pack as bare ``py`` can silently resolve into a foreign pack. The
loader must not depend on that name.

Both tests run in a subprocess: module resolution is process-global state, and the
foreign package has to be bound to `py` before the loader is imported.
"""

import os
import subprocess
import sys
import textwrap

node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
adaptive_prompts_dir = os.path.abspath(os.path.join(node_root, "..", "comfyui-adaptiveprompts"))
loader_path = os.path.join(node_root, "py", "prompt_stack_loader.py")
init_path = os.path.join(node_root, "__init__.py")

FOREIGN_SETUP = """\
import sys

# A second pack owns the name `py`, as happens in a real custom_nodes folder.
sys.path.insert(0, {foreign!r})
import py
assert getattr(py, "PACK", None) == "foreign", "precondition failed: foreign py did not win"
"""

LOADER_UNDER_TEST = """\
import importlib.util

spec = importlib.util.spec_from_file_location("loader_under_test", {loader!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

assert module.SeededRandom.__module__.startswith("_adaptiveprompts_core"), (
    "SeededRandom resolved to " + module.SeededRandom.__module__ + " instead of the base pack"
)
assert module.DEFAULT_WILDCARD_ROOT is not None
assert callable(module.evaluate_prompt_core)

# The foreign pack keeps its own `py` package.
assert py.PACK == "foreign"
"""

PACK_INIT_UNDER_TEST = """\
import importlib.util

# ComfyUI loads a pack as a bare module, so the relative import in __init__.py always
# fails there and the path-based fallback is the path actually exercised in production.
spec = importlib.util.spec_from_file_location("pack_under_test", {init!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

assert "PromptStackLoader" in module.NODE_CLASS_MAPPINGS
assert py.PACK == "foreign"
"""


def _make_foreign_pack(tmp_path):
    """A stand-in for another custom node pack that ships a regular py/ package."""
    pack = tmp_path / "foreign-pack"
    (pack / "py").mkdir(parents=True)
    (pack / "py" / "__init__.py").write_text("PACK = 'foreign'\n", encoding="utf-8")
    return pack


def _run(script):
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)


def _build(tmp_path, body):
    script = FOREIGN_SETUP.format(foreign=str(_make_foreign_pack(tmp_path))) + "\n" + body
    return textwrap.dedent(script)


def test_loader_imports_when_another_pack_owns_the_py_package(tmp_path):
    result = _run(_build(tmp_path, LOADER_UNDER_TEST.format(loader=loader_path)))
    assert result.returncode == 0, f"loader failed with a foreign `py` package present:\n{result.stderr}"


def test_pack_init_loads_as_bare_module_with_foreign_py(tmp_path):
    result = _run(_build(tmp_path, PACK_INIT_UNDER_TEST.format(init=init_path)))
    assert result.returncode == 0, f"pack __init__ failed with a foreign `py` package present:\n{result.stderr}"


CONFIG_SYNC_UNDER_TEST = """\
import importlib.util
import os
import sys

# 1. Load extension first (creates _adaptiveprompts_core.config)
spec = importlib.util.spec_from_file_location("loader_under_test", {loader!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

# 2. Simulate ComfyUI loading the base pack afterwards under its own package name
base_config_path = os.path.join({base_dir!r}, "py", "config.py")
if os.path.isfile(base_config_path):
    cfg_spec = importlib.util.spec_from_file_location("host_adaptiveprompts.py.config", base_config_path)
    host_cfg = importlib.util.module_from_spec(cfg_spec)
    sys.modules[cfg_spec.name] = host_cfg
    cfg_spec.loader.exec_module(host_cfg)

    # Mutate the host config singleton in memory (as /adaptive_prompts/config does)
    host_cfg.config_instance.config["missing_wildcard_behavior"] = "Silently Fail"
    module._sync_base_config()

    synth_cfg = sys.modules["_adaptiveprompts_core.config"]
    assert synth_cfg.config_instance is host_cfg.config_instance
    assert synth_cfg.get_config("missing_wildcard_behavior") == "Silently Fail"
"""


def test_config_singleton_syncs_when_extension_loads_before_base_pack(tmp_path):
    body = CONFIG_SYNC_UNDER_TEST.format(loader=loader_path, base_dir=adaptive_prompts_dir)
    result = _run(_build(tmp_path, body))
    assert result.returncode == 0, f"config singleton sync failed:\n{result.stderr}"
