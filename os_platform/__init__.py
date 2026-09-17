import sys

from os_platform.base import Platform


def create_platform(**kwargs) -> Platform:
    if sys.platform.startswith("linux"):
        from os_platform.linux import LinuxPlatform

        return LinuxPlatform(**kwargs)
    raise NotImplementedError(f"No hay implementación de Platform para {sys.platform!r} todavía")
