"""Pi-side tools that reuse the desktop app's collector and evaluation layer.

Deployed explicitly by ``pi/deploy/deploy_x500.sh`` to ``/opt/x500fs/lib`` and
run with the Pi's existing virtualenv Python (which has pymavlink). They back
the corrected ``x500-preflight`` and ``x500-status`` commands.
"""
