"""DDX-ES web interface (Starlette, Jinja2 templates, server-rendered SVG). Run: `ddxes serve`."""


def create_app():
    from .app import create_app as _create
    return _create()
