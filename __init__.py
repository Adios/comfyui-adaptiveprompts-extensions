import sys
import os

PluginRegistry = None
for mod_name, mod in list(sys.modules.items()):
    if mod_name.endswith(".py.plugin_registry") and "adaptiveprompt" in mod_name.lower():
        if hasattr(mod, "PluginRegistry"):
            PluginRegistry = mod.PluginRegistry
            break

adaptive_prompts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "comfyui-adaptiveprompts"))
if adaptive_prompts_dir not in sys.path:
    sys.path.insert(0, adaptive_prompts_dir)

if PluginRegistry is not None:
    try:
        from .py.syntax_extensions import conditional_bracket_handler, conditional_bypass_handler
        PluginRegistry.register_bracket_handler(conditional_bracket_handler)
        PluginRegistry.register_bypass_handler(conditional_bypass_handler)
        print("[Adaptive Prompts Extensions] Registered syntax extensions (Conditional Branching).")
    except Exception as e:
        print(f"[Adaptive Prompts Extensions] Error registering syntax: {e}")

from .py.prompt_stack_loader import PromptStackLoader

NODE_CLASS_MAPPINGS = {
    "PromptStackLoader": PromptStackLoader,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptStackLoader": "🥞 Prompt Stack Loader 🥞",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
