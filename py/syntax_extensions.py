import re

# We import resolve_wildcards from the core to evaluate dynamic variables inside conditional statements
try:
    from py.generator import resolve_wildcards
    from py.string_utils import PromptCleanup
except ImportError:
    resolve_wildcards = None
    PromptCleanup = None
