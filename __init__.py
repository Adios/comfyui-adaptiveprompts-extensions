import sys
import os

# Make sure we can import from comfyui-adaptiveprompts
adaptive_prompts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "comfyui-adaptiveprompts"))
if adaptive_prompts_dir not in sys.path:
    sys.path.insert(0, adaptive_prompts_dir)

try:
    from py.plugin_registry import PluginRegistry
    from .py.syntax_extensions import conditional_bracket_handler, wildcard_strip_handler
    
    PluginRegistry.register_bracket_handler(conditional_bracket_handler)
    PluginRegistry.register_wildcard_handler(wildcard_strip_handler)
    print("[Adaptive Prompts Extensions] Registered syntax extensions (Conditional Branching, Strip Variables).")
except ImportError:
    pass

from .py.prompt_stack_loader import PromptStackLoader

NODE_CLASS_MAPPINGS = {
    "PromptStackLoader": PromptStackLoader,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptStackLoader": "🥞 Prompt Stack Loader 🥞",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
