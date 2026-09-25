# yt-dlp canary and rollback runbook

`yt-dlp` is the coordinator's most fragile dependency because YouTube extraction,
the EJS challenge solver, and PO-token behavior move independently. The
coordinator image owns all downloads on devbox's residential IP; RunPod/vast GPU
images must not install `yt-dlp`, `yt-dlp-ejs`, Deno, Node, or cookie material.

## Pinned stack

- `yt-dlp==2026.3.17`
- `yt-dlp-ejs==0.8.0`
- `bgutil-ytdlp-pot-provider>=1.3.1`
- coordinator Dockerfile: Node/npm from apt, pinned Deno, `yt-dlp-ejs` from the
  Python dependency layer
- runtime yt-dlp args: `--remote-components ejs:github`, YouTube player-client
  chain, optional bgutil PO-token sidecar, optional per-job client-supplied
  cookies (see `docs/per-job-cookies.md`)

Dependabot checks the Python dependency graph weekly and groups
`yt-dlp`, `yt-dlp-ejs`, and `bgutil-ytdlp-pot-provider` into one PR. Treat those
PRs as extraction-risk changes: merge only after CI and a deployed canary pass.

## Nightly canary

Workflow: `.github/workflows/yt-dlp-canary.yml`

Schedule: daily at `08:23 UTC`, plus manual `workflow_dispatch`.

Required repository secrets:

- `KARAOKE_CANARY_BASE_URL`
- `KARAOKE_CANARY_SERVICE_TOKEN`

Optional repository vars:

- `KARAOKE_CANARY_URLS`
- `KARAOKE_CANARY_TIMEOUT_SECONDS`
- `KARAOKE_CANARY_POLL_SECONDS`

The workflow runs `uv run --frozen --no-dev scripts/ytdlp_canary.py`, submits a
real `/jobs` request to the deployed coordinator, and polls until the job reaches
`separating`, `transcribing`, or `completed`. That is the point where the
coordinator-side download has succeeded. If the check passes before terminal
completion, the script calls `/jobs/{id}/cancel` to avoid waiting on the whole
GPU pipeline.

Once a check passes, the script deletes its job (`DELETE /jobs/{id}`, also
removing the artifacts) so canary rows don't pile up in the Booth. Failed
canary jobs are kept, so their error stays visible.

On failure, the workflow opens or comments on an issue titled
`yt-dlp canary failure`, then fails the workflow run.

## GPU keepalive canary

The download canary above cancels its job before GPU dispatch, so it says
nothing about the RunPod endpoint. RunPod scales an endpoint that gets no
requests for about 7 days down to `workersMax=0`. After that, every job fails
with `409 ENDPOINT_PAUSED`. This happened silently from 2026-09-08 to 2026-09-24.

`.github/workflows/gpu-keepalive.yml` therefore runs the same script with
`KARAOKE_CANARY_MODE=full` every 3 days. It sends one short video (repo var
`KARAOKE_KEEPALIVE_URL`, default "Me at the zoo") and waits for `completed`.
That is one real, successful GPU request (about $0.01), which keeps the
endpoint active and checks the whole pipeline. On failure it opens or comments
on `GPU keepalive canary failure`.

If the failure is `ENDPOINT_PAUSED`, restore the endpoint with a RunPod REST
`PATCH /v1/endpoints/{id}` setting `workersMax` > 0 (the live endpoint id is
`KARAOKE_RUNPOD_ENDPOINT_ID` in Infisical), then re-run the workflow.

## Rollback

1. Open the failing canary run and identify the first bad deployed SHA/image.
2. Find the last green deployed canary SHA/image.
3. In `pyproject.toml`, pin `yt-dlp` and, if needed, `yt-dlp-ejs` to the
   last-known-good versions from that green deployment. Keep exact `==` pins.
4. Run `uv lock`.
5. Run the mandatory verification gate:

   ```bash
   git fetch origin && git rebase origin/main
   uv sync --frozen --all-extras --all-groups
   uv run ruff check src tests
   uv run pytest -q
   ```

6. Open a PR with the failing canary link and `Refs #17`.
7. After merge, the operator rebuilds/redeploys the Dockhand coordinator image.
   Agents do not deploy.
8. Manually run the `yt-dlp canary` workflow. Keep the tracking issue open until
   the deployed canary is green.
