"""BladeEye Pro native desktop runtime package."""


def run_desktop_app(argv=None):
    from .app import run_desktop_app as _run

    return _run(argv)


__all__ = ["run_desktop_app"]
