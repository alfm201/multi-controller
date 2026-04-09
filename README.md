# multi-controller

`multi-controller` shares keyboard and mouse input across machines on the same LAN.
The current implementation is Windows-first and now uses a lease-based control plane so
only one controller is authorized to drive a target at a time.

## Roles

- `controller`: captures local input and sends it to one active target.
- `target`: receives forwarded input and injects it into the local OS.
- `coordinator`: owns the lease table and decides which controller currently holds each target.

Nodes can combine roles. The common default is `["controller", "target"]`.

## What Changed

- Lease TTL is now active: default TTL is `3000ms`.
- Active controllers send `ctrl.heartbeat` every `1s`.
- Targets receive `ctrl.lease_update` and only inject input from the authorized controller.
- Router state is explicit: `inactive`, `pending`, `active`.
- Hotkey switching no longer activates a new target until the coordinator grants it.
- `Ctrl+Shift+Tab` suppresses the matched modifier sequence so the target does not receive a stray `Tab` combo.

## Install

```bash
python -m pip install -e .[dev]
```

Runtime dependency:

- `pynput`

Development tools:

- `pytest`
- `pyinstaller`

## Config

Example `config.json`:

```json
{
  "default_roles": ["controller", "target"],
  "nodes": [
    {"name": "A", "ip": "192.168.0.10", "port": 5000, "roles": ["controller", "target"]},
    {"name": "B", "ip": "192.168.0.11", "port": 5000, "roles": ["controller", "target"]},
    {"name": "COORD", "ip": "192.168.0.12", "port": 5000, "roles": ["coordinator"]}
  ],
  "coordinator": {
    "candidates": ["COORD", "A"]
  }
}
```

Validation now rejects:

- duplicate node names
- coordinator candidates that are not present in `nodes`
- invalid role names
- missing or non-positive ports
- `--active-target` values that are missing, point to a non-target, or point to self

## Run

### Same PC, two instances

```bash
python main.py --node-name A --active-target B
python main.py --node-name B --active-target A
```

### Two PCs on the same LAN

1. Put the same `config.json` on both machines.
2. Set each node IP to the real LAN address of that machine.
3. Start one instance per machine.

Example:

```bash
python main.py --node-name A --active-target B
python main.py --node-name B
```

### Headless target

Use a target-only node when the remote machine should only receive input:

```json
{
  "nodes": [
    {"name": "A", "ip": "10.0.0.10", "port": 5000, "roles": ["controller", "target"]},
    {"name": "HEADLESS", "ip": "10.0.0.20", "port": 5000, "roles": ["target"]}
  ],
  "coordinator": {"candidates": ["A"]}
}
```

Run:

```bash
python main.py --node-name A --active-target HEADLESS
python main.py --node-name HEADLESS
```

If OS injection is unavailable, the target falls back to a logging injector instead of crashing.

## Hotkey

- `Ctrl+Shift+Tab`: cycle to the next peer with the `target` role.
- With a coordinator configured, the router enters `pending` first and only becomes `active` after `ctrl.grant`.
- Without a coordinator, switching remains local and immediate.

## Control Plane

Supported control frames:

- `ctrl.claim`
- `ctrl.release`
- `ctrl.heartbeat`
- `ctrl.grant`
- `ctrl.deny`
- `ctrl.lease_update`

The coordinator sends `ctrl.lease_update` to the target whenever a lease is granted, cleared, or expires.
When `controller_id` becomes `null`, the target releases any stuck key/button state and stops injecting input.

## Packaging

Build a Windows executable with PyInstaller:

```bash
pyinstaller --onefile main.py
```

Place `config.json` next to the generated executable in `dist/`.

## Logging

Important log lines:

- `[ROUTER STATE]`: router state transitions and reasons
- `[COORDINATOR]`: grant, deny, release, expiry decisions
- `[SINK LEASE]`: current target-side authorized controller
- `[INJECT ...]`: actual or logging-only OS injection calls

## Coordinate Policy

Mouse coordinates are currently forwarded as absolute screen coordinates.
For Windows setups with different monitor layouts or DPI scaling, keep controller and target display geometry aligned.
This is the current policy for v1 stabilization; coordinate normalization is still a follow-up improvement.

## Test Plan

Once dev dependencies are installed:

```bash
python -m pytest -q
```
