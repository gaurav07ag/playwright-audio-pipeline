"""
vocaroo_sync.py

Workflow (matches your flowchart):
  1. Read the SIP recording link from column P (it's stored as
     =HYPERLINK("https://sipX.richierichleads.com/recordings/....mp3", "Download MP3")
  2. Check column AE (Vocaroo link) — if it's already filled, skip that row.
  3. Download the call recording from your authorized network.
  4. Save it to a temp file.
  5. Upload it to Vocaroo with Playwright.
  6. Grab the new Vocaroo share link.
  7. Write that link into column AE.
  8. Delete the temp audio file.
  9. Move to the next row.

Starts at row 5709 on the "Appointment Tracker" tab, as requested.

-----------------------------------------------------------------------
SETUP (one-time)
-----------------------------------------------------------------------
1. pip install -r requirements.txt
2. playwright install chromium
3. Create a Google Cloud service account, enable the Sheets API, download
   the JSON key, and share your "Richie Rich Master Lead Tracker" sheet
   with the service account's email (Editor access).
4. Put the JSON key path into SERVICE_ACCOUNT_FILE below (or set the
   GOOGLE_APPLICATION_CREDENTIALS env var).
5. Run this on the machine/network that's allowed to reach
   *.richierichleads.com (per your flowchart's "authorized computer/network"
   step) — e.g. your office network or VPN.

-----------------------------------------------------------------------
RUNNING
-----------------------------------------------------------------------
    python vocaroo_sync.py

It's safe to re-run: any row whose AE cell already has a value is skipped,
so if it stops partway through (network hiccup, etc.) just run it again
and it'll pick up where it left off.

Use --headful to watch the browser while it uploads (handy for the first
run, to confirm the Vocaroo selectors below still match their site).
Use --dry-run to just print what it *would* do, without uploading or
writing to the sheet.
-----------------------------------------------------------------------
"""

import argparse
import os
import re
import sys
import tempfile
import time

import gspread
import requests
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

# ======================================================================
# CONFIG — edit these for your setup
# ======================================================================

SPREADSHEET_ID = "14bXvoi01kLdTIju54itsTaYbNA5epmb542gS0_3PbEQ"
WORKSHEET_NAME = "Appointment Tracker"

SERVICE_ACCOUNT_FILE = "service_account.json"  # path to your downloaded key

START_ROW = 5709          # first row to process, per your instructions
RECORDING_COL = "P"       # ISA Recording (HYPERLINK formula w/ sip link)
VOCAROO_COL = "AE"        # Rich Notes... wait, AE = where Vocaroo link goes

REQUEST_TIMEOUT = 60       # seconds, for downloading the mp3
SHEET_WRITE_DELAY = 1.2    # seconds between sheet writes (rate-limit safety)
ROW_DELAY = 0.5            # small pause between rows

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

# ======================================================================
# GOOGLE SHEETS HELPERS
# ======================================================================

def connect_sheet():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    return sh.worksheet(WORKSHEET_NAME)


def col_to_a1(col_letters, row):
    return f"{col_letters}{row}"


def get_last_row(ws):
    """Last non-empty row in the recording column, used as the scan boundary."""
    values = ws.col_values(gspread.utils.a1_to_rowcol(f"{RECORDING_COL}1")[1])
    return len(values)


def fetch_ranges(ws, start_row, end_row):
    """
    Batch-fetch column P (as FORMULA, so we can pull the raw URL out of the
    HYPERLINK() call) and column AE (as plain values) for the given rows.
    """
    p_range = f"{RECORDING_COL}{start_row}:{RECORDING_COL}{end_row}"
    ae_range = f"{VOCAROO_COL}{start_row}:{VOCAROO_COL}{end_row}"

    p_data = ws.get(p_range, value_render_option="FORMULA")
    ae_data = ws.get(ae_range, value_render_option="FORMATTED_VALUE")

    def flat(data, n):
        out = []
        for i in range(n):
            if i < len(data) and data[i]:
                out.append(data[i][0])
            else:
                out.append("")
        return out

    n = end_row - start_row + 1
    return flat(p_data, n), flat(ae_data, n)


URL_RE = re.compile(r'HYPERLINK\(\s*"([^"]+)"', re.IGNORECASE)
RAW_URL_RE = re.compile(r'https?://\S+\.mp3', re.IGNORECASE)


def extract_recording_url(cell_text):
    """Pull the sip*.richierichleads.com mp3 URL out of the P-column cell."""
    if not cell_text:
        return None
    m = URL_RE.search(cell_text)
    if m:
        return m.group(1)
    # fallback: maybe it's already a plain URL, not a formula
    m = RAW_URL_RE.search(cell_text)
    if m:
        return m.group(0)
    return None


# ======================================================================
# DOWNLOAD THE RECORDING
# ======================================================================

def download_recording(url, dest_path):
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, stream=True)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return dest_path


# ======================================================================
# UPLOAD TO VOCAROO (Playwright)
# ======================================================================

def upload_to_vocaroo(page, file_path):
    """
    Opens vocaroo.com, uses its "upload an existing file" option (instead
    of recording live), waits for it to process, and returns the share URL.

    NOTE: Vocaroo's page markup can change. The selectors below are a
    best-effort starting point — run once with --headful and, if the
    upload button or share-link box isn't found, open devtools, find the
    right selector, and update the constants at the top of this function.
    """
    UPLOAD_BUTTON_SELECTORS = [
        'button[aria-label="go to upload page"]',  # confirmed from live page, works regardless of UI language
        "#upload-button",
        "[aria-label='Upload']",
    ]
    FILE_INPUT_SELECTOR = "input.FileUploadButton__fileInput, input[type='file']"
    SHARE_LINK_SELECTORS = [
        "input[value^='https://voca.ro/']",
        "#share_page_url",
        "input#simple_link",
    ]

    page.goto("https://vocaroo.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)  # let any JS-driven UI finish rendering

    # Try to reveal the file input, either by clicking an "upload" control
    # or by finding a hidden <input type=file> directly.
    clicked = False
    for sel in UPLOAD_BUTTON_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                page.locator(sel).first.click(timeout=3000)
                clicked = True
                break
        except Exception:
            continue

    if clicked:
        page.wait_for_timeout(800)  # let the SPA route/panel change settle

    try:
        file_input = page.locator(FILE_INPUT_SELECTOR).first
        file_input.wait_for(state="attached", timeout=10000)
        file_input.set_input_files(file_path, timeout=10000)
    except Exception:
        # Couldn't find the file input — save a screenshot + the page HTML
        # so the selectors can be corrected from what the page actually is.
        debug_png = f"vocaroo_debug_{int(time.time())}.png"
        debug_html = f"vocaroo_debug_{int(time.time())}.html"
        try:
            page.screenshot(path=debug_png, full_page=True)
            with open(debug_html, "w", encoding="utf-8") as f:
                f.write(page.content())
            print(f"    -> saved debug screenshot: {debug_png}")
            print(f"    -> saved debug HTML: {debug_html}")
        except Exception:
            pass
        raise

    if not clicked:
        # Some Vocaroo layouts trigger upload just from the file input
        # existing on the page without needing a button click first.
        pass

    # Wait for the share panel to render, then scan every <input> on the
    # page for one whose value is a voca.ro share link. This is more
    # robust than guessing exact selectors against React's controlled
    # inputs, since the visible value is what we actually confirmed works.
    share_url = None
    try:
        page.wait_for_function(
            "document.querySelectorAll('input').length > 0 && "
            "[...document.querySelectorAll('input')].some(i => /voca\\.ro\\//.test(i.value))",
            timeout=30000,
        )
    except Exception:
        pass

    inputs = page.locator("input")
    try:
        count = inputs.count()
        for i in range(count):
            try:
                val = inputs.nth(i).input_value(timeout=1000)
            except Exception:
                continue
            if val and "voca.ro/" in val:
                share_url = val.strip()
                break
    except Exception:
        pass

    # Fallback: try the specific selectors in case the scan above missed it.
    if not share_url:
        for sel in SHARE_LINK_SELECTORS:
            try:
                page.wait_for_selector(sel, timeout=5000)
                share_url = page.locator(sel).first.input_value()
                if share_url:
                    break
            except Exception:
                continue

    if not share_url:
        raise RuntimeError(
            "Could not find the Vocaroo share link after upload. "
            "Re-run with --headful to inspect the page and update "
            "SHARE_LINK_SELECTORS in upload_to_vocaroo()."
        )

    return share_url.strip()


# ======================================================================
# MAIN
# ======================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headful", action="store_true", help="show the browser window")
    parser.add_argument("--dry-run", action="store_true", help="don't upload or write, just print")
    parser.add_argument("--start-row", type=int, default=START_ROW)
    parser.add_argument("--end-row", type=int, default=None, help="defaults to last row with data")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="keep running: after finishing the current rows, wait and re-scan for new rows forever",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=60,
        help="seconds to wait between scans in --watch mode (default: 60)",
    )
    args = parser.parse_args()

    ws = connect_sheet()
    start_row = args.start_row

    with sync_playwright() as pw:
        # --mute-audio mutes all audio output at the Chromium level, so
        # Vocaroo's preview player (which gets created dynamically after
        # upload, not present at page load) can never make sound — the
        # DOM-based muting below can't catch elements added after 'load'.
        browser = pw.chromium.launch(
            headless=not args.headful,
            args=["--mute-audio"],
        )
        page = browser.new_page()
        page.on("load", lambda: page.evaluate(
            "document.querySelectorAll('audio,video').forEach(el => el.muted = true)"
        ))

        try:
            while True:
                end_row = args.end_row or get_last_row(ws)

                if end_row < start_row:
                    print(f"No rows yet at/after {start_row} (last row is {end_row}).")
                else:
                    print(f"Scanning rows {start_row}-{end_row} on '{WORKSHEET_NAME}'...")
                    p_values, ae_values = fetch_ranges(ws, start_row, end_row)

                    for offset, row in enumerate(range(start_row, end_row + 1)):
                        p_cell = p_values[offset]
                        ae_cell = ae_values[offset]

                        if ae_cell.strip():
                            continue  # Vocaroo link column already filled — skip

                        url = extract_recording_url(p_cell)
                        if not url:
                            print(f"Row {row}: no recording URL found in column {RECORDING_COL}, skipping.")
                            continue

                        print(f"Row {row}: found recording -> {url}")

                        if args.dry_run:
                            print(f"Row {row}: [dry-run] would download, upload, and write to {VOCAROO_COL}{row}")
                            continue

                        tmp_path = None
                        try:
                            fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
                            os.close(fd)
                            download_recording(url, tmp_path)

                            vocaroo_link = upload_to_vocaroo(page, tmp_path)
                            print(f"Row {row}: uploaded -> {vocaroo_link}")

                            ws.update(range_name=col_to_a1(VOCAROO_COL, row), values=[[vocaroo_link]])
                            time.sleep(SHEET_WRITE_DELAY)

                        except Exception as e:
                            print(f"Row {row}: FAILED - {e}", file=sys.stderr)
                            # Leave AE blank so this row gets retried next time.
                        finally:
                            if tmp_path and os.path.exists(tmp_path):
                                os.remove(tmp_path)

                        time.sleep(ROW_DELAY)

                    print("Pass complete.")

                if not args.watch:
                    break

                # Next pass should re-scan from start_row again — new rows might
                # appear anywhere in the range, and already-filled AE cells are
                # skipped anyway, so this stays cheap.
                print(f"Waiting {args.poll_interval}s before checking for new rows... (Ctrl+C to stop)")
                time.sleep(args.poll_interval)

        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            browser.close()

    print("Done.")


if __name__ == "__main__":
    main()