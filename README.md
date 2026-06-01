# Adaptive Prompts Extensions

This is a companion extension node for [comfyui-adaptiveprompts](https://github.com/Alectriciti/comfyui-adaptiveprompts) that adds advanced, experimental, or specific workflow-related features to the core engine.

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
This extension strictly depends on the core **Adaptive Prompts** engine. You **MUST** have the official [comfyui-adaptiveprompts](https://github.com/Alectriciti/comfyui-adaptiveprompts) node installed in your `custom_nodes` folder for this extension to function.

---

## 🧩 Nodes Included

### 🥞 Prompt Stack Loader
<img src="images/prompt_stack_loader.png"/>

The **Prompt Stack Loader** is a powerful node designed to sequentially load, parse, and evaluate a stack of text files. Instead of constructing unwieldy monolithic prompt strings, it serves as an orchestrator for **modular prompt configurations** where your logic is distributed across multiple files.

#### Why Use a Stack Loader?
By leveraging this node, you can transition your workflow into a highly adaptable **profile-based system**:
*   **Rapid Context Switching:** Easily switch between different subjects by changing a single line in your stack, automatically pulling in their associated LoRAs and custom styling.
*   **Dynamic Adaptation:** Your main Prompt Generator template dynamically adapts to the current subject, making the process seamless.
*   **Scalability:** The system is designed around a production-ready **Subject Hook Architecture**, allowing you to build complex, highly-detailed prompts cleanly and modularly. 

#### Key Features
*   **Sequential File Processing:** Load and evaluate a list of text files (a "stack") in order. File paths can be relative or specifically pull a random file from a directory.
*   **Dynamic Stack Modifiers:** Use inline commands like `remove:path/to/file.txt` or `replace:old_path|new_path` to surgically alter a master stack template without changing the underlying files.
*   **Hidden Files & Local Overrides:** Any file or directory starting with `_` or `.` is completely hidden from the randomizer. This allows you to safely store specialized states or drafts (e.g., `_explicit_override.txt`) alongside your main prompts, loading them only when you explicitly ask for them!
*   **Automatic LoRA Tag Extraction:** Any `<lora:name:weight>` tags found within your stacked files are automatically extracted and compiled into a clean `lora_string` output to be sent directly to a LoRA Tag Loader.
*   **Context Accumulation & Overrides:** As files are processed, variables are collected into a `context` dictionary. You can pass context between multiple Stack Loaders, using the **Override** toggle to cleanly re-roll specific elements (like a character's expression) while keeping the rest of the generation state identical.

For a deeper dive into the **Architecture Philosophy** and ready-to-use template examples, please refer to the **[Real-World Example Stacks Documentation](prompt_examples/README.md)**.

A ready-to-use example workflow is also available: **[Prompt Stack Loader Three Scenes Pipeline](workflow/PromptStackLoaderThreeScenesPipeline.json)**.

#### Node Parameters & Stack Syntax

- **`base_dir`**: The foundational directory for resolving file paths.
  - **Empty:** Resolves to the `prompts` directory within this extension's folder.
  - **Security Restrictions:** Absolute paths and escaping the base directory via `../` are explicitly disabled. Examples:
    - **Allowed:** `subfolder/file.txt`, `subfolder/../file.txt` (stays inside `base_dir`)
    - **Blocked:** `C:\file.txt`, `/etc/passwd`, `../../outside_file.txt` (escapes `base_dir`)
    Symlinks pointing outside the base directory are blocked, and files exceeding 5MB are skipped.
- **`stack_file`**: The path to a `.txt` file containing your execution stack (e.g., `stacks/illustrious.txt`). *(Note: Unlike the paths inside the stack, this file is allowed to use `../` to escape your `base_dir`, as long as it remains inside the extension folder).*
- **`inline_stack`**: A multiline text field used to define paths or apply on-the-fly overrides directly within the node.
- **`override_context`**: Determines how incoming context is handled: **Merge** (appends new variables) or **Override** (replaces existing variables).

> **Note on Paths & Security:** All file paths specified within a `stack_file` or the `inline_stack` must be relative paths. Relative paths are always resolved against your defined `base_dir`. Path resolution is **cross-platform safe** (works across Windows/macOS/Linux) and strictly sandboxed. Symlinks that point outside the `base_dir` are strictly forbidden, and files exceeding 5MB are automatically skipped.

**Inline Stack Commands:**
Each line within a stack acts as an instruction. Alongside standard file paths, you can use the following commands to modify the stack dynamically right from the node:
- `random:folder_path` -> Selects a random `.txt` file from the specified directory (includes subfolders). *(Note: Files and folders starting with `_` or `.` are ignored).*
- `remove:file_path` -> Excludes a specific file from being loaded (e.g., `remove:styles/cyberpunk.txt`). Because LoRA tags are always accumulated across the stack and ignore the **Override** toggle, excluding the file that contains the LoRA is the primary way to prevent it from being merged into your final `lora_string` output.
- `replace:old_file|new_file` -> Swaps a file in-place, allowing targeted modifications without editing the original stack file. 
  - e.g., `replace:lighting/sunny.txt|lighting/rainy.txt` or `replace:random:anime|anime/frieren/frieren.txt`.

**Local Overrides & Hidden Files:**
A powerful workflow technique is to prefix files or directories with `_` or `.` (e.g., `_action_pose.txt` or `_WIP`). Because these are ignored by the randomizer, they won't randomly appear in your generations. However, you can still load them *explicitly* by writing their exact path in your stack (e.g., `anime/neon_genesis_evangelion/_asuka_override.txt`). This makes managing complex "Context Override" modes much cleaner, as your base options and their specific overrides can live safely in the exact same directory without interfering with each other.

#### Node Outputs

- **`prompt` (STRING):** The fully evaluated, comma-separated string containing all the text and resolved wildcards from the processed files, with LoRA tags stripped out.
- **`context` (DICT):** The accumulated dictionary of variables created during the evaluation of the stack. This can be passed to a Prompt Generator or another Stack Loader.
- **`lora_string` (STRING):** A clean string containing all `<lora:name:weight>` tags found across all files in the stack, ready to be passed to a LoRA Tag Loader.

---

## 🔀 Advanced Macros: `ps_switch`

The Prompt Stack Loader includes a built-in pre-processor macro for conditional branching across files. This is extremely useful when an action or clothing choice needs to adapt perfectly to a background scene you loaded earlier in the stack.

For example, imagine a global stack that first loads a specific background scene:
`backgrounds/scenes/urban/cafe_interior.txt`
This file sets the background details, and defines a scene variable:
`{cafe}^scene`

Later in the same stack (or in a separate downstream Stack Loader), you load an action file, for example, `actions/leisure/drinking.txt`. Because the scene variable was established upstream, you can use the `ps_switch(var)` macro to contextually adapt the pose:

```text
{
  dynamic pose,
  {ps_switch(scene)
    | cafe: sitting at table, holding a coffee cup
    | swimming pool | beach: splashing water, looking at the horizon
    | default: standing casually
  }
}^action
```

**How it works:**
*   **Contextual Matching:** The `PromptStackLoader` checks its accumulated context for the variable `scene`. If it finds `cafe`, it swaps the entire `ps_switch` block with `sitting at table, holding a coffee cup`.
*   **Shared Results (Fallthrough):** You can share a single result across multiple cases by separating them with pipes (e.g., `| swimming pool | beach: splashing water`).
*   **Default Fallback:** If the variable resolves to something else, or isn't defined at all, it outputs the `default:` branch.
*   **Safe "Pass-Through":** If the variable `scene` is completely missing from the upstream context, the macro safely resolves to the `default:` fallback (or an empty string if no default is provided) to prevent syntax errors.

> **Note:** Because `ps_switch` runs as a stack-level macro *before* core prompt evaluation, it relies entirely on variables defined in *previous* files in the stack. If you assign a variable and attempt to switch on it within the **exact same file**, it will use the old upstream value instead.

---

## 💡 Pro Tips & Advanced Workflows

### 🔗 Chained Loaders & Global States

The true power of the `PromptStackLoader` shines when chaining multiple nodes together via the `context` ports. This gracefully bypasses `base_dir` path restrictions and allows you to establish global states (like SFW/explicit modes, weather, or time of day) that dynamically control downstream subjects—even when those subjects are selected randomly!

By pairing chained loaders with the `ps_switch` macro, you can encapsulate all character variations directly inside their own files, allowing upstream loaders to dictate their state.

**Example: Global Modifiers with Random Subjects**

```text
[ Node 1: Global State ]
base_dir: prompts/vars
inline_stack: mode_explicit.txt (contains: {explicit}^mode)
         |
    (context out)
         |
[ Node 2: Subject Generator ]
base_dir: prompts/anime
inline_stack: random:characters
```

Because Node 1 passes the `mode` variable downstream, any random character picked by Node 2 (e.g., `characters/frieren.txt`) instantly reacts to the global state:

```text
# frieren.txt

{{ps_switch(mode)
  | explicit: revealing white dress, collarbone
  | default: classic white dress, winter coat
}}^outfit
```

This architecture keeps your prompt files highly modular, ensures `random:` selection remains powerful without breaking, and completely eliminates the need for messy override scripts or folder structures.

### 🔄 Self-Referential Overrides (Infinite Stacking)

A powerful way to keep your master templates clean is to use **self-referential variables** to modify existing states without needing dedicated placeholder variables (like `__^outfit_state__`) in your base template.

Because variables defined later in the stack seamlessly overwrite earlier ones, an override file can recall its own previous value, append a new modifier, and save it back!

**Example: Stacking Outfit Modifiers**
Imagine you have a base template that simply calls `__^outfit__`.
1. Your stack first loads `outfits/school_uniform.txt`, which contains:
   `{white shirt, blue pleated skirt}^outfit`
2. Your stack then loads an override state file like `outfit_states/_wet.txt`. Instead of setting a brand new variable, it references the existing one:
   `{__^outfit__, wet seethrough fabric}^outfit`
3. Finally, your stack loads `outfit_states/_torn.txt`:
   `{__^outfit__, torn edges}^outfit`

**The Result:** The engine elegantly accumulates the values: `white shirt, blue pleated skirt, wet seethrough fabric, torn edges`—all stored perfectly under the single `^outfit` variable. 

This guarantees your main templates stay completely free of bloat, while giving you the flexibility to infinitely stack states (wet, dirty, torn) just by chaining text files!

> **CRITICAL:** For this pattern to work correctly, your `PromptStackLoader` node **must have `override_context` set to `Override`**. If set to `Merge`, the engine will keep both the old and new outfit strings in memory, which may cause the randomizer to sometimes select the old, unmodified outfit.

> **Rule of Thumb:** Order matters. Always load the base definition file before any self-referential override files in your stack.
---

## 🙏 Acknowledgments

A special thank you to **[Alectriciti](https://github.com/Alectriciti/comfyui-adaptiveprompts)** for bringing us so many nice building blocks!
