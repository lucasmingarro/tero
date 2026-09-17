import sys
import threading

from os_platform.base import Platform

_instance: Platform | None = None
_lock = threading.Lock()


def create_platform(**kwargs) -> Platform:
    if sys.platform.startswith("linux"):
        from os_platform.linux import LinuxPlatform

        return LinuxPlatform(**kwargs)
    raise NotImplementedError(f"No hay implementación de Platform para {sys.platform!r} todavía")


def get_platform(**kwargs) -> Platform:
    """The one Platform instance of the process.

    Created on the first call, passing `kwargs` on to `create_platform`.
    main.py is the one that calls it first, with the activation key from
    config.toml; everything else (the tools, health.py) calls it with no
    arguments and gets that same instance -- the key is only needed by
    `listen_key`, so nobody else has to know about it, and there is never a
    second instance reading the keyboard.
    """
    global _instance
    with _lock:
        if _instance is None:
            _instance = create_platform(**kwargs)
        return _instance
