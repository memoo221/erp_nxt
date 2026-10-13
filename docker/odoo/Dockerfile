# Development image for NXTVOLT procurement.
#
# We do NOT modify Odoo itself. We start from the official image, pinned to an
# exact major version (never `latest`: a new major version changes the ORM and
# the database schema, and a database cannot be downgraded), and add two
# developer conveniences on top.
FROM odoo:20.0

# The official image switches to the unprivileged "odoo" user at the end.
# Installing files system-wide needs root, so switch back briefly.
USER root

# 1) debugpy: the debug server VS Code attaches to.
# --break-system-packages: the base OS marks its Python as "externally managed"
# (PEP 668) so that pip does not fight with apt. Inside a throw-away dev
# container that protection is not useful, so we opt out explicitly.
RUN pip3 install --no-cache-dir --break-system-packages debugpy

# 2) Auto-reload fix for Windows bind mounts (explained in the .py file).
# Python runs the `import` line of a .pth file at every interpreter start-up.
# Installed beside the watchdog package (apt puts it in /usr/lib/python3/
# dist-packages) so that the shim can import watchdog when the .pth line runs.
COPY nxtvolt_dev_watch.py /usr/lib/python3/dist-packages/nxtvolt_dev_watch.py
RUN echo "import nxtvolt_dev_watch" > /usr/lib/python3/dist-packages/nxtvolt_dev_watch.pth

# Drop privileges again: Odoo must never run as root.
USER odoo
