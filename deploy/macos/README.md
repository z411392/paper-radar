# macOS launchd worker

This directory contains a template, not an installed service.

1. Initialize the intended workspace explicitly with `paper-radar init --workspace PATH --with-runtime`.
2. Copy `com.paper-radar.worker.plist.example` to a user-owned plist outside the repository.
3. Replace `__PYTHON__` with the absolute Python executable that has the Paper Radar wheel installed.
4. Replace `__WORKSPACE__` with the absolute runtime workspace path.
5. Validate the plist with `plutil -lint`, then load it with the normal per-user `launchctl` workflow.

The template intentionally does **not** include `--allow-live-source`. A default launchd worker may plan
and maintain durable jobs, but cannot contact arXiv. Enabling live source access is a separate commissioning
step and also requires an absolute shared `--rate-limit-state` path.

SIGTERM/SIGINT request a graceful loop stop. The current cycle is allowed to finish; the worker does not
cancel an in-flight external request and does not hold a SQLite writer transaction while waiting on I/O.

Email delivery is also not enabled by this plist. SMTP remains behind the delivery commissioning boundary.
