class PluginRegistry:
    def __init__(self):
        self._plugins = {}

    def get_plugin_for_app(self, app_name):
        """Returns a plugin if one exists for the specific app (e.g., 'vscode')"""
        return self._plugins.get(app_name.lower())

    def list_plugins(self):
        return list(self._plugins.keys())

# ✅ The instance that tool_selector is looking for:
plugin_registry = PluginRegistry()