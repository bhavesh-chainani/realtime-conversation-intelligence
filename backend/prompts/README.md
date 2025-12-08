# AI Prompts Directory

This directory contains all editable prompt files that control the behavior of the AI agents in the system. You can customize these files to adjust how the agents respond without modifying Python code.

## Files Overview

### Router Agent Prompts

**`router_system_prompt.txt`**
- Defines the system instructions for the router agent
- Controls when suggestions should be generated
- Modify this to change routing logic (e.g., when to suggest, decision criteria)

**`router_user_prompt.txt`**
- Template for the user prompt sent to the router agent
- Contains placeholder: `{conversation_transcript}` - automatically replaced with actual conversation text
- Modify this to change how the conversation is presented to the router agent

### Suggestion Agent Prompts

**`suggestion_system_prompt.txt`**
- Defines the system instructions for the suggestion agent
- Controls what types of suggestions are generated and how they're formatted
- Modify this to change suggestion behavior (e.g., what to focus on, output format)

**`suggestion_user_prompt.txt`**
- Template for the user prompt sent to the suggestion agent
- Contains placeholders:
  - `{conversation_transcript}` - automatically replaced with actual conversation text
  - `{max_suggestions}` - automatically replaced with the maximum number of suggestions to generate
- Modify this to change how the conversation is presented to the suggestion agent

### Fallback Suggestions

**`fallback_suggestions.json`**
- JSON array of suggestion objects used when the AI fails or errors occur
- Each suggestion object should have:
  - `type`: String category/action type
  - `text`: String description of the suggestion
  - `confidence`: Float between 0.0-1.0
  - `details`: Object containing:
    - `keyPoints`: Array of strings
    - `naturalResponse`: String the operator can use
    - `followUpQuestions`: Array of strings
    - `priority`: String ("high", "medium", or "low")

## How to Edit

1. Open the file you want to modify in any text editor
2. Make your changes while preserving placeholders (e.g., `{conversation_transcript}`)
3. Save the file
4. Restart the backend server for changes to take effect

## Template Placeholders

- `{conversation_transcript}` - The actual conversation text (used in router and suggestion user prompts)
- `{max_suggestions}` - Maximum number of suggestions to generate (used in suggestion user prompt)

**Important**: Do not remove or modify these placeholders unless you understand how they're used in the code. They are automatically replaced at runtime with actual values.

## Examples

### Example 1: Changing Router Decision Criteria

Edit `router_system_prompt.txt` and modify the "Decision criteria" section to change when suggestions are triggered.

### Example 2: Changing Suggestion Focus

Edit `suggestion_system_prompt.txt` and modify the role description or the numbered list to change what the suggestion agent focuses on.

### Example 3: Customizing Fallback Suggestions

Edit `fallback_suggestions.json` and modify the JSON structure to change what fallback suggestions are shown when errors occur.

## Notes

- Files are loaded once and cached for performance. Restart the server after making changes.
- All text files use UTF-8 encoding.
- JSON files must be valid JSON syntax or the system will fail to load them.

