"""Make Odoo's --dev=reload work when the addons folder is a Windows bind mount.

THE PROBLEM
    `--dev=reload` restarts Odoo when a .py file changes. Odoo learns about
    changes from the `watchdog` library, which on Linux uses inotify: the kernel
    pushes an event when a file is written.

    With Docker Desktop on Windows, a folder such as D:\\nxt_volt\\addons reaches
    the container through a "9p" network-style filesystem. inotify reports
    nothing at all on such a mount, so Odoo never notices an edit.

THE FIX (development image only; Odoo itself is not modified)
    Odoo does `from watchdog.observers import Observer` and then calls
    `Observer().schedule(handler, path, recursive=True)` once per addons path.
    Before Odoo is imported, we replace that `Observer` name with a small class
    that sends each path to one of two real observers:

      * paths listed in ODOO_POLL_WATCH_PATHS -> PollingObserver, which compares
        file timestamps once a second (works on any filesystem);
      * every other path -> the normal inotify observer (Odoo's own addons hold
        tens of thousands of files; polling those would waste CPU).

HOW IT IS LOADED
    The Dockerfile installs this file next to a one-line `.pth` file. Python
    executes `import ...` lines found in .pth files at interpreter start-up,
    which is early enough to run before Odoo imports watchdog.
    Both files go into the SAME directory as the watchdog package: Python adds
    site directories to the import path one at a time, and a .pth file can only
    import what is in directories added so far.
    Nothing happens unless ODOO_POLL_WATCH_PATHS is set (see docker-compose.yml).
"""
import os

_POLL_PATHS = tuple(
    p.rstrip("/") for p in os.environ.get("ODOO_POLL_WATCH_PATHS", "").split(",") if p.strip()
)


def _install():
    import watchdog.observers
    from watchdog.observers.polling import PollingObserver

    native_observer_class = watchdog.observers.Observer

    class HybridObserver:
        """Implements only the four methods Odoo's FSWatcherWatchdog calls."""

        def __init__(self):
            self._native = native_observer_class()
            self._polling = PollingObserver(timeout=1)

        def schedule(self, event_handler, path, recursive=False):
            normalized = path.rstrip("/")
            use_polling = any(
                normalized == p or normalized.startswith(p + "/") for p in _POLL_PATHS
            )
            observer = self._polling if use_polling else self._native
            return observer.schedule(event_handler, path, recursive=recursive)

        def start(self):
            self._native.start()
            self._polling.start()

        def stop(self):
            self._native.stop()
            self._polling.stop()

        def join(self, timeout=None):
            self._native.join(timeout)
            self._polling.join(timeout)

    watchdog.observers.Observer = HybridObserver


if _POLL_PATHS:
    try:
        _install()
    except ImportError as exc:
        # Never fail silently: without this shim auto-reload quietly does nothing.
        import sys

        print(f"nxtvolt_dev_watch: polling watcher NOT installed ({exc})", file=sys.stderr)
