import sys
import os

# Hack to avoid Python dual-import singleton bugs.
# ComfyUI loads the mother project into sys.modules under a specific name (e.g. custom_nodes.comfyui-adaptiveprompts...)
# If we use sys.path.insert and import it ourselves, we create a SECOND instance of the PluginRegistry class,
# which the mother project will never see!
PluginRegistry = None
for mod_name, mod in list(sys.modules.items()):
    if mod_name.endswith(".py.plugin_registry") and "adaptiveprompt" in mod_name.lower():
        if hasattr(mod, "PluginRegistry"):
            PluginRegistry = mod.PluginRegistry
            break

# Still insert into sys.path so syntax_extensions can import pure functions like resolve_wildcards
adaptive_prompts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "comfyui-adaptiveprompts"))
if adaptive_prompts_dir not in sys.path:
    sys.path.insert(0, adaptive_prompts_dir)

if PluginRegistry is not None:
    try:
        from .py.syntax_extensions import conditional_bracket_handler, wildcard_strip_handler
        PluginRegistry.register_bracket_handler(conditional_bracket_handler)
        PluginRegistry.register_wildcard_handler(wildcard_strip_handler)
        print("[Adaptive Prompts Extensions] Registered syntax extensions (Conditional Branching, Strip Variables).")
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
