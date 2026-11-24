"""Authenticated catalog checks driven by the browser acceptance command."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import quote

from verify_cdp import VerifyError, open_tab, wait_for

from karaoke.catalog import youtube_id


CATALOG_TAB = '.catalog-tabs a[href="#/catalog"]'
SONG_LINK = 'input[aria-label="Song link"]'
ADD_SONG = ".catalog-add-song"


def check(evidence, scope, step, **observed):
    for name, expected in step["assert"].items():
        actual = observed[name]
        evidence.check(scope, name, expected, actual, actual == expected)


async def run_pilot_checks(evidence, scenario, root):
    # Each assertion is backed by tests of the production admission, import,
    # download and state-settlement code, with all providers stubbed.
    cases = [
        (
            ["test_duplicate_manifest_and_repeat_import", "test_idempotent_metadata_import_without_jobs"],
            {"duplicate_sources": 0, "new_downloads": 0, "new_gpu_jobs": 0},
        ),
        (["test_priority_cooldown_and_reservations"], {"pilot_state": "waiting_for_foreground", "new_pilot_downloads": 0}),
        (["test_priority_cooldown_and_reservations"], {"pilot_state": "waiting_for_cooldown", "new_pilot_downloads": 0}),
        (["test_priority_cooldown_and_reservations"], {"pilot_state": "waiting_for_budget", "new_gpu_jobs": 0}),
        (
            ["test_reservation_uses_observed_charges_and_stays_within_caps"],
            {"reservation_micros": 118532, "uncertain_charge_influences_reservation": False},
        ),
        (
            ["test_yt_dlp_gate_stops_and_pauses_without_gpu", "test_pilot_ytdlp_never_retries"],
            {"pilot_state": "paused", "new_gpu_jobs": 0, "automatic_retries": 0},
        ),
        (
            ["test_auto_check_publishes_only_on_all_checks"],
            {"all_checks_pass": "ready", "any_check_fails": "review", "audit_actor": "auto-check"},
        ),
        (
            ["test_import_retires_excluded_assets_without_erasing_the_ledger"],
            {"excluded_asset_hidden": True, "excluded_asset_charge_retained": True},
        ),
    ]
    for index, (step, (tests, observations)) in enumerate(zip(scenario["action"], cases, strict=True), 1):
        scope = f"pilot.{index}"
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "pytest", "-q", "-rs", *[f"tests/test_catalog_pilot.py::{test}" for test in tests]],
            cwd=root, capture_output=True, text=True, timeout=60, check=False,
        )
        if result.returncode or "SKIPPED" in result.stdout:
            evidence.skip(scope, step["assert"], result.stdout[-800:])
        else:
            check(evidence, scope, step, **observations)


def ticket(owner):
    command = os.getenv("KARAOKE_VERIFY_CLERK_TICKET_COMMAND")
    if not command:
        raise VerifyError("Configure KARAOKE_VERIFY_CLERK_TICKET_COMMAND for two Clerk QA users")
    try:
        result = subprocess.run([command, str(owner)], capture_output=True, text=True, timeout=30, check=True)
        token = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise VerifyError(f"Could not acquire Clerk QA ticket #{owner}") from None
    if not token or any(c.isspace() for c in token):
        raise VerifyError(f"Clerk QA ticket #{owner} was missing or malformed")
    return token


@asynccontextmanager
async def clerk_tab(args, evidence, profile, script, owner=1):
    value = await asyncio.to_thread(ticket, owner)
    evidence.secrets.append(value)
    async with open_tab(args.cdp, profile, args.target, script) as tab:
        await tab.navigate(f"{args.target}/app/?sign-in=1")
        try:
            await wait_for(
                lambda: tab.js("!!window.Clerk?.loaded && !!window.Clerk?.client"),
                30, "Clerk did not initialize on the mobile Booth",
            )
            signed_in = await tab.js(f"""(async () => {{
                try {{
                    const clerk = window.Clerk;
                    const attempt = await clerk.client.signIn.create({{
                        strategy: "ticket", ticket: {json.dumps(value)}
                    }});
                    await clerk.setActive({{session: attempt.createdSessionId}});
                    return !!clerk.session;
                }} catch {{ return false; }}
            }})()""")
            if not signed_in:
                raise VerifyError(f"Clerk QA sign-in failed for account #{owner}")
            await wait_for(lambda: tab.js("!!document.querySelector('.catalog-tabs')"), 25,
                           "Signed-in Booth tabs not visible")
            me = await clerk_api(tab, "/me")
            if me["status"] != 200 or me["body"].get("state") != "clerk_user":
                raise VerifyError(f"Coordinator did not authenticate QA account #{owner} as Clerk")
            yield tab, me["body"]["subject"]
        finally:
            evidence.console(tab, f"{profile}.clerk-{owner}")


async def clerk_api(tab, path, method="GET", payload=None, json_body=True):
    return await tab.js(f"""(async () => {{
        const token = await window.Clerk.session.getToken();
        const response = await fetch({json.dumps(path)}, {{
            method: {json.dumps(method)},
            headers: {{Authorization: "Bearer " + token, Accept: "application/json",
                      "Content-Type": "application/json"}},
            body: {json.dumps(json.dumps(payload)) if payload is not None else "undefined"},
        }});
        return {{
            status: response.status,
            body: {str(json_body).lower()} && response.headers.get("Content-Type")?.includes("application/json")
                  ? await response.json() : null,
        }};
    }})()""")


async def click(tab, selector):
    point = await wait_for(
        lambda: tab.element_point(f"document.querySelector({json.dumps(selector)})"),
        15, f"Control not reachable ({selector.split('href=')[0][:55]})",
    )
    await tab.tap(point)


async def visible_in_viewport(tab, selector):
    return await tab.js(f"""(() => {{
        const el = document.querySelector({json.dumps(selector)});
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0
            && r.right <= innerWidth && r.bottom <= innerHeight
            && el.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2));
    }})()""")


async def type_into(tab, selector, text):
    field = f"document.querySelector({json.dumps(selector)})"
    point = await wait_for(lambda: tab.element_point(field), 15, "Search or submit field not visible")
    await tab.tap(point)
    await tab.js(f"(() => {{ const input = {field}; input.focus(); input.select(); }})()")
    await tab.call("Input.insertText", {"text": text})


async def ready_fixture(tab, language=None):
    catalog = await clerk_api(tab, "/catalog")
    if catalog["status"] != 200:
        raise VerifyError("The signed-in account cannot read the catalog")
    ready = [song for song in catalog["body"]["songs"] if song["stage_token"]]
    if not ready:
        raise VerifyError("No auto-checked catalog recording is published")
    return next((song for song in ready if song["language"] == language), ready[0])


def stage_link(token):
    return f'a[href="#/job/{quote(token, safe="")}"]'


async def submit_url(tab, source):
    await type_into(tab, 'input[aria-label="Song link"]', source)
    tab.response = None
    await click(tab, 'input[aria-label="Song link"] + button.primary, [data-my-songs] button.primary')
    return await wait_for(lambda: asyncio.sleep(0, tab.response), 25, "Booth submit has no HTTP 201 receipt")


async def cleanup(api, job, args, evidence, scope, linked):
    if not job:
        return
    if linked:
        result = await api.request("DELETE", f"/jobs/{job['id']}", expected=(204, 404))
        evidence.check(scope, "job_link_removed", 204, result.status_code, result.status_code in {204, 404})
    else:
        passed, result = await api.cleanup(job, args.cleanup_grace)
        evidence.check(scope, "private_job_removed", True, result, passed)


async def verify_ready(args, api, evidence, scenario, script, inspect_player):
    first_load, booth, search, start, submit, personal_stage = scenario["action"]
    for profile in scenario["state"]["profiles"]:
        job = None
        async with clerk_tab(args, evidence, profile, script) as (tab, subject):
            scope = f"{profile}.catalog"
            song = await ready_fixture(tab, "he" if profile == "iphone" else "en")
            card = f'.catalog-card[data-catalog-song="{song["id"]}"]'
            try:
                await tab.navigate(f"{args.target}/app/")
                song_link_visible = await wait_for(lambda: visible_in_viewport(tab, SONG_LINK), 25,
                                                   "Song link input missing on first load of /app/")
                check(evidence, scope, first_load, song_link_visible=song_link_visible,
                      my_songs_selected=await tab.js("""!!document.querySelector(
                          '.catalog-tabs a[href="#/my-songs"][aria-current="page"]'
                      )"""))
                await evidence.capture(tab, scope, "first-load")
                await click(tab, CATALOG_TAB)
                await wait_for(lambda: tab.js("!!document.querySelector('.catalog-shelf-scroll')"), 15, "No catalog shelves")
                layout = await tab.js(f"""(() => {{
                    const shelves = [...document.querySelectorAll('.catalog-shelf-scroll')];
                    const width = document.documentElement.clientWidth;
                    return {{
                        ready: !!document.querySelector({json.dumps(card + "[href]")}),
                        names: [...document.querySelectorAll('.catalog-shelf h2')].map(el => el.textContent),
                        noPageScroll: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= width + 1,
                        shelfScroll: shelves.some(el => el.scrollWidth > el.clientWidth + 1),
                        viewport: [width, window.innerHeight],
                    }};
                }})()""")
                add_song_visible = await visible_in_viewport(tab, ADD_SONG)
                await evidence.capture(tab, scope, "shelves")
                await click(tab, ADD_SONG)
                link_focused = await wait_for(
                    lambda: tab.js(f"document.activeElement?.matches({json.dumps(SONG_LINK)})"),
                    15, "Add song did not focus the Song link input",
                )
                await click(tab, CATALOG_TAB)
                await click(tab, ".catalog-shelf a[href^='#/catalog/']")
                opened = await wait_for(lambda: tab.js("!!document.querySelector('.catalog-grid')"), 15,
                                        "Full shelf did not open")
                await evidence.capture(tab, scope, "full-shelf")
                check(evidence, scope, booth,
                      add_song_button_visible=add_song_visible, add_song_focuses_link=link_focused,
                      ready_recording_visible=layout["ready"],
                      primary_shelves_visible={"Hebrew hits", "English hits"} <= set(layout["names"]),
                      shelves_scroll_independently=layout["noPageScroll"] and layout["shelfScroll"],
                      viewport=layout["viewport"], full_shelf_link_works=opened)
                catalog = (await clerk_api(tab, "/catalog"))["body"]["songs"]
                for language in scenario["state"]["search_languages"]:
                    match = song if song["language"] == language else next(
                        row for row in catalog if row["language"] == language
                    )
                    await check_search(tab, match, evidence, scope, search)
                await type_into(tab, ".catalog-search", search_term(song))
                lyrics = (await clerk_api(tab, f"/share/{song['stage_token']}/lyrics"))["body"]
                await click(tab, stage_link(song["stage_token"]))
                await inspect_player(tab, evidence, f"{scope}.sing", start["assert"], lyrics)
                await click(tab, "button.m-btn.ghost")
                await click(tab, '.catalog-tabs a[href="#/my-songs"]')
                identifier = youtube_id(song["source_url"])
                source = f"https://youtu.be/{identifier}#karaoke-verify-{uuid.uuid4().hex}"
                job = await submit_url(tab, source)
                if job["source_url"] != source or job["owner_subject"] != subject:
                    job = None
                    raise VerifyError("Reuse receipt did not match the test owner and source")
                frames = tab.stages.get(job["id"], [])
                active = job["status"] != "completed" or any(frame["status"] != "completed" for frame in frames)
                link = stage_link(job["job_token"])
                visible = await wait_for(lambda link=link: tab.js(f"!!document.querySelector({json.dumps(link)})"),
                                         15, "Reused song did not appear in My Songs")
                check(evidence, f"{scope}.reuse", submit,
                      sing_action_visible=visible, processing_progress_shown=active,
                      new_downloads=int(active), new_gpu_jobs=int(bool(job["gpu_instance_id"])),
                      my_songs_selected=await tab.js("!!document.querySelector('[data-my-songs]')"))
                await evidence.capture(tab, scope, "my-songs")
                await click(tab, link)
                await inspect_player(tab, evidence, f"{scope}.my-songs-sing", personal_stage["assert"], lyrics)
            finally:
                await cleanup(api, job, args, evidence, scope, linked=True)


def search_term(song):
    pattern = r"[א-ת]{3,}" if song["language"] == "he" else r"[A-Za-z]{3,}"
    word = re.search(pattern, f"{song['title']} {song['artist']}")
    if not word:
        raise VerifyError(f"Catalog search fixture lacks {song['language']} text")
    return word[0][:min(5, len(word[0]) - 1)]


async def check_search(tab, song, evidence, scope, step):
    language = song["language"]
    card = f'.catalog-grid [data-catalog-song="{song["id"]}"]'
    await type_into(tab, ".catalog-search", search_term(song))
    details = await wait_for(lambda: tab.js(f"document.querySelector({json.dumps(card)})?.textContent"),
                             15, f"Partial {language} search did not match its catalog recording")
    check(evidence, f"{scope}.search-{language}", step,
          matching_recording_visible=song["title"] in details,
          recording_version_visible=song["version"] in details,
          shelves_replaced_by_results=await tab.js("!document.querySelector('.catalog-shelf')"),
          action_matches_readiness=("Sing" if song["stage_token"] else "Prepare") in details)
    await evidence.capture(tab, scope, f"search-{language}")


async def verify_miss(args, api, evidence, scenario, script, inspect_player):
    search, submit, start, other = scenario["action"]
    first, second = scenario["state"]["profiles"]
    source = os.getenv("KARAOKE_VERIFY_CATALOG_MISS_URL", scenario["state"]["private_url"]).split("#", 1)[0]
    identifier = youtube_id(source)
    source += f"#karaoke-verify-{uuid.uuid4().hex}"
    job = None
    scope = f"{first}.catalog-miss"
    try:
        async with clerk_tab(args, evidence, first, script) as (tab, subject):
            catalog = (await clerk_api(tab, "/catalog"))["body"]["songs"]
            if not identifier or any(youtube_id(song["source_url"]) == identifier for song in catalog):
                raise VerifyError("The configured private miss source must be outside the catalog")
            before = (await clerk_api(tab, "/jobs"))["body"]
            await click(tab, CATALOG_TAB)
            await type_into(tab, ".catalog-search", "No catalog fixture " + uuid.uuid4().hex[:8])
            paste = await wait_for(lambda: tab.js("!!document.querySelector('.catalog-empty button')"), 15,
                                   "Search miss did not offer the URL fallback")
            after = (await clerk_api(tab, "/jobs"))["body"]
            created = set(row["id"] for row in after) - set(row["id"] for row in before)
            check(evidence, scope, search, no_matching_recording_visible=True,
                  paste_url_action_visible=paste, new_downloads=len(created),
                  new_gpu_jobs=sum(bool(row["gpu_instance_id"]) for row in after if row["id"] in created))
            await click(tab, ".catalog-empty button")
            job = await submit_url(tab, source)
            if job["source_url"] != source or job["owner_subject"] != subject:
                job = None
                raise VerifyError("Private submit receipt did not match the test owner and source")

            async def terminal():
                frames = tab.stages.get(job["id"], [])
                return next((frame for frame in reversed(frames)
                             if frame["status"] in {"completed", "failed", "cancelled"}), None)

            frame = await wait_for(terminal, args.job_timeout, "No terminal WebSocket event for the private job")
            final = await api.status(job)
            if frame["status"] != "completed":
                raise VerifyError(f"Private job #{job['id']} returned {frame['status']}")
            lyrics = await api.lyrics(job)
            link = stage_link(job["job_token"])
            visible = await wait_for(lambda link=link: tab.js(f"!!document.querySelector({json.dumps(link)})"),
                                     15, "Private job has no Sing action")
            check(evidence, scope, submit, job_submitted=job["status"],
                  websocket_completed=frame["progress"], synced_lyrics=lyrics["synced"], sing_action_visible=visible,
                  my_songs_selected=await tab.js("!!document.querySelector('[data-my-songs]')"))
            evidence.check(scope, "private_gpu_cost_micros", "reported", final["gpu_cost_micros"],
                           final["gpu_cost_micros"] is not None)
            await click(tab, link)
            await inspect_player(tab, evidence, f"{scope}.sing", start["assert"], lyrics)
        async with clerk_tab(args, evidence, second, script, owner=2) as (tab, other_subject):
            await click(tab, CATALOG_TAB)
            if other_subject == subject:
                raise VerifyError("The two Clerk QA tickets resolved to the same account")
            title = final["track"] or final["title"]
            await type_into(tab, ".catalog-search", title[:120])
            await wait_for(lambda: tab.js("!!document.querySelector('.catalog-empty, .catalog-grid')"), 15,
                           "Second owner's catalog search did not load")
            result = (await clerk_api(tab, "/catalog?q=" + quote(title)))["body"]
            jobs = (await clerk_api(tab, "/jobs"))["body"]
            check(evidence, f"{second}.catalog-miss", other, no_matching_recording_visible=not result["songs"],
                  other_owner_job_visible=any(row["id"] == job["id"] for row in jobs),
                  user_originated_job_in_catalog=any(youtube_id(row["source_url"]) == identifier for row in result["songs"]))
    finally:
        await cleanup(api, job, args, evidence, scope, linked=False)


async def verify_shared(args, api, evidence, scenario, script):
    submit, delete, guest_step, other = scenario["action"]
    scope = "catalog-shared"
    jobs = []
    try:
        async with AsyncExitStack() as stack:
            first, first_subject = await stack.enter_async_context(clerk_tab(args, evidence, "iphone", script))
            second, second_subject = await stack.enter_async_context(clerk_tab(args, evidence, "android", script, owner=2))
            if first_subject == second_subject:
                raise VerifyError("The two Clerk QA tickets resolved to the same account")
            song = await ready_fixture(first)
            identifier = youtube_id(song["source_url"])

            async def create(tab, url, subject):
                response = await clerk_api(tab, "/jobs", "POST", {"url": url})
                if response["status"] != 201 or response["body"]["owner_subject"] != subject:
                    raise VerifyError("Could not create both signed-in owner links")
                jobs.append(response["body"])
                return response["body"]

            one, two = await asyncio.gather(
                create(first, f"https://youtu.be/{identifier}", first_subject),
                create(second, f"https://music.youtube.com/watch?v={identifier}", second_subject),
            )
            check(evidence, scope, submit,
                  distinct_owner_job_ids=one["id"] != two["id"],
                  immediate_completed_links=all(j["status"] == "completed" and j["share_url"] is None for j in jobs),
                  new_gpu_jobs=sum(bool(j["gpu_instance_id"]) for j in jobs))
            removed = await clerk_api(first, f"/jobs/{one['id']}", "DELETE", json_body=False)
            if removed["status"] != 204:
                raise VerifyError("The first Clerk owner could not remove their own job link")
            jobs.remove(one)
            first_jobs, second_jobs = await asyncio.gather(clerk_api(first, "/jobs"), clerk_api(second, "/jobs"))
            catalog = (await clerk_api(second, "/catalog"))["body"]["songs"]
            check(evidence, scope, delete,
                  deleted_job_visible_to_first_owner=any(row["id"] == one["id"] for row in first_jobs["body"]),
                  second_owner_library_entry_visible=any(row["id"] == two["id"] for row in second_jobs["body"]),
                  ready_recording_visible=any(row["stage_token"] == song["stage_token"] for row in catalog))
            paths = (f"/share/{song['stage_token']}", f"/share/{two['job_token']}")
            denied = {}
            for suffix in ("", "/lyrics", "/karaoke.mp3", "/vocals.mp3"):
                statuses = await asyncio.gather(*(api.http.get(path + suffix) for path in paths))
                denied[suffix] = all(value.status_code in {401, 404} for value in statuses)
            async with open_tab(args.cdp, "iphone", args.target, script) as guest:
                await guest.navigate(f"{args.target}/app/#/job/{song['stage_token']}")
                wall = await wait_for(lambda: guest.js("[...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Sign in')"),
                                      25, "Guest Stage did not show Clerk sign-in")
                await evidence.capture(guest, scope, "guest-stage")
            check(evidence, scope, guest_step, sign_in_required=wall, public_share_available=not denied[""],
                  lyrics_delivered=not denied["/lyrics"], streams_delivered=not (denied["/karaoke.mp3"] and denied["/vocals.mp3"]))
            results = await asyncio.gather(*(clerk_api(second, paths[0] + suffix, json_body=False)
                                             for suffix in ("/lyrics", "/karaoke.mp3", "/vocals.mp3")))
            check(evidence, scope, other, catalog_access=bool(catalog),
                  lyrics_delivered=results[0]["status"] == 200,
                  streams_delivered=all(row["status"] == 200 for row in results[1:]))
    finally:
        for job in jobs:
            await cleanup(api, job, args, evidence, scope, linked=True)
