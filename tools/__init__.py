"""Tool registry: a decorator turns a Python function into something the
local model can see and call (JSON tool-calling schema).

Adding a new tool means writing a function with a docstring and type
hints, decorating it with @tool, and importing the module from
brain/router.py.

Note on language: identifiers and comments are English, but every string
that ends up spoken by Tero (or read by the model to decide what to say)
stays in Spanish -- that includes the error messages built below.
"""

import inspect
import typing
from typing import Callable

_TOOLS: dict[str, Callable[..., str]] = {}
_SCHEMAS: list[dict] = []

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _param_schema(annotation) -> dict:
    if typing.get_origin(annotation) is typing.Literal:
        return {"type": "string", "enum": list(typing.get_args(annotation))}
    return {"type": _JSON_TYPES.get(annotation, "string")}


def tool(func: Callable[..., str]) -> Callable[..., str]:
    """Registers `func` as a tool available to the model."""
    signature = inspect.signature(func)
    annotations = typing.get_type_hints(func)
    properties = {}
    required = []
    for name, parameter in signature.parameters.items():
        properties[name] = _param_schema(annotations.get(name, str))
        if parameter.default is inspect.Parameter.empty:
            required.append(name)

    _TOOLS[func.__name__] = func
    _SCHEMAS.append(
        {
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": (func.__doc__ or "").strip(),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
    )
    return func


def catalog() -> list[dict]:
    """JSON schemas for every registered tool, to hand over to Ollama."""
    return list(_SCHEMAS)


_ERROR_PREFIXES = ("La herramienta ", "Herramienta desconocida")


def is_error(result: str) -> bool:
    """True if `result` is one of the error messages built by `execute`."""
    return result.startswith(_ERROR_PREFIXES)


def execute(name: str, arguments: dict) -> str:
    """Runs the tool `name` with the arguments the model chose."""
    func = _TOOLS.get(name)
    if func is None:
        return f"Herramienta desconocida: {name!r}."
    try:
        return func(**arguments)
    except Exception as error:
        return f"La herramienta {name!r} falló: {error}"
