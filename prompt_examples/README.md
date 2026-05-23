# Adaptive Prompts - Real-World Example Stacks

This folder contains a demonstration of a real-world, production-ready prompt architecture utilizing the **Prompt Stack Loader**. 

It replicates a structured ComfyUI setup where different components of a prompt (subjects, aesthetics, lighting, backgrounds) are broken out into separate files. This makes maintaining and generating highly complex, dynamic prompts clean and modular.

---

## 🏗️ The Architecture Philosophy

The core design pattern demonstrated here is the **Subject Hook System** (or "Contract System"), prioritizing a strict separation of concerns.

### 1. Modularity & The Stack
Instead of building one massive, unwieldy prompt string, elements are separated logically:
*   **Subjects (`arch/illustrious/anime/`):** Contains the character definitions, including their LoRAs, canonical triggers, and specific anatomical or micro-traits.
*   **Aesthetics (`arch/illustrious/aesthetics/`):** Base quality boosters and background-specific modifiers.
*   **Environment & Polish:** Dedicated directories for Backgrounds, Expressions, Lighting, and Finishes.
*   **Stacks (`stacks/`):** The orchestrator files that use the Stack Loader syntax (e.g., `random:arch/illustrious/anime`) to load files in a specific, sequential order.

### 2. The Subject Hook System (Interfaces)
A major challenge with modular prompts is how to apply character-specific aesthetic traits or negative prompts *without* overwriting the global baseline styles. 

This architecture solves this by utilizing **Variables** as "hooks."

*   **In the Subject File:** A character file (e.g., `arch/illustrious/anime/frieren/frieren.txt`) defines not just visual tags, but also variables for their specific artistic needs using the `^variable` assignment syntax:
    ```text
    {
      anime screencap, (traditional media: 1.2)
    }^subject_aesthetic
    ```

*   **In the Global Template:** The global aesthetic file (e.g., `arch/illustrious/aesthetics/base/noobai/default.txt`) leaves "slots" open for these hooks:
    ```text
    {
      masterpiece, best quality, ultra-detailed,
      __^subject_aesthetic__
    }^aesthetic
    ```

Because the Stack Loader evaluates files sequentially (Subject first, then Aesthetics), the character safely injects their specific style into the broader generation context.

### 3. Canonical Trait Breakdown
Character definitions systematically assign traits to micro-variables (like `^identity`, `^physique`, `^face_no_eyes`). This allows later prompt processing or specific nodes to reference the exact structural makeup of a character independently, avoiding tag conflicts in complex poses.

### 4. Automatic LoRA Tag Extraction
LoRA models are invoked right at the top of the character's definition file. The Prompt Stack Loader automatically parses these LoRA tags across all loaded files and outputs a clean `lora_string` that can be directly routed to a LoRA Tag Loader (e.g., `LoRA Text Loader` from `Lora Manager`).

### 5. The Final Template (Execution)
Once the Stack Loader has parsed all the files and built the `context` dictionary, you pass that context into a **Prompt Generator** node. Because the character is broken down into micro-traits, you have ultimate control over the final scene composition. 

Here is an example of a final prompt template used in a multi-stage workflow:

```text
__^aesthetic__,
1girl, solo,
__^identity__, __^physique__, __^chest__, __^face__, __^expression__,
__^clothing__,
{0-1$$dynamic pose},
__^background__,
__^background_aesthetic__,
__^lighting__,
__^finish__
```

And for a secondary scene where you want the head out of frame, you can surgically use the micro-traits (using `__^face_no_eyes__` and `__^chest_size__` instead of the full variables) to avoid generating conflicting anatomies:

```text
__^aesthetic__,
1girl, solo, dutch angle, from side, (head out-of-frame, eyes out-of-frame),
__^identity__, __^physique__,
__^chest_size__,
__^face_no_eyes__,
__^clothing__,
__^background__,
__^background_aesthetic__,
__^lighting__,
__^finish__
```

### 6. Context Accumulation & Overrides
In a multi-scene workflow, you can chain multiple Stack Loaders together. For example, if you want to generate a second image with the exact same character, background, and lighting, but you want to re-roll their expression:

1.  Pass the `context` from your Root Stack Loader into a new Stack Loader.
2.  Ensure you have set the proper `base_dir` and **leave `stack_file` empty**. *(Note: If you duplicate the root Stack Loader and forget to remove the `stack_file` input, it will completely re-roll everything defined in that stack file).*
3.  In the `inline_stack`, write the path you want to re-roll. For example: `expressions/default.txt`.
4.  Set `override_context` to **Override**.

This recalculates the expression variable while keeping the rest of your scene's variables strictly identical. Note that since LoRA tags are always accumulated and ignore the **Override** toggle, you would use the `remove:file_path` command in the `inline_stack` if you need to pull out a LoRA during a re-roll.
