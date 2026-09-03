"""Gunicorn configuration for ALPHABETTI.

Two workers, because the box has two cores and eight other applications on it.
Threads matter more than workers here: every request is either a SQLite read
(fast) or a wait on the folding Space (long and entirely idle), and a thread
waiting on a socket costs almost nothing.
"""

# TCP on the loopback, not a unix socket. That is the convention every other
# app on this droplet already follows (FlexAppeal 8004, PANTS 8005, and so on
# up to 8007), and it sidesteps the socket-permission problem that a unix
# socket creates: systemd's RuntimeDirectory is 0770 owned by the service user,
# and nginx runs as www-data, so nginx cannot traverse into it without being
# added to the app's group. 502 with working static files is the symptom.
import os
bind = os.environ.get("BIND_ADDR", "127.0.0.1:8008")

workers = 2
worker_class = "gthread"
# The fold pool is bounded separately (ALPHABETTI_MAX_CONCURRENT); these threads
# serve HTTP, including the status polls that arrive every second or so while a
# fold is running.
threads = 8

# MUST stay in sync with proxy_read_timeout in deploy/nginx-alphabetti.conf.
# A submit returns a job id immediately, so no request should ever be slow; this
# is a backstop, not a budget.
timeout = 120
graceful_timeout = 30
keepalive = 5

# Recycle workers periodically. The app is long-lived and mostly idle, and this
# is cheap insurance against a slow leak on a box with 2 GB free.
max_requests = 800
max_requests_jitter = 120

accesslog = "-"
errorlog = "-"
loglevel = "info"
