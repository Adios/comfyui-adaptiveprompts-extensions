import importlib.util
import os
import sys

try:
    from .py.prompt_stack_loader import PromptStackLoader
except ImportError:
    # ComfyUI loads a custom node pack as a bare module rather than as part of a
    # package, so the relative import above cannot resolve there. Fall back to loading
    # the file by path, rather than importing the generic top-level name `py`, which
    # other custom node packs also provide (and may already own in sys.modules).
    _loader_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "py", "prompt_stack_loader.py")
    _spec = importlib.util.spec_from_file_location("comfyui_adaptiveprompts_extensions.prompt_stack_loader", _loader_path)
    if _spec is None or _spec.loader is None:
        raise ImportError(f"The 'comfyui-adaptiveprompts-extensions' node could not load {_loader_path}")
    _loader_module = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _loader_module
    _spec.loader.exec_module(_loader_module)
    PromptStackLoader = _loader_module.PromptStackLoader

NODE_CLASS_MAPPINGS = {
    "PromptStackLoader": PromptStackLoader,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptStackLoader": "🥞 Prompt Stack Loader 🥞",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
