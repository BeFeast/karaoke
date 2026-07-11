# Changelog

All notable changes to this project are documented here. Each `vX.Y.Z`
section is generated per release from the merged pull-request titles in the
tag range. See the README "Releases" section for how a release is cut.

## [v0.31.0] - 2026-07-11

_Bump: minor_

- feat(lyrics-api): parse Enhanced LRC word tags into words[] on LyricsLine (#223)
- feat(worker): Whisper-floor Enhanced LRC word tags + mega-segment split (#219) (#224)
- fix(runpod): stop Whisper repetition-collapse + emit Enhanced LRC word tags (r7) (#225)
- feat(spa): word-accurate lyric wipe from Enhanced-LRC words[]/end (#221) (#227)

## [v0.3.1] - 2026-06-11

_Bump: patch_

- fix(player): lyrics autoscroll offsetParent + gentle dual-stem follower sync (#114)

## [v0.3.0] - 2026-06-10

_Bump: minor_

- feat(gpu): upgrade stem separation htdemucs → BS-Roformer via audio-separator (#103)
- chore(runpod): repoint template image to cuda12.4-r5 (BS-Roformer) (#106)

## [v0.2.0] - 2026-06-09

_Bump: minor_

- feat(release): port scribe-service release pipeline (#100)

## [v0.1.0] - 2026-06-09

_Baseline release — first tag. Aggregates every merged PR up to `e4f81be`._

- fix(worker): auto-retry transient RunPod GPU-capacity stalls (#92)
- Pin yt-dlp stack and add nightly canary (#82)
- feat(extension): per-job ephemeral YouTube cookies on submit (Refs #77) (#80)
- feat(api,worker): ephemeral per-job YouTube cookies on POST /jobs (Refs #77) (#79)
- feat(api,extension): auto-rotate YouTube cookies via Chrome extension (#73) (#74)
- fix(runpod): flush stale workers on template image change (#75)
- fix(config): make ytdlp_cookies_file opt-in (empty default) (#68) (#72)
- feat(worker): yt-dlp cookies + EJS n-sig solver for session-gated videos (#68) (#71)
- fix(runpod): r4 GPU image build deps + bump template to cuda12.4-r4 (#70)
- feat(worker): yt-dlp YouTube anti-bot — deno + bgutil PO-token provider + backoff (#69)
- feat(worker): force-align LRCLIB plain lyrics to LRC in the GPU job (#55) (#67)
- feat(spa): synced-lyrics highlight panel on the item route (#59) (#65)
- feat(api): structured lyrics endpoint + artifacts[] on JobOut (#56) (#66)
- feat(spa): real karaoke player on the item route (wavesurfer + dual-stem) (#64)
- feat(worker): LRCLIB synced-lyrics sourcing (Track 1) (#63)
- UX v2: React item route /app/#/job/:token (hash routing) (#61)
- feat(api+worker): persist source music metadata for lyrics lookup (Refs #53) (#62)
- feat(spa+share): in-app confirm modal + Scribe-styled result page (#52)
- feat(spa+api): job actions (delete/cancel/clear-failed) + per-job links + mobile overflow fix (#51)
- feat(spa): port Scribe's design system onto the Karaoke SPA (LAN-mode, no auth) (#49)
- fix(runpod): two-tier timeout — fail fast on queue, never kill a running job (#47)
- fix(runpod): tighten timeouts to match 1-3 min SLA, fail fast on capacity outage (#46)
- fix(runpod): raise wall_ceiling + R2 presign TTL to 30 min (URL must outlive queue wait) (#45)
- fix(runpod): widen GPU pool + workersMax=3 in provision script (#44)
- feat(spa): Submitter SPA (Vite+React+Clerk) served by FastAPI (#43)
- feat(api): GET /jobs (owner-scoped list) + GET /me (#42)
- feat(api): HTML share page + artifact file serving (#41)
- feat(runpod): handler PUTs stems to R2 (bypass /run output 10MB cap) (#40)
- feat(runpod): upload mix.wav to R2 + presigned URL (bypass /run 10MB cap) (#39)
- fix(runpod): submit jobs to api.runpod.ai/v2, not rest.runpod.io/v1 (#38)
- fix(runpod): exclude isServerless from template PATCH (immutable after create) (#37)
- fix(runpod): two-step template+endpoint provision (templateId now required) (#36)
- feat(worker): RunPod Serverless client + scheduler dispatch + tests (#34)
- feat(runpod): Serverless image + handler + provisioning script (#35)
- fix(worker): widen vast offer pool + cold-start budget for the 10GB image (#32)
- fix(docker): add openssh-client to coordinator image (#31)
- feat(worker): real vast.ai worker (Demucs + Whisper) with always-on teardown (#30)
- feat(gpu): karaoke-vast HTTP server entrypoint + python3.12 build fix (#29)
- ci: GitHub Actions gate (ruff + pytest + alembic upgrade head) (#28)
- fix(alembic): single CREATE TYPE path (#27)
- fix(alembic): create_type=False on JOB_STATUS to prevent double-CREATE (#26)
- fix(alembic): use psycopg (v3) for sync migrations (#25)
- feat(docker): runtime bootstrap (Infisical secrets + alembic) (#24)
- fix(docker): include README.md in build context (#23)
- chore: commit uv.lock for reproducible builds (#22)
- feat(docker): add api Dockerfile for karaoke:local stack image (#21)
- feat(api): skeleton with multi-layer auth + mocked worker (#20)
- feat(extension): port Chrome submitter from scribe (#3)
- feat(gpu): scaffold ghcr.io/befeast/karaoke-vast:cuda12.4 (#2)
