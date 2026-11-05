"""Shared serialized-input budget for the isolated RSI experiment."""

MAX_INPUT_CHARACTERS = 100_000


def check_input_size(text, context):
    if len(text) > MAX_INPUT_CHARACTERS:
        raise ValueError(f'{context} exceeds {MAX_INPUT_CHARACTERS:,} characters; no silent truncation.')
