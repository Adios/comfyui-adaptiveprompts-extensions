import sys
import os
import unittest
import importlib.util

if 'py' in sys.modules:
    del sys.modules['py']

# Purge any extensions paths from sys.path to prevent Python from treating its 'py' folder as a package
ext_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path = [p for p in sys.path if ext_root not in os.path.abspath(p)]

# Append core directory
core_dir = os.path.abspath(os.path.join(ext_root, '..', 'comfyui-adaptiveprompts'))
sys.path.insert(0, core_dir)

# Import from core
from py.plugin_registry import PluginRegistry
from py.generator import SeededRandom, resolve_wildcards, find_next_bracket_span, _split_top_level_pipes

# Import extension logic by direct file path since python package collision is a headache
ext_py_path = os.path.join(ext_root, 'py', 'syntax_extensions.py')
spec = importlib.util.spec_from_file_location("syntax_extensions", ext_py_path)
syntax_extensions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(syntax_extensions)

PluginRegistry.register_bracket_handler(syntax_extensions.conditional_bracket_handler)

class TestGenerator(unittest.TestCase):
    def setUp(self):
        self.wildcard_dir = "wildcards"

    def test_find_next_bracket_span(self):
        span = find_next_bracket_span("hello { a | { b | c } } world")
        self.assertEqual(span, (6, 22))

    def test_split_top_level_pipes(self):
        parts = _split_top_level_pipes("if(var==(prompt: 1.2)) | A | B")
        self.assertEqual(parts, ["if(var==(prompt: 1.2)) ", " A ", " B"])

    def test_lazy_evaluation(self):
        res = resolve_wildcards("{ a | { b | c } }", SeededRandom(0), self.wildcard_dir)
        self.assertEqual(res.strip(), "a")

    def test_weight_recursion_limit(self):
        from py.syntax_extensions import _get_statement_weight
        # 15 nested parens. The limit is 12, so it will multiply 1.1 for 12 layers
        # and then cap out, preventing RecursionError.
        nested = "(((((((((((((((ninja)))))))))))))))"
        weight = _get_statement_weight(nested)
        self.assertAlmostEqual(weight, 1.1 ** 12)

    def test_variable_assignment(self):
        res = resolve_wildcards("{hello}^varA { __^varA__ }", SeededRandom(0), self.wildcard_dir)
        self.assertEqual(res.strip(), "hello hello")

    def test_if_defined(self):
        vars1 = {"view": {"origin": "close-up"}}
        res1 = resolve_wildcards("{if(?view) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res1.strip(), "Yes")

        res2 = resolve_wildcards("{if(?unknown) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res2.strip(), "No")

    def test_if_operators(self):
        vars1 = {"view": {"origin": "close-up shot"}}
        # Contains
        res = resolve_wildcards("{if(view~close) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res.strip(), "Yes")
        # Not contains
        res = resolve_wildcards("{if(view!~close) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res.strip(), "No")
        # Equals
        res = resolve_wildcards("{if(view==close-up shot) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res.strip(), "Yes")
        # Not equals
        res = resolve_wildcards("{if(view!=close-up shot) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res.strip(), "No")

    def test_if_multiline(self):
        prompt = "{if(?view)\n  | do\n  | do not\n  }"
        vars1 = {"view": {"origin": "close-up"}}
        res = resolve_wildcards(prompt, SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res.strip(), "do")

    def test_switch_basic(self):
        prompt = "{switch(view)\n  | close-up: (face:1.2)\n  | full-body: shoes\n  | default: nothing\n  }"
        vars1 = {"view": {"origin": "close-up"}}
        res1 = resolve_wildcards(prompt, SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res1.strip(), "(face:1.2)")

        vars2 = {"view": {"origin": "unknown"}}
        res2 = resolve_wildcards(prompt, SeededRandom(0), self.wildcard_dir, _resolved_vars=vars2)
        self.assertEqual(res2.strip(), "nothing")

    def test_switch_lazy_default(self):
        prompt = "{switch(view)\n  | close-up: A\n  | default: { B | C }\n  }"
        vars1 = {"view": {"origin": "unknown"}}
        res1 = resolve_wildcards(prompt, SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertIn(res1.strip(), ["B", "C"])

    def test_if_numeric_operators(self):
        vars1 = {"test": {"o1": "(masterpiece: 1.2)"}}
        # Greater than
        res = resolve_wildcards("{if(test>1.0) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res.strip(), "Yes")
        # Less than
        res = resolve_wildcards("{if(test<1.0) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res.strip(), "No")
        # Greater than or equal
        res = resolve_wildcards("{if(test>=1.2) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res.strip(), "Yes")
        # Default weight 1.0
        vars2 = {"test": {"o1": "masterpiece"}}
        res = resolve_wildcards("{if(test>0.5) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars2)
        self.assertEqual(res.strip(), "Yes")

    def test_if_numeric_robustness(self):
        # Nested fully-wrapped weight string ((a: 1.2), (b: 1.1))
        # Top level is ( ... ), so it gets 1.1x multiplier. Inner is "(a: 1.2), (b: 1.1)" which is NOT fully wrapped, so inner returns 1.0.
        # Total = 1.1 * 1.0 = 1.1.
        vars1 = {"test": {"o1": "((a: 1.2), (b: 1.1))"}}
        res = resolve_wildcards("{if(test > 1.05) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res.strip(), "Yes")

        # Malformed weight string (unclosed parens) should default to 1.0
        vars2 = {"test": {"o1": "((a: 1.2), (b: 1.1)"}}
        res = resolve_wildcards("{if(test > 0.8) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars2)
        self.assertEqual(res.strip(), "Yes")

        # Fully wrapped multiplier sequence [[masterpiece]]
        # 0.9 * 0.9 = 0.81
        vars3 = {"test": {"o1": "[[masterpiece]]"}}
        res = resolve_wildcards("{if(test < 0.85) | Yes | No}", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars3)
        self.assertEqual(res.strip(), "Yes")

    def test_conditional_toggle_opt_in(self):
        # When enable_conditionals=False (default), it should treat {if...} as a standard random choice
        prompt = "{if(?view) | Yes | No}"
        vars1 = {"view": {"origin": "close-up"}}
        
        # Test 10 times to ensure it's acting like a random choice (roulette) between "if(?view)", "Yes", and "No"
        results = set()
        for i in range(20):
            res = resolve_wildcards(prompt, SeededRandom(i), self.wildcard_dir, _resolved_vars=vars1, enable_conditionals=False)
            results.add(res.strip())
        
        # It should contain the "if(?view)" string itself because it's just a roulette choice
        self.assertIn("if(?view)", results)
        self.assertIn("Yes", results)
        self.assertIn("No", results)

    def test_conditional_inside_wildcard_file(self):
        # Create a temporary wildcard file
        os.makedirs(self.wildcard_dir, exist_ok=True)
        wildcard_path = os.path.join(self.wildcard_dir, "logic_test.txt")
        with open(wildcard_path, "w") as f:
            f.write("{if(?view) | inside-wildcard-yes | inside-wildcard-no}")

        try:
            vars1 = {"view": {"o1": "close-up"}}
            # Test with variable defined
            res1 = resolve_wildcards("__logic_test__", SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
            self.assertEqual(res1.strip(), "inside-wildcard-yes")

            # Test with variable undefined
            res2 = resolve_wildcards("__logic_test__", SeededRandom(0), self.wildcard_dir, _resolved_vars={})
            self.assertEqual(res2.strip(), "inside-wildcard-no")
        finally:
            if os.path.exists(wildcard_path):
                os.remove(wildcard_path)


    def test_switch_complex_matching(self):
        from py.generator import SeededRandom, resolve_wildcards
        prompt = "{switch(view)\n  | (small breasts: 0.8): A\n  | ~large: B\n  | default: C\n  }"
        
        # Test 1: exact match
        vars1 = {"view": {"o1": "(small breasts: 0.8)"}}
        res1 = resolve_wildcards(prompt, SeededRandom(0), self.wildcard_dir, _resolved_vars=vars1)
        self.assertEqual(res1.strip(), "A")

        # Test 4: default
        vars4 = {"view": {"o1": "unknown"}}
        res4 = resolve_wildcards(prompt, SeededRandom(0), self.wildcard_dir, _resolved_vars=vars4)
        self.assertEqual(res4.strip(), "C")
        



if __name__ == '__main__':
    unittest.main()
