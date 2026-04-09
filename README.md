# multi-controller

`multi-controller` shares keyboard and mouse input across machines on the same LAN.
It is Windows-first and uses a lease-based control plane so only one controller can drive a target at a time.

## Current Model

- Every configured node is in the same group.
- Nodes connect to every other configured peer.
- Nodes are `controller + target` by default.
- `roles` is now optional and only needed for special cases like target-only devices.
- There is no static coordinator priority list anymore.
- The coordinator is elected automatically from the currently online group members.
- The elected coordinator is the online node with the smallest `node_id`.

This keeps config simpler and lets the system recover when the previous coordinator goes offline.

## Lease Flow

- A controller requests a target with `ctrl.claim`.
- The current elected coordinator grants or denies the lease.
- While active, the controller sends `ctrl.heartbeat` every `1s`.
- Lease TTL is `3000ms`.
- The target receives `ctrl.lease_update` and only injects input from the authorized controller.
- If the coordinator changes, the next heartbeat recreates the lease on the new coordinator.

## Router State

- `inactive`: no selected target
- `pending`: a target switch was requested but not granted yet
- `active`: the controller has a granted lease and forwards input

`Ctrl+Shift+Tab` cycles targets. The router now waits for `ctrl.grant` before activating the next target.

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

Minimal example:

```json
{
  "nodes": [
    {"name": "A", "ip": "192.168.0.10", "port": 5000},
    {"name": "B", "ip": "192.168.0.11", "port": 5000},
    {"name": "C", "ip": "192.168.0.12", "port": 5000}
  ]
}
```

Optional target-only device:

```json
{
  "nodes": [
    {"name": "A", "ip": "10.0.0.10", "port": 5000},
    {"name": "HEADLESS", "ip": "10.0.0.20", "port": 5000, "roles": ["target"]}
  ]
}
```

Validation rejects:

- duplicate node names
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

If OS injection is unavailable, the target falls back to a logging injector instead of crashing.

## Control Frames

- `ctrl.claim`
- `ctrl.release`
- `ctrl.heartbeat`
- `ctrl.grant`
- `ctrl.deny`
- `ctrl.lease_update`

## Packaging

```bash
pyinstaller --onefile main.py
```

Place `config.json` next to the generated executable in `dist/`.

## Testing

```bash
python -m pytest -q
```
