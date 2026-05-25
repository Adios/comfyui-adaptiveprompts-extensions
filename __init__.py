import sys
import os

try:
    from .py.prompt_stack_loader import PromptStackLoader
except ImportError:
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    from py.prompt_stack_loader import PromptStackLoader
    sys.path.pop(0)

NODE_CLASS_MAPPINGS = {
    "PromptStackLoader": PromptStackLoader,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptStackLoader": "🥞 Prompt Stack Loader 🥞",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
