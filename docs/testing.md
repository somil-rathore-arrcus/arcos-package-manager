# Testing

## In Docker (the standard)

The tests run on the same base image the application runs on. From the
checkout directory:

```bash
# Backend: pytest in the image's test stage, as your own uid so it can read out/
docker build --target test --build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g) -t apm-test .
docker run --rm apm-test

# Backend, also checking the real generated mapping in out/ (read-only)
docker run --rm -v "$PWD/out:/app/out:ro" apm-test

# Frontend: the build stage already typechecks; then the unit tests
docker build --target frontend-build -t apm-frontend-build . \
  && docker run --rm apm-frontend-build npx vitest run

# Remove the test images afterwards
docker image rm apm-test apm-frontend-build
```

Building these images does not touch the running container.

## In the development venv

```bash
scripts/setup-venv.sh --dev
scripts/test.sh
```

`scripts/test.sh` runs the backend tests, the frontend typecheck and tests,
and - when the files exist - the three output checks below.

## What the tests cover

- **Offline** on committed fixtures: Debian metadata parsing, candidate
  discovery, ref selection, kernel series, criticality rules, mapping
  storage, report writing, the publisher and its ledger, the API routes.
- **Integration tests on real git repositories** built by the tests: ancestry
  and merge-base selection, the comparison sets, backport tiers, patch preview,
  re-validation and cherry-pick, the publish commit. A mock would happily
  confirm whatever the code already believes; a real repository does not.
- **The deployment contract** (`tests/test_docker_contract.py`): Node only in
  the build stage, non-root user, no secret copied into the image, pinned
  dependencies, the healthcheck, read-only mounts, the entrypoint never
  printing a secret.
- **GitHub is always mocked.** No test pushes to a real repository or opens a
  real pull request.

Frontend tests mock `fetch` rather than the API client, so URL construction,
query strings and error decoding are exercised too.

Last full run (2026-10-06, in Docker): backend 438 passed; frontend typecheck
clean, 34 passed.

## Checking real output

Run inside the container against the data in `out/`:

```bash
docker compose exec app python scripts/verify_output.py              # mapping invariants
docker compose exec app python scripts/verify_upstream_md.py bookworm # files match mapping and plan
docker compose exec app python scripts/verify_pr_ledger.py bookworm   # ledger invariants (after --apply)
```

Each ends with `FAILURES: none` when all checks pass.

## Smoke test of a deployment

```bash
docker compose ps                                                     # (healthy)
curl -s http://<server-ip>:8080/health                                # {"status":"ok",...}
curl -s http://<server-ip>:8080/api/health                            # capabilities
docker compose exec app apm doctor --repo Arrcus/mstpd                # FAILURES: none
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd   # DRY_RUN_OK
```

The last command is a dry run: it reads, builds and checks the commit, and
pushes nothing.

## After changing a domain model

Regenerate the frontend types so a mismatch is a TypeScript error:

```bash
PYTHONPATH=src ./.venv/bin/python scripts/generate_frontend_types.py
```
