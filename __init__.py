import sys
import os

# Make sure we can import from comfyui-adaptiveprompts
adaptive_prompts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "comfyui-adaptiveprompts"))
if adaptive_prompts_dir not in sys.path:
    sys.path.insert(0, adaptive_prompts_dir)

try:
    from py.plugin_registry import PluginRegistry
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
