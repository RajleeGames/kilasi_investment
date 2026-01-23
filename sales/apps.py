# sales/apps.py

from django.apps import AppConfig

class SalesConfig(AppConfig):
    name = 'sales'

    def ready(self):
        # Import the signals module so its decorators get registered
        import sales.signals  # noqa
