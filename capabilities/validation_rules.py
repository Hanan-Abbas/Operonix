from capabilities.file_ops import safe_path_check
from capabilities.text_ops import validate_text
from capabilities.web_ops import validate_url
from capabilities.ui_ops import validate_coordinates
from capabilities.command_ops import validate_command


# Rules receive (action_data, merged_args) where merged_args combines step args + action_data["args"].


async def rule_safe_path(action_data, merged):
    return await safe_path_check({}, merged)


async def rule_validate_text(action_data, merged):
    return await validate_text(merged)


async def rule_validate_url(action_data, merged):
    return await validate_url(merged)


async def rule_validate_coordinates(action_data, merged):
    return await validate_coordinates(merged)


async def rule_validate_command(action_data, merged):
    return await validate_command(merged)


async def rule_screenshot(action_data, merged):
    if merged.get("path"):
        return True, None
    if merged.get("url"):
        return await validate_url(merged)
    return False, "screenshot requires path or url"


_PATH_INTENTS = [
    "write_file",
    "append_file",
    "read_file",
    "delete_file",
    "move_file",
    "list_dir",
    "create_dir",
    "delete_dir",
]

INTENT_VALIDATION = {}

for _intent in _PATH_INTENTS:
    INTENT_VALIDATION[_intent] = [rule_safe_path]

INTENT_VALIDATION.update(
    {
        "run_command": [rule_validate_command],
        "execute_script": [rule_validate_command],
        "git_op": [rule_validate_command],
        "install_package": [rule_validate_command],
        "check_status": [rule_validate_command],
        "open_url": [rule_validate_url],
        "click_link": [rule_validate_url],
        "fill_form": [rule_validate_url],
        "submit_form": [rule_validate_url],
        "extract_text": [rule_validate_url],
        "screenshot": [rule_screenshot],
        "click": [rule_validate_coordinates],
        "double_click": [rule_validate_coordinates],
        "move_cursor": [rule_validate_coordinates],
        "generate_text": [rule_validate_text],
        "summarize_text": [rule_validate_text],
        "translate_text": [rule_validate_text],
        "correct_grammar": [rule_validate_text],
        "code_generate": [rule_validate_text],
        "code_format": [rule_validate_text],
        "code_analyze": [rule_validate_text],
        "type_text": [rule_validate_text],
    }
)

# Backwards-compatible export
all_validation_rules = []
