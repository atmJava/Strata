# Terminal reference

The retained `strata` command manages a prepared Linux CUDA `sm_75` server. It does not install models, fetch engine
packages, update a desktop app or configure another backend.

Run `strata --help` or `strata <command> --help` for the authoritative parser.

## Commands

| Command | Purpose |
|---|---|
| `strata serve` | run the OpenAI/Anthropic server in the foreground |
| `strata launch` | compatibility alias for `serve` |
| `strata profile` | create, inspect, edit and version saved serving profiles |
| `strata doctor` | read-only local engine, profile and GPU check |
| `strata check-production` | compare a run config and environment with the 2 x 2080 Ti profile |
| `strata runtime` | start, verify, restart, stop and clean up an owned background server |
| `strata status` | fetch one read-only telemetry snapshot |
| `strata monitor` | print one snapshot or open the optional interactive monitor |
| `strata logs` | read bounded redacted request activity |
| `strata connection` | save a read-only server URL, prefix and credential reference |
| `strata config` | resolve effective client settings without printing secrets |

Unknown or removed commands fail with an argparse usage error. In particular, this distribution has no `code`,
`tune`, `timeskip`, `components`, `models`, `bench`, `calibration`, `update`, `connect` or installer command.

## Common checks

```sh
strata --version
strata doctor --profile /path/to/strata-022.json --live
strata check-production --config /path/to/strata-022.json
strata serve --config /path/to/strata-022.json --plan
```

`doctor` defaults to the CUDA backend. A non-CUDA backend is reported as unsupported. The optional live probe uses
`nvidia-smi` and does not start the engine.

## Profiles

```sh
strata profile create NAME --from-config /absolute/path/to/run-config.json
strata profile list [--output json]
strata profile show NAME [--effective]
strata profile validate NAME
strata profile set NAME KEY=VALUE [KEY=VALUE ...] [--plan]
strata profile use-config NAME --from-config PATH [--plan]
strata profile edit NAME
strata profile history NAME [--output json]
strata profile rollback NAME --to REVISION
```

A profile is a revisioned reference to a source run config plus typed serving overrides. Editing or switching its
source does not alter a running instance until restart.

## Runtime

```sh
strata runtime start --profile NAME [--plan] [--wait SECONDS]
strata runtime status --profile NAME
strata runtime verify --profile NAME [--load] [--plan]
strata runtime restart --profile NAME [--plan]
strata runtime stop --profile NAME [--plan]
strata runtime cleanup [--profile NAME]
```

Execution plans expose revisions and identities. Supply the reviewed preconditions on restart and stop:

```text
--if-profile-revision
--if-source-sha256
--if-owner-id
--if-instance-id
```

A source switch or in-place source migration also requires the matching `--allow-source-*` flag and the source
digest from the plan.

Runtime state is under:

```text
${XDG_STATE_HOME:-~/.local/state}/strata/runtime
```

The owner transport is a private Unix socket on Linux. `runtime verify --load` performs an authenticated one-token
generation check; it is not a work-load or soak test.

## Read-only clients

Direct loopback URL:

```sh
strata status --url http://127.0.0.1:8080
strata status --url http://127.0.0.1:8080 --output json
strata monitor --url http://127.0.0.1:8080 --once
strata logs --url http://127.0.0.1:8080 --output json
```

Saved connection:

```sh
strata connection create edge \
  --url https://server.example \
  --prefix /strata \
  --credential-env STRATA_TELEMETRY_KEY
strata connection list --output json
strata connection show edge --output json
strata config show --connection edge --output json
strata status --connection edge --json
```

Connection records are owner-private. They store the environment variable name and never the credential value.
A remote URL must use HTTPS. A loopback URL may use HTTP.

`STRATA_CONNECTION` selects a saved connection. Explicit `--connection` or `--url` takes precedence over that
environment variable and `STRATA_URL`.

## Logs

```sh
strata logs --url http://127.0.0.1:8080
strata logs --url http://127.0.0.1:8080 --output json
strata logs --url http://127.0.0.1:8080 --follow --output jsonl
```

`logs` reads bounded telemetry activity. It omits prompt bodies and reports observed sequence gaps or an instance
restart. It is not a durable audit log.

## Interactive monitor

One-shot status and logs need only Python's standard library. The interactive Textual view is optional:

```sh
python3 -m pip install -r requirements-monitor.txt
strata monitor --url http://127.0.0.1:8080
```

The monitor is read-only and never starts or stops the server. `q` detaches. On a terminal without an interactive
display, use `monitor --once` or `status`.

## Environment

| Variable | Purpose |
|---|---|
| `STRATA_URL` | default telemetry server root |
| `STRATA_CONNECTION` | saved read-only connection name |
| `STRATA_API_KEY` | inference key or legacy broad telemetry credential |
| `STRATA_TELEMETRY_KEY` | preferred read-only telemetry credential |
| `STRATA_CONNECTION_DIR` | absolute saved-connection directory |
| `STRATA_PROFILE_DIR` | absolute saved-profile directory |
| `STRATA_RUNTIME_DIR` | absolute owner-runtime state directory |
| `STRATA_COMPONENTS_DIR` | local engine component root |

Do not put credentials in URLs, command arguments, profiles or repository files. Point a credential option at an
environment variable name.

## JSON output

`--output json` produces one machine-readable object. `--json` is an alias where the command accepts it.
`serve --json` is a plan-only shortcut; foreground serving uses human or JSON-lines output appropriate to its
process lifetime.

Static output conventions:

- success includes `{"schema_version": 1, "ok": true, ...}`
- failure includes a stable integer `error.code` and a plain `error.message`
- telemetry snapshots are bounded and redacted before rendering
- a missing measurement is `null` or explicitly unavailable; it is never replaced with an invented value
