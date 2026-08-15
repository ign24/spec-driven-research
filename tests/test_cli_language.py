"""The command-line help surface must be English.

`sdr.product_language` inspects source literals. This inspects the help text
Click actually renders, so a Spanish string reaching a user through a command
summary or an option description fails even if it was assembled rather than
written as a literal.
"""

import re

import click

from sdr.cli import main

SPANISH = re.compile(r"[áéíóúñ¿¡]", re.IGNORECASE)


def _help_texts() -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []

    def walk(command: click.Command, path: str) -> None:
        if command.help:
            texts.append((f"{path} help", command.help))
        if command.short_help:
            texts.append((f"{path} short_help", str(command.short_help)))
        for param in command.params:
            if isinstance(param, click.Option) and param.help:
                texts.append((f"{path} {param.name}", param.help))
        if isinstance(command, click.Group):
            for name, sub in command.commands.items():
                walk(sub, f"{path} {name}")

    walk(main, "sdr")
    return texts


def test_every_rendered_help_string_is_english() -> None:
    spanish = sorted(f"{where}: {text}" for where, text in _help_texts() if SPANISH.search(text))

    assert spanish == [], f"the CLI help surface must be English: {spanish}"


def test_the_help_surface_is_not_empty() -> None:
    assert len(_help_texts()) > 30
