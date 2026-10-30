from __future__ import annotations

from importlib import import_module

from app.plugins.base import Plugin


class PluginRegistry:
    def __init__(self, module_names: tuple[str, ...]) -> None:
        self._plugins: list[Plugin] = []
        builtins = ("plugins.admin_manager",)
        configured = builtins + tuple(name for name in module_names if name not in builtins)
        for module_name in configured:
            module = import_module(module_name)
            factory = getattr(module, "create_plugin", None)
            if not callable(factory):
                raise RuntimeError(f"plugin {module_name!r} must export create_plugin()")
            plugin = factory()
            if any(existing.name == plugin.name for existing in self._plugins):
                raise RuntimeError(f"duplicate plugin name: {plugin.name}")
            self._plugins.append(plugin)

    @property
    def plugins(self) -> tuple[Plugin, ...]:
        return tuple(self._plugins)

    @property
    def supplier_plugins(self) -> tuple[Plugin, ...]:
        return tuple(
            plugin for plugin in self._plugins if getattr(plugin, "supplier_provider", False)
        )

    @property
    def supplier_providers(self) -> frozenset[str]:
        return frozenset(
            plugin.name for plugin in self._plugins if getattr(plugin, "supplier_provider", False)
        )


_registry: PluginRegistry | None = None


def configure_plugins(module_names: tuple[str, ...]) -> PluginRegistry:
    global _registry
    _registry = PluginRegistry(module_names)
    return _registry


def get_plugin_registry() -> PluginRegistry:
    if _registry is None:
        raise RuntimeError("plugins have not been configured")
    return _registry
