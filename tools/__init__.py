"""Tool registry: a decorator turns a Python function into something the
local model can see and call (JSON tool-calling schema).

Adding a new tool means writing a function with a docstring and type
hints, decorating it with @tool, declaring in the module which platforms it
supports (`SUPPORTED_PLATFORMS = {"linux", "darwin"}`), and importing the
module from brain/router.py.

Note on language: identifiers and comments are English, but every string
that ends up spoken by Tero (or read by the model to decide what to say)
stays in Spanish -- that includes the error messages built below.
"""

import inspect
import sys
import typing
from typing import Callable

_TOOLS: dict[str, Callable[..., str]] = {}
_SCHEMAS: list[dict] = []

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _param_schema(annotation) -> dict:
    if typing.get_origin(annotation) is typing.Literal:
        return {"type": "string", "enum": list(typing.get_args(annotation))}
    return {"type": _JSON_TYPES.get(annotation, "string")}


def _supported_here(func: Callable[..., str]) -> bool:
    """Whether the module defining `func` says it works on this OS.

    brain/router.py imports every tool module unconditionally (importing is
    what registers them), so this is the place where a tool that cannot work
    here is left out of the catalog: the model never sees it, and therefore
    cannot call something that was going to fail and be read out loud as an
    error. Not declaring it is an error and not "works everywhere": a new
    tool has to say where it runs.
    """
    module = sys.modules[func.__module__]
    platforms = getattr(module, "SUPPORTED_PLATFORMS", None)
    if platforms is None:
        raise RuntimeError(
            f"El módulo {func.__module__} no declara SUPPORTED_PLATFORMS "
            f"(hace falta para registrar {func.__name__}, ver tools/__init__.py)"
        )
    return sys.platform in platforms


def tool(func: Callable[..., str]) -> Callable[..., str]:
    """Registers `func` as a tool available to the model, if the module it
    lives in supports this OS (see `_supported_here`)."""
    if not _supported_here(func):
        return func
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
