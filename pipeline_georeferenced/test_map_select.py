"""
Drives the 2D map's "select an area -> Generate 3D" flow
(leaflet_pitch/index.html) in headless Chromium against the real app under
uvicorn, with the two heavy stages stubbed as in test_concurrency.py
(_prepare_cropped_input records the bounds the server received;
_run_pipeline sleeps briefly and writes a synthetic DSM).

Checks:
- dragging in select mode draws the box; map panning is off while
  selecting and back on afterwards; the size shows in km and degrees;
- Generate posts multipart west/south/east/north equal to the drawn box,
  the server receives the same bounds, polling shows "running" and reaches
  done, and the page navigates to the 3D viewer with ?job=<that job>;
- a box over the 0.10 degree cap or under the 0.02 degree minimum turns
  red, says why and disables Generate; one just above the minimum doesn't;
- a 400 shows the server's detail; a failed job shows the server's error;
  a 500 while polling is reported; Generate can't be submitted twice;
- the accuracy note: a box matching Nainital names the precomputed result,
  a box elsewhere says accuracy is not validated;
- the existing AOI rectangle's "Open 3D terrain" link still works.

Needs Playwright with its Chromium (pip install playwright; playwright
install chromium) and network access to unpkg.com for Leaflet. Map tiles
and the 3D viewer page are stubbed in the browser.

    python test_map_select.py
"""
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import rasterio

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "validation"))
sys.path.insert(0, str(ROOT / "dsm_calibration"))
import test_evidence as te  # noqa: E402  (synthetic grid writer)

PIPELINE_S = 2.5  # long enough that a 2 s poll sees "running"
PNG_1PX = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def main():
    import httpx
    import uvicorn
    from playwright.sync_api import sync_playwright
    import main as app_main

    fails = []

    def check(ok, msg):
        print(("ok   " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)

    meta = json.loads((ROOT / "qgis_prep" / "data" / "geo_metadata.json").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        grid = tmp / "grid"
        te.write_grid(grid, meta)
        out = tmp / "output_dsm.tif"
        app_main.BASE_DIR, app_main.JOBS_DIR_NAME = tmp, "jobs"
        (tmp / "jobs").mkdir()
        app_main.DSM_CAL_DIR, app_main.DSM_OUTPUT_PATH = tmp / "cal", out
        (tmp / "cal").mkdir()
        app_main.JOBS_STORE_PATH = tmp / "jobs_store.json"
        app_main.dem_court_mod.CACHE_ROOT = str(tmp / "court_cache")
        app_main.jobs.clear()

        received, fail_next = [], {"on": False}

        def fake_prepare(mode, geotiff, bounds, timings):
            received.append({"mode": mode, "bounds": bounds})
            return {"requested_bounds": list(bounds)}

        def fake_pipeline(dem_source, sources, timings):
            time.sleep(PIPELINE_S)
            if fail_next["on"]:
                raise RuntimeError("Copernicus window empty (stub)")
            with rasterio.open(grid / "output_dsm.tif") as src, rasterio.open(out, "w", **src.profile) as dst:
                dst.write(src.read(1), 1)
            w, s, e, n = received[-1]["bounds"]
            return {"metrics": {"dem_source": "copernicus", "vertical_datum": "EGM2008"}, "view_urls": {},
                    "bounds_epsg4326": {"west": w, "south": s, "east": e, "north": n}}

        app_main._prepare_cropped_input = fake_prepare
        app_main._run_pipeline = fake_pipeline

        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
        server = uvicorn.Server(uvicorn.Config(app_main.app, host="127.0.0.1", port=port,
                                               lifespan="off", log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                httpx.get(base + "/index.html"); break
            except httpx.ConnectError:
                time.sleep(0.05)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            def open_map():
                page = browser.new_page(viewport={"width": 1280, "height": 860})
                page.route("**/tile.openstreetmap.org/**", lambda r: r.fulfill(status=200, content_type="image/png", body=PNG_1PX))
                page.route("**/3d_visualization/index.html*", lambda r: r.fulfill(
                    status=200, content_type="text/html", body="<!doctype html><title>viewer stub</title>"))
                page.goto(base + "/leaflet_pitch/index.html")
                page.wait_for_function("typeof map !== 'undefined' && document.querySelector('#open-3d:not(.disabled)')")
                # the page's own fitBounds to the current AOI animates; draw only once the view has settled
                page.wait_for_function("!map._animatingZoom && !(map._panAnim && map._panAnim._inProgress)")
                page.wait_for_timeout(300)
                return page

            def draw(page, west, south, east, north):
                """Select mode, then a real mouse drag from the NW to the SE corner."""
                # keep the box clear of the info panel over the map's top-left corner
                page.evaluate(f"map.fitBounds([[{south}, {west}], [{north}, {east}]], "
                              f"{{paddingTopLeft: [420, 120], paddingBottomRight: [80, 80], animate: false}})")
                page.click("#select-toggle")
                rect = page.evaluate("(() => { const r = document.getElementById('map').getBoundingClientRect(); return [r.left, r.top]; })()")
                pt = lambda lat, lon: page.evaluate(f"(() => {{ const p = map.latLngToContainerPoint([{lat}, {lon}]); return [p.x, p.y]; }})()")
                (x0, y0), (x1, y1) = pt(north, west), pt(south, east)
                page.mouse.move(rect[0] + x0, rect[1] + y0)
                page.mouse.down()
                dragging_during = page.evaluate("map.dragging.enabled()")
                page.mouse.move(rect[0] + x1, rect[1] + y1, steps=12)
                live = page.inner_text("#box-info")
                page.mouse.up()
                return {"dragging_during": dragging_during, "live_info": live,
                        "dragging_after": page.evaluate("map.dragging.enabled()"), "box": page.evaluate("box")}

            # 1. normal flow: draw a 0.06 x 0.05 degree box near Nainital, generate, follow to the viewer
            page = open_map()
            check("disabled" not in (page.get_attribute("#open-3d", "class") or "") and
                  page.get_attribute("#open-3d", "href").endswith("3d_visualization/index.html"),
                  "existing AOI 'Open 3D terrain' link is enabled and points at the viewer")
            check(page.evaluate("map.dragging.enabled()"), "map pans normally before selecting")
            want = (79.44, 29.36, 79.50, 29.41)
            d = draw(page, *want)
            box = d["box"]
            check(not d["dragging_during"] and d["dragging_after"], "map dragging off while drawing, restored after")
            check("km" in d["live_info"] and "°" in d["live_info"], f"live size while dragging: {d['live_info']!r}")
            err = max(abs(box[k] - v) for k, v in zip(("west", "south", "east", "north"), want))
            check(err < 2e-4, f"drawn box matches the dragged corners (max {err:.1e} deg)")
            info = page.inner_text("#box-info")
            dlon, dlat = box["east"] - box["west"], box["north"] - box["south"]
            check("km" in info and f"{dlon:.4f}" in info and f"{dlat:.4f}" in info and not page.is_disabled("#generate"),
                  f"box size shown, Generate enabled: {info!r}")

            posts = []
            page.on("request", lambda r: posts.append(r) if r.method == "POST" and "/jobs" in r.url else None)
            page.click("#generate")
            page.click("#generate", force=True)  # second click while the first is running
            seen_running = False
            for _ in range(80):
                try:  # short timeout: the page may navigate to the viewer mid-read
                    txt = page.locator("#job-status").inner_text(timeout=500) if "leaflet_pitch" in page.url else ""
                except Exception:
                    txt = ""
                seen_running |= txt.startswith("Running")
                if "3d_visualization" in page.url:
                    break
                page.wait_for_timeout(100)
            check(len(posts) == 1, f"one POST despite a second click while running (got {len(posts)})")
            body = posts[0].post_data or ""
            fields = {k: float(body.split(f'name="{k}"')[1].split("\r\n\r\n")[1].split("\r\n")[0])
                      for k in ("west", "south", "east", "north")} if posts else {}
            check(posts and "wait=false" in posts[0].url and "multipart/form-data" in posts[0].headers.get("content-type", ""),
                  "POST /jobs?wait=false as multipart form data")
            check(fields and max(abs(fields[k] - box[k]) for k in fields) < 1e-6,
                  f"POST bounds = the drawn box: {fields}")
            check(received and received[-1]["mode"] == "fetch_by_bounds" and
                  max(abs(a - b) for a, b in zip(received[-1]["bounds"], (fields["west"], fields["south"], fields["east"], fields["north"]))) < 1e-9,
                  f"server received the same bounds, as fetch_by_bounds: {received[-1] if received else None}")
            check(seen_running, "polling showed the running state")
            # the response body is gone once the page navigates; the server's store has the job
            job_id = next((j["id"] for j in app_main.jobs.values()
                           if (j.get("input") or {}).get("requested_bounds") == [fields[k] for k in ("west", "south", "east", "north")]), None)
            check("3d_visualization/index.html" in page.url and f"job={job_id}" in page.url,
                  f"after done, navigated to the viewer for this job: {page.url.replace(base, '')}")
            check(app_main.jobs.get(job_id, {}).get("status") == "done", "the job is done on the server")
            page.close()

            # 2. oversize box: red, says why, Generate disabled
            page = open_map()
            big = draw(page, 79.30, 29.30, 79.45, 29.38)["box"]  # 0.15 x 0.08 deg
            check(big is not None and big["east"] - big["west"] > 0.10, f"oversize box drawn: {big}")
            check(page.is_disabled("#generate"), "box wider than 0.10 deg disables Generate")
            check("over" in (page.get_attribute("#box-info", "class") or "") and "Too large" in page.inner_text("#box-info"),
                  f"oversize box is red and says why: {page.inner_text('#box-info')!r}")
            color = page.evaluate("selRect.options.color")
            check(color == "#ff6b6b", f"oversize rectangle drawn red ({color})")

            # 2b. undersize box: below 0.02 deg per side, then one just above it
            small = draw(page, 79.460, 29.380, 79.475, 29.410)["box"]  # 0.015 x 0.03 deg
            check(small is not None and small["east"] - small["west"] < 0.02, f"undersize box drawn: {small}")
            info = page.inner_text("#box-info")
            check(page.is_disabled("#generate") and "Too small: at least 0.02 degrees per side" in info
                  and "over" in (page.get_attribute("#box-info", "class") or ""),
                  f"box narrower than 0.02 deg disables Generate and says why: {info!r}")
            check(page.evaluate("selRect.options.color") == "#ff6b6b", "undersize rectangle drawn red")
            okbox = draw(page, 79.460, 29.380, 79.485, 29.405)["box"]  # 0.025 x 0.025 deg
            info = page.inner_text("#box-info")
            check(okbox is not None and min(okbox["east"] - okbox["west"], okbox["north"] - okbox["south"]) >= 0.02
                  and not page.is_disabled("#generate") and "Too small" not in info,
                  f"box just above 0.02 deg enables Generate: {info!r}")

            # 3. accuracy note: Nainital's precomputed box vs a box elsewhere (Pune)
            draw(page, 79.42, 29.35, 79.51, 29.42)
            note = page.inner_text("#accuracy-note")
            check("Overlaps Nainital" in note and "14.8" in note, f"box over Nainital names the precomputed result: {note!r}")
            draw(page, 73.83, 18.49, 73.89, 18.55)
            note = page.inner_text("#accuracy-note")
            check(note == "Terrain from Copernicus DEM; accuracy not validated for this area.", f"box elsewhere: {note!r}")

            # 4. a 400 from the server shows its detail and lets the user try again
            page.route("**/jobs?wait=false", lambda r: r.fulfill(status=400, content_type="application/json",
                                                               body=json.dumps({"detail": "Invalid bounds (stub)"})))
            page.click("#generate")
            page.wait_for_function("document.getElementById('job-status').classList.contains('failed')")
            txt = page.inner_text("#job-status")
            check(txt == "Rejected (HTTP 400): Invalid bounds (stub)" and not page.is_disabled("#generate"),
                  f"400 shows the server's detail, Generate usable again: {txt!r}")
            page.unroute("**/jobs?wait=false")

            # 5. a failed job shows the server's error text
            fail_next["on"] = True
            page.click("#generate")
            page.wait_for_function("document.getElementById('job-status').classList.contains('failed')"
                                   " && document.getElementById('job-status').textContent.startsWith('Job failed')", timeout=20000)
            txt = page.inner_text("#job-status")
            check("Copernicus window empty (stub)" in txt, f"failed job shows the server's error: {txt!r}")
            fail_next["on"] = False

            # 6. a 500 while polling (e.g. a job whose metrics don't serialise) is reported, not hung on
            page.route("**/jobs/*", lambda r: r.fulfill(status=500, content_type="text/plain", body="Internal Server Error")
                       if r.request.method == "GET" else r.continue_())
            page.click("#generate")
            page.wait_for_function("document.getElementById('job-status').textContent.startsWith('Server error')", timeout=20000)
            txt = page.inner_text("#job-status")
            check("HTTP 500" in txt and not page.is_disabled("#generate"), f"500 while polling is reported: {txt!r}")
            page.close()
            browser.close()
        for _ in range(100):  # let the last background job finish before the temp dir goes
            if all(j.get("status") not in ("queued", "running") for j in app_main.jobs.values()):
                break
            time.sleep(0.1)
        server.should_exit = True

    print("\nFAIL:\n  " + "\n  ".join(fails) if fails else "\nPASS")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
