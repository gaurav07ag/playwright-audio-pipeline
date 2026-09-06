# Audio Relink Automation

Automation that converts an IP-restricted audio link into a universally accessible one, using Python + Playwright.

## Problem

A recording was served from a domain locked down by IP whitelisting. The audio played fine on whitelisted machines, but every other device hit a dead link — no easy way to share or review the recording outside that one restricted network.

## Solution

Instead of requesting network or firewall changes, this project automates a workaround:

1. **Playwright** logs in and automatically detects/grabs the restricted audio link — no manual copy-paste.
2. **Python** downloads the audio file in the background.
3. The file is re-uploaded to **Vocaroo**, and the script scrapes the new shareable link.
4. The resulting Vocaroo link works on **any device** — no IP restriction, no VPN, no whitelist needed.

## Flow

```
Restricted audio link (IP-whitelisted, works on one device)
            │
            ▼
     Playwright bot
  (logs in, grabs the link)
            │
            ▼
   Python downloader
 (fetches the audio file)
            │
            ▼
    Vocaroo upload
(re-hosts, scrapes new link)
            │
            ▼
      Universal link
 (plays on any device)
```

## Stack

- Python
- Playwright
- Web scraping
- Process automation

## Setup

```bash
git clone https://github.com/gaurav07ag/audio-relink-automation.git
cd audio-relink-automation
pip install -r requirements.txt
playwright install
```

## Usage

```bash
python main.py --url "<restricted-audio-link>"
```

The script logs in, fetches the audio, uploads it to Vocaroo, and prints the universal shareable link.

## Why this matters

A lot of "infrastructure problems" don't need infrastructure changes — a well-placed automation layer can solve access issues faster and cheaper than waiting on IT/network changes.

## License

MIT
