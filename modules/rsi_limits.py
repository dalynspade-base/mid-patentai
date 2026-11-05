"""RSI sends complete serialized inputs without an application character ceiling."""

# None is deliberately recorded as JSON null in previews and frozen run configs.
# Provider context windows still apply; their failures must propagate without truncation.
MAX_INPUT_CHARACTERS = None


def check_input_size(text, context):
    if MAX_INPUT_CHARACTERS is not None and len(text) > MAX_INPUT_CHARACTERS:
        raise ValueError(f'{context} exceeds {MAX_INPUT_CHARACTERS:,} characters; no silent truncation.')
