# Owner-local management protocol, version 1

`python -m strata_control.management` exposes the same saved-profile runtime operations used by `strata`. It is
one-shot process IPC over stdin/stdout, not an HTTP management API and not a remote administration channel.

## Transport

- send one UTF-8 JSON object on stdin
- close stdin
- read one JSON object from stdout
- diagnostics go to stderr
- the request limit is 64 KiB
- duplicate JSON keys, nonfinite numbers, unknown actions and unexpected fields are rejected

Example:

```json
{"schema_version":1,"action":"runtime.start.plan","profile":"production","params":{"shutdown_timeout_s":30}}
```

Success includes `{"schema_version":1,"ok":true,...}`. Failure includes `schema_version`, `ok:false`, a plain
`error` and `exit_code`. The process exits with that code.

## Actions

`params` must contain exactly the keys in the table.

| Action | Exact `params` keys | Effect |
|---|---|---|
| `runtime.start.plan` | `shutdown_timeout_s` | Validate the profile and source, return launch arguments, profile revision and source SHA-256. |
| `runtime.start` | `wait_s`, `shutdown_timeout_s`, `if_profile_revision`, `if_source_sha256` | Recheck both reviewed values, start through the owner runtime and journal the result. The two preconditions must not be null. |
| `runtime.status` | none | Return the owner's current state. |
| `runtime.verify.plan` | `load` | Read owner and instance identity and show whether verification would load an unloaded model. |
| `runtime.verify` | `wait_s`, `load`, `if_owner_id`, `if_instance_id` | Recheck both identities, then perform the owner and launch checks plus a one-token generation probe. |
| `runtime.stop.plan` | none | Read the current owner ID and, when available, instance ID. |
| `runtime.stop` | `wait_s`, `if_owner_id`, `if_instance_id` | Drain and stop only the reviewed owner and instance. `if_owner_id` must not be null. |
| `runtime.restart.plan` | `allow_source_switch`, `allow_source_migration` | Compare the saved profile, current source, owner and instance and return the execution preconditions. |
| `runtime.restart` | `wait_s`, `startup_wait_s`, `if_profile_revision`, `if_owner_id`, `if_instance_id`, `allow_source_switch`, `allow_source_migration`, `if_source_sha256` | Recheck reviewed profile, source, owner and instance, then drain, restart and journal the result. All four identity/digest preconditions must not be null. |

Times are seconds. Profile revisions are 16 lowercase hexadecimal characters. Source SHA-256 values are 64
lowercase hexadecimal characters. Owner and instance IDs are 32 lowercase hexadecimal characters when present.

Boolean `load`, `allow_source_switch` and `allow_source_migration` values must be actual JSON booleans.

## Review flow

1. Run the matching `.plan` action.
2. Show the returned source, profile, owner and instance identities to the operator.
3. Run the mutating action with the same identities.
4. Inspect journal and runtime status if the process is interrupted or times out.

A plan does not reserve the profile, source file or running instance. The owner rechecks execution preconditions
inside the owner process before draining or launching. A stale plan fails closed.

## CLI preference

Operators should normally use the CLI, which constructs these requests and prints the plan:

```sh
strata runtime start --profile production --plan
strata runtime start --profile production
strata runtime verify --profile production --plan --load
strata runtime verify --profile production --load
strata runtime restart --profile production --plan
strata runtime stop --profile production --plan
```

The protocol only manages a local profile owned by this account. It does not install models or engines, inspect a
remote server, expose an inference key or provide a general browser management surface.
