# Terminal quick start

Use `strata` from an installed local command, or `python3 strata.py` from this checkout.

## 1. Check the server profile

```sh
strata doctor --profile /opt/ai/src/strata-0.2.2/strata-022.json --live
strata check-production --config /opt/ai/src/strata-0.2.2/strata-022.json
strata serve --config /opt/ai/src/strata-0.2.2/strata-022.json --plan
```

The plan does not start the engine, load the model or change files.

## 2. Save a management profile

```sh
strata profile create production \
  --from-config /opt/ai/src/strata-0.2.2/strata-022.json
strata profile validate production
```

A saved profile is a revisioned reference to the source config plus typed serving overrides. The source config
remains the owner of model, engine and engine-environment details.

## 3. Start the owned server

```sh
strata runtime start --profile production --plan
strata runtime start --profile production
strata runtime status --profile production
strata runtime verify --profile production --load
```

`runtime verify --load` proves that the bound API answered a one-token generation through the loaded model. It does
not prove 256K stability or sustained throughput.

## 4. Read telemetry

```sh
strata status --url http://127.0.0.1:8080 --json
strata monitor --url http://127.0.0.1:8080 --once
strata logs --url http://127.0.0.1:8080 --output json
```

Save a URL separately when the server is behind a reverse proxy:

```sh
strata connection create edge \
  --url https://server.example \
  --prefix /strata \
  --credential-env STRATA_TELEMETRY_KEY
strata config show --connection edge --output json
strata status --connection edge --json
```

The connection file stores the environment variable name, never its value.

## 5. Stop or restart

```sh
strata runtime restart --profile production --plan
strata runtime restart --profile production
strata runtime stop --profile production --plan
strata runtime stop --profile production
```

The local owner checks the profile revision, source digest, owner ID and instance ID from an execution plan before
mutating a running service. Use a fresh plan when a precondition has changed.

## DFlash2

Set `"drafting": "dflash2"` in the source config after placing the compatible pack at the documented path. The
server adds the DFlash2 engine flags when the pack is present and falls back to MTP with a reason when it is not.
Keep `"auto"` while the target host lacks a measured `sm_75` DFlash2 sweep.
