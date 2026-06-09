# Devcontainer Supervisor Socket Analysis (2026-05-31)

## Scope and execution context

This analysis was performed by the Copilot coding agent running inside the project devcontainer.

Important: these findings depend on the in-container runtime, process tree, mounted paths, and Home Assistant supervisor startup scripts present in the devcontainer image.

A local-environment agent (outside the devcontainer) cannot reliably reproduce or validate these results unless it executes the same checks inside this devcontainer runtime.

## Why this note exists

Supervisor logs showed repeated lines like:
- Cannot connect to unix socket /run/os/core.sock
- Error on call http://localhost/api/core/state

The goal was to determine whether this indicates a true misconfiguration versus expected startup/debug behavior, and to verify how supervisor log-level changes behave in this image.

## Commands run and what they proved

### 1) Supervisor state and options
- ha supervisor info
- ha supervisor options --raw-json

Observed:
- Runtime showed debug: true and logging: debug.
- The raw-json options call returned an empty data object, which did not expose current values in this environment.

### 2) Running process and startup behavior
- ps aux | grep supervisor
- supervisor_run --help
- Inspect /usr/bin/supervisor_run

Observed:
- Supervisor is launched by a docker run command that sets SUPERVISOR_DEV=1.
- The devcontainer supervisor launcher hard-injects SUPERVISOR_DEV=1 at startup.

### 3) Socket and environment checks
- ls -l /run/os
- env | grep -i supervisor
- pgrep -a os-agent
- ls -l /run/os (after os-agent check)

Observed:
- /run/os path is absent in this container.
- os-agent process can be running while /run/os is still absent.
- This is enough to produce /run/os/core.sock connection errors during supervisor API checks.

### 4) Log-level command semantics
- ha supervisor options --help

Observed:
- --log-level is a global HA CLI verbosity flag.
- Supervisor logging config is controlled with --logging and --debug.

### 5) Runtime vs persisted behavior test
- ha supervisor options --logging info --debug=false
- ha supervisor info
- cat /mnt/supervisor/config.json
- ha supervisor restart
- ha supervisor info (after restart)
- cat /mnt/supervisor/config.json (after restart)

Observed:
- Correct options changed runtime immediately to logging info and debug false.
- Persisted config also changed to logging info and debug false.
- After restart, runtime reverted to logging debug and debug true.
- Persisted config remained info/false.

Conclusion:
- In this devcontainer image, supervisor startup behavior overrides runtime to debug on restart, due to startup-time dev mode injection.

## Main conclusions

1. The previous command using --log-level did not modify supervisor logging config.
2. Correct command is:
   - ha supervisor options --logging info --debug=false
3. In this devcontainer, those settings apply immediately to the currently running supervisor.
4. On restart, runtime returns to debug because startup injects dev mode.
5. The /run/os/core.sock messages are mainly startup/debug noise in this environment unless they continue well beyond startup while core is healthy.

## Recommended next steps

1. Use the correct command in operational docs/scripts:
   - ha supervisor options --logging info --debug=false

2. If restart-persistent non-debug behavior is required in devcontainer runs, update startup behavior so debug is not forced on launch.
   - Candidate location: /usr/bin/supervisor_run and related /etc/supervisor_scripts startup logic.
   - Specifically review whether SUPERVISOR_DEV should be conditional or disabled for normal troubleshooting runs.

3. Add a small validation step after supervisor restarts:
   - ha supervisor info
   - verify logging and debug fields match expected values.

4. Treat /run/os/core.sock messages as expected startup noise only when:
   - they occur during startup windows, and
   - Home Assistant core becomes healthy and reachable afterward.

5. Escalate as a real issue if core.sock errors continue continuously after startup stabilization with a healthy core.

## Agent-to-agent handoff note

If a local-host agent is reviewing this issue, do not assume these behaviors apply on the host OS directly.

Run the same checks from inside the devcontainer before drawing conclusions. The launcher, socket paths, and supervisor runtime wiring are container-specific here.
