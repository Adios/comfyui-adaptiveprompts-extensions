# Adaptive Prompts Extensions

This is a companion extension node for `comfyui-adaptiveprompts` that adds advanced, experimental, or specific workflow-related features to the core engine.

## 📦 Installation
If you are not using ComfyUI Manager (or if the node is not yet available in the Manager's registry), you can install this extension manually:

1. Open your terminal or command prompt.
2. Navigate to your ComfyUI `custom_nodes` directory:
   ```bash
   cd ComfyUI/custom_nodes/
   ```
3. Clone this repository:
   ```bash
   git clone https://github.com/your-username/comfyui-adaptiveprompts-extensions.git
   ```
4. Restart ComfyUI.

### Dependencies
This extension strictly depends on the core **Adaptive Prompts** engine. You **MUST** have the official `comfyui-adaptiveprompts` node installed in your `custom_nodes` folder for this extension to function.

## Nodes Included

## 🥞 Prompt Stack Loader
<img src="images/prompt_stack_loader.png"/>

The **Prompt Stack Loader** is a powerful node designed to sequentially load, parse, and evaluate a stack of text files. Instead of constructing unwieldy monolithic prompt strings, it serves as an orchestrator for **modular prompt configurations** where your logic is distributed across multiple files.

### Why Use a Stack Loader?
By leveraging this node, you can transition your workflow into a highly adaptable **profile-based system**:
*   **Rapid Context Switching:** Easily switch between different subjects by changing a single line in your stack, automatically pulling in their associated LoRAs and custom styling.
*   **Dynamic Adaptation:** Your main Prompt Generator template dynamically adapts to the current subject, making the process seamless.
*   **Scalability:** The system is designed around a production-ready **Subject Hook Architecture**, allowing you to build complex, highly-detailed prompts cleanly and modularly. 

### Key Features
*   **Sequential File Processing:** Load and evaluate a list of text files (a "stack") in order. File paths can be absolute, relative, or specifically pull a random file from a directory.
*   **Dynamic Stack Modifiers:** Use inline commands like `remove:path/to/file.txt` or `replace:old_path|new_path` to surgically alter a master stack template without changing the underlying files.
*   **Automatic LoRA Tag Extraction:** Any `<lora:name:weight>` tags found within your stacked files are automatically extracted and compiled into a clean `lora_string` output to be sent directly to a LoRA Tag Loader.
*   **Context Accumulation & Overrides:** As files are processed, variables are collected into a `context` dictionary. You can pass context between multiple Stack Loaders, using the **Override** toggle to cleanly re-roll specific elements (like a character's expression) while keeping the rest of the generation state identical.

For a deeper dive into the **Architecture Philosophy** and ready-to-use template examples, please refer to the **[Real-World Example Stacks Documentation](prompt_examples/README.md)**.

A ready-to-use example workflow is also available: **[Prompt Stack Loader Three Scenes Pipeline](workflow/PromptStackLoaderThreeScenesPipeline.json)**.

### Node Parameters & Stack Syntax

- **`base_dir`**: The foundational directory for resolving file paths.
  - **Empty:** Resolves to the `prompts` directory within this extension's folder (`comfyui-adaptiveprompts/prompts/`).
  - **Relative Path:** Resolves relative to this extension's folder (e.g., `my_prompts` resolves to `comfyui-adaptiveprompts/my_prompts/`).
  - **Absolute Path:** Utilizes the absolute directory directly on your system.
- **`stack_file`**: The path to a `.txt` file containing your execution stack (e.g., `stacks/illustrious.txt`).
- **`inline_stack`**: A multiline text field used to define paths or apply on-the-fly overrides directly within the node.
- **`override_context`**: Determines how incoming context is handled: **Merge** (appends new variables) or **Override** (replaces existing variables).

> **Note on Paths:** All file paths specified within a `stack_file` or the `inline_stack` can be either absolute paths or relative paths. Relative paths are always resolved against your defined `base_dir`. Furthermore, path resolution is designed to be **cross-platform safe**, meaning your stacks and folder paths will work seamlessly across Windows, macOS, and Linux.

**Inline Stack Commands:**
Each line within a stack acts as an instruction. Alongside standard file paths, you can use the following commands to modify the stack dynamically right from the node:
- `random:folder_path` -> Selects a random `.txt` file from the specified directory (includes subfolders).
- `remove:file_path` -> Excludes a specific file from being loaded (e.g., `remove:styles/cyberpunk.txt`). Because LoRA tags are always accumulated across the stack and ignore the **Override** toggle, excluding the file that contains the LoRA is the primary way to prevent it from being merged into your final `lora_string` output.
- `replace:old_file|new_file` -> Swaps a file in-place, allowing targeted modifications without editing the original stack file. 
  - e.g., `replace:lighting/sunny.txt|lighting/rainy.txt` or `replace:random:anime|anime/frieren/frieren.txt`.

### Node Outputs

- **`prompt` (STRING):** The fully evaluated, comma-separated string containing all the text and resolved wildcards from the processed files, with LoRA tags stripped out.
- **`context` (DICT):** The accumulated dictionary of variables created during the evaluation of the stack. This can be passed to a Prompt Generator or another Stack Loader.
- **`lora_string` (STRING):** A clean string containing all `<lora:name:weight>` tags found across all files in the stack, ready to be passed to a LoRA Tag Loader.

## 🔀 Conditional Branching & Logic
You can now program logic directly into your prompts! Using lightweight condition operators, you can tell the engine to output specific text only if certain variables are set or match specific values. This is incredibly useful for avoiding prompt conflicts (e.g., ensuring "shoes" doesn't appear in a "close-up" portrait).

> [!TIP]
> **When to use Conditionals vs Nodes:** 
> While you *can* program complex logic purely with text conditionals, **it is highly recommended to use nodes for macro-level choices.** For example, if you are designing entirely different scenes (like a "from above" layout vs. a "side view" layout), it is much easier to maintain separate Prompt Generators for each, and route them using nodes like `Random Integers` combined with `Switch Any (Impact Pack)`. 
> 
> Keep your text-based `if/else` and `switch` statements reserved for **micro-adjustments and fine-tuning** (such as resolving clothing conflicts, swapping a specific detail, or conditionally amplifying a tag's impact based on another variable).

### If / Else Statements
You can use `{if(condition) | then | else}` syntax. The `| else` part is optional.

**Lightweight Operators:**
*   `?var` -> **(Is Defined)** True if the variable `var` has been assigned.
*   `!var` -> **(Not Defined)** True if `var` has NOT been assigned.
*   `var~tag` -> **(Contains)** True if the variable's value contains `tag`.
*   `var!~tag` -> **(Not Contains)** True if it does NOT contain `tag`.
*   `var==value` -> **(Equals)** True if it exactly matches `value`.
*   `var!=value` -> **(Not Equals)** True if it does NOT exactly match.
*   `var>value`  -> **(Greater Than)** True if variable weight is greater than value.
*   `var<value`  -> **(Less Than)** True if variable weight is less than value.
*   `var>=value` -> **(Greater or Equal)** True if weight is greater or equal.
*   `var<=value` -> **(Less or Equal)** True if weight is less or equal.

**Examples:**
```text
# Ensure panties are only visible if the shot is full-body
{if(shot==full body) | panties}

# Only output if a variable's weight is emphasized (above 1.0)
{if(lighting > 1.0) | (bloom:1.2) | }

# Only output if a variable exists
{if(?character_name) | 1girl, __^character_name__ | 1girl, generic face}

# The else branch is executed if the condition fails
{if(view~close-up) | detailed face | (detailed background:1.2)}
```

### Switch Statements
For more complex branching based on a single variable, you can use the `switch(var)` statement. It checks the variable and executes the matching `case:`.

```text
{switch(view)
  | close-up: (detailed face:1.2), portrait
  | full-body: standing, shoes, full shot
  | cowboy: cowboy shot, belt
  | default: 
      # The default branch executes if no cases match
      {some|other|wildcard}
}
```

> [!WARNING]
> **Switch Case Limitation:** Because `switch` syntax relies on `:` as a delimiter (`| case: result`), it cannot safely match exact strings that contain unwrapped colons (e.g., `apple:, banana:`). The parser will incorrectly split the case at the first unwrapped colon it finds. If you need to match against a string containing colons, you should use an `if` statement instead: `{if(var==apple:, banana:) | result}`. Note that structural weights safely wrapped in parentheses like `(apple: 0.8)` will still parse perfectly in switch cases.

*Note: The engine natively uses **Lazy Evaluation**. Unchosen branches are completely ignored, meaning any variables (`^var`) assigned inside an unchosen branch will safely NOT be executed.*

> [!WARNING]
> **Macro-Like Implementation & Lazy Evaluation Injection**
> Unlike mainstream template systems (like Jinja) that build a complete Abstract Syntax Tree (AST), this engine uses a regex-based, macro-like string substitution mechanism. This means variables are resolved *before* conditional structures are parsed.
> 
> As a result, variables can "inject" logic:
> 1. **Operator Injection:** If you write `{if({var})} | A | B}` and `{var}` resolves to `hello == there`, the condition literally becomes `hello == there` and evaluates accordingly.
> 2. **Keyword Injection (NOT Possible):** Fortunately, writing `{ {var} | A | B }` where `{var}` resolves to `if(shot==full body)` will **NOT** inject a conditional block. The parser checks for the `if(...)` keyword *before* resolving wildcards on the choice string itself, so it acts safely as a standard roulette block.
> 
> Treat this as a powerful feature of lazy evaluation, but be careful not to accidentally inject operators (`==`, `>`, `~`) inside the `if(...)` parenthesis via wildcards if you do not intend to evaluate them as logic.

### Execution Limits & Safety
- **Inline Nesting (Max 12 Passes):** You can nest conditionals inline (e.g., `{if(A)|{if(B)|...}}`) up to roughly 12 passes deep. Exceeding this will leave any deeper brackets unresolved as raw text.
- **File Recursion (Max 80 Depth):** If your conditional logic relies on wildcard files that recursively call other wildcard files with conditionals, execution is capped at a depth of 80 to prevent infinite loops.

### Compatibility with Comments & Wildcards
- **`## Comment Blocks ##`**: Conditional statements are fully supported inside comment blocks! Any variables assigned inside a successfully evaluated conditional branch within a comment block will be stored and preserved, even though the comment text itself is hidden from the final prompt.
- **Wildcard Files**: You can absolutely use conditionals inside `.txt` wildcard files. However, because wildcard files are read **line-by-line**, your entire `{if(...)|...}` statement must be written on a single line. Multiline if-statements will break inside wildcards.

### Reserved Syntax
By introducing this feature, the following bracket syntaxes are now "reserved" by the engine:
- `{if( ... ) | ... }`
- `{switch( ... ) | ... }`

If you genuinely want to generate the exact string `{if(apple) | yes | no}` in your prompt, these are now reserved words so the engine will always attempt to evaluate them as logic. If `apple` is undefined, the branch will evaluate to false and output `no`. Note that the condition parser expects strict syntax without spaces in the variable name (e.g., `if(my_var)` works, `if(my var)` does not and will fail gracefully, outputting raw text).

