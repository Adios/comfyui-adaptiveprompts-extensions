import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const ID_PREFIX = "AdaptivePromptsExtensions.";
const SETTING_DEBUG_LOG = ID_PREFIX + "debug_log";

async function syncToBackend(key, value) {
    try {
        await api.fetchApi("/adaptive_prompts_extensions/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [key]: Boolean(value) }),
        });
    } catch (e) {
        console.error("[Adaptive Prompts Extensions] Failed to sync config to backend:", e);
    }
}

app.registerExtension({
    name: "AdaptivePromptsExtensions.Settings",

    settings: [
        {
            id: SETTING_DEBUG_LOG,
            name: "Log Resolved Stack",
            type: "boolean",
            defaultValue: false,
            tooltip:
                "Prints the final resolved stack file list to the console whenever Prompt Stack Loader runs (applies on the next execution when a stack file, inline stack, or seed changes).",
            category: ["Adaptive Prompts Extensions", "Debugging", "Log Resolved Stack"],
            onChange: (value) => syncToBackend("debug_log", value),
        },
    ],

    async setup() {
        syncToBackend("debug_log", app.ui.settings.getSettingValue(SETTING_DEBUG_LOG, false));
    },
});
