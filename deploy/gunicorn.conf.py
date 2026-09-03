"""Gunicorn configuration for ALPHABETTI.

Two workers, because the box has two cores and eight other applications on it.
Threads matter more than workers here: every request is either a SQLite read
(fast) or a wait on the folding Space (long and entirely idle), and a thread
waiting on a socket costs almost nothing.
"""

bind = "unix:/run/alphabetti/alphabetti.sock"
umask = 0o007

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
