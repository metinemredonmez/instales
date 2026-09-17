"""OpenAI-style function schemas from plain Python callables — the local engine's tool protocol.

`function_schema(fn)` reads what `ai/tools.py` already writes for the Claude engine: the name, the docstring (the
text before `Args:` becomes the description, each `name: text` line under it the parameter's description) and the
type hints (str/int/float/bool → JSON types, `Literal[...]` → enum, `list[X]` → array, `X | None` → X, a parameter
with a default is not required). Nothing is invented: a hint the mapping does not know becomes an unconstrained
schema, and a tool without a docstring gets an empty description.
"""

from __future__ import annotations

import inspect
import re
import types
import typing
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

_ARG_LINE = re.compile(r"^\s*(\w+)\s*(?:\([^)]*\))?:\s*(.*)$")
_SECTION = re.compile(r"^\s*(Args|Arguments|Parameters|Returns|Raises|Examples?|Note|Notes)\s*:\s*$")
_SCALARS: dict[Any, dict] = {str: {"type": "string"}, int: {"type": "integer"}, float: {"type": "number"}, bool: {"type": "boolean"},
                             Decimal: {"type": "number"}, dict: {"type": "object"}, type(None): {"type": "null"}}


def parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """(description, {arg: description}) from a Google-style docstring: the description is every line before the
    first section header, joined with spaces; under `Args:` a line at the arguments' indentation that reads
    `name: text` starts an argument, the deeper-indented lines after it continue its text."""
    if not doc:
        return "", {}
    description: list[str] = []
    args: dict[str, str] = {}
    section, current, arg_indent = None, None, None
    for line in inspect.cleandoc(doc).splitlines():
        header = _SECTION.match(line)
        if header:
            section, current, arg_indent = header.group(1), None, None
            continue
        if section is None:
            description.append(line.strip())
            continue
        if section not in ("Args", "Arguments", "Parameters") or not line.strip():
            continue
        if arg_indent is None:
            arg_indent = _indent(line)
        arg = _ARG_LINE.match(line) if _indent(line) <= arg_indent else None
        if arg:
            current = arg.group(1)
            args[current] = arg.group(2).strip()
        elif current is not None:
            args[current] = f"{args[current]} {line.strip()}".strip()
    return " ".join(part for part in description if part).strip(), args


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def json_schema(tp: Any) -> dict:
    """The JSON schema of one type hint (see the module docstring for the mapping)."""
    if tp in _SCALARS:
        return dict(_SCALARS[tp])
    origin = get_origin(tp)
    if origin is Literal:
        values = list(get_args(tp))
        base = _SCALARS.get(type(values[0]), {}) if values else {}
        return {**{k: v for k, v in base.items() if k == "type"}, "enum": values}
    if origin in (Union, types.UnionType):
        members = [a for a in get_args(tp) if a is not type(None)]
        if len(members) == 1:
            return json_schema(members[0])
        return {"anyOf": [json_schema(m) for m in members]}
    if origin in (list, tuple, set, frozenset, Sequence, typing.Sequence) or tp in (list, tuple, set):
        args = get_args(tp)
        return {"type": "array", "items": json_schema(args[0]) if args else {}}
    if origin is dict or tp is dict:
        return {"type": "object"}
    if origin is typing.Annotated:
        return json_schema(get_args(tp)[0])
    return {}


def function_schema(fn: Callable[..., Any]) -> dict:
    """One OpenAI `tools` entry for `fn`: {"type": "function", "function": {name, description, parameters}}."""
    description, arg_docs = parse_docstring(fn.__doc__)
    try:
        hints = get_type_hints(fn)
    except Exception:  # noqa: BLE001 — an unresolvable forward reference leaves that parameter unconstrained
        hints = {}
    properties: dict[str, dict] = {}
    required: list[str] = []
    for name, param in inspect.signature(fn).parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        hint = hints.get(name, param.annotation)
        schema = json_schema(hint) if hint is not inspect.Parameter.empty else {}
        if name in arg_docs:
            schema["description"] = arg_docs[name]
        if param.default is inspect.Parameter.empty:
            required.append(name)
        properties[name] = schema
    return {"type": "function", "function": {"name": fn.__name__, "description": description,
                                             "parameters": {"type": "object", "properties": properties, "required": required}}}
