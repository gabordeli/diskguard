class CorePluginBase:
    def __init__(self, plugin_name):
        self.plugin_name = plugin_name

    def update(self):
        pass


class Gtk3PluginBase:
    def __init__(self, plugin_name):
        self.plugin_name = plugin_name

    def enable(self):
        pass

    def disable(self):
        pass
