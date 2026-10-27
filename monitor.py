
import os
import re
import json
import html
import smtplib
import time

from pathlib import Path
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

# ============================================================
# ACATENANGO AVAILABILITY MONITOR
# Target departure: November 4, 2026
# Travelers: 2
# Notifications: Telegram
# ============================================================

TARGET_DATE = re.compile(
    r"\b(?:November\s+4(?:,\s*2026)?|Nov\.?\s+4(?:,\s*2026)?|2026-11-04)\b",
    re.IGNORECASE,
)

AVAILABILITY_WORDS = re.compile(
    r"\b(?:available|availability|spots?\s+left|spaces?\s+available|"
    r"places?\s+left|book\s+now|reserve\s+now|select\s+this\s+date|"
    r"check\s+availability|openings?)\b",
    re.IGNORECASE,
)

UNAVAILABLE_WORDS = re.compile(
    r"\b(?:sold\s*out|fully\s*booked|no\s*availability|"
    r"unavailable|no\s*spots?\s*left|no\s*spaces?\s*available|"
    r"closed\s*for\s*booking)\b",
    re.IGNORECASE,
)

OPERATORS = {
    "Explore Acatenango":
        "https://www.exploreacatenango.com/en/hikes/acatenango-volcano-overnight-hike",
    "QuetzalGo":
        "https://quetzalgo.gt/acatenango",
    "Guatemala Nature":
        "https://guatemalanature.com/tours/acatenango-overnight",
    "Lava Trails":
        "https://lava-trails.com/acatenango-overnight-tour-lava-trails",
    "Lava Trails Reservations":
        "https://lavatrails.rezdy.com/index",
    "Acatenango Local Adventure":
        "https://acatelocaladventure.com/",
    "Acatenango.org":
        "https://acatenango.org/overnight-tours/",
}

STATE_FILE = Path("state.json")
REQUEST_TIMEOUT = 25


class TextExtractor(HTMLParser):
    """Extract visible-ish text and page links from downloaded HTML."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self.links = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag in ("script", "style", "noscript", "svg"):
            self.skip_depth += 1

        if tag == "a" and attrs.get("href"):
            self.links.append({
                "text": "",
                "href": attrs["href"],
            })

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip_depth = max(0, self.skip_depth - 1)

    def handle_data(self, data):
        if self.skip_depth == 0:
            text = data.strip()
            if text:
                self.parts.append(text)

            if self.links:
                self.links[-1]["text"] += " " + text

    def get_text(self):
        return re.sub(
            r"\s+", " ", html.unescape(" ".join(self.parts))
        ).strip()


def fetch_page(url):
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; "
                "AcatenangoAvailabilityMonitor/1.0)"
            )
        },
    )

    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        page_url = response.geturl()
        raw = response.read().decode("utf-8", errors="replace")

    parser = TextExtractor()
    parser.feed(raw)

    return {
        "url": page_url,
        "text": parser.get_text(),
        "links": parser.links,
    }


def detect_candidate(page):
    """
    Return a possible availability clue only when the target date
    and availability language occur close together.

    This does NOT confirm live inventory or guarantee two seats.
    """
    text = page["text"]

    for match in TARGET_DATE.finditer(text):
        start = max(0, match.start() - 500)
        end = min(len(text), match.end() + 500)
        context = text[start:end]

        if UNAVAILABLE_WORDS.search(context):
            continue

        if AVAILABILITY_WORDS.search(context):
            return context[:900]

    # Booking sites often mention the date in a link or button.
    for link in page["links"]:
        combined = link["text"] + " " + link["href"]

        if TARGET_DATE.search(combined) and AVAILABILITY_WORDS.search(combined):
            return f"Date-related booking link: {combined[:500]}"

    return None


def send_telegram(subject, body):
    """Send a Telegram message using GitHub Actions secrets."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret."
        )

    message = f"{subject}\n\n{body}"

    # Telegram text message limit is 4096 characters.
    message = message[:4000]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urlencode({
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    request = Request(url, data=data, method="POST")

    with urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError("Telegram API reported that sending failed.")

    print("Telegram notification sent.")


def load_state():
    if not STATE_FILE.exists():
        return {"operators": {}}

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if "operators" not in data:
            data["operators"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        print("Warning: state.json could not be read; starting fresh.")
        return {"operators": {}}


def main():
    print("Acatenango availability monitor starting.")
    print("Target: November 4, 2026; 2 people.")
    print("Checking public operator pages.")

    state = load_state()
    previous = state.get("operators", {})
    updated = dict(previous)

    alerts = []
    errors = []

    for name, url in OPERATORS.items():
        old_status = previous.get(name, {}).get("status", "unknown")

        try:
            page = fetch_page(url)
            clue = detect_candidate(page)

            if clue:
                new_status = "possible_opening"

                # Alert only when a possible opening first appears,
                # or returns after a previous non-opening state.
                if old_status != "possible_opening":
                    alerts.append({
                        "name": name,
                        "url": page["url"],
                        "clue": clue,
                    })
            else:
                new_status = "no_matching_clue"

            updated[name] = {
                "status": new_status,
                "url": page["url"],
                "checked_at_utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
            }

            print(f"{name}: {new_status}")

        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"{name}: CHECK FAILED: {exc}")

            # Preserve previous state when a check fails.
            # A failed fetch is not proof that a tour is full.

        except Exception as exc:
            errors.append(f"{name}: unexpected error: {exc}")
            print(f"{name}: UNEXPECTED ERROR: {exc}")

        # Be polite to operator websites.
        time.sleep(2)

    if alerts:
        lines = [
            "A possible Acatenango overnight opening was detected.",
            "",
            "Target departure: November 4, 2026",
            "Travelers: 2 people",
            "",
            "This is an automated page-text signal, not confirmation",
            "that two seats are bookable. Verify before paying.",
            "",
        ]

        for alert in alerts:
            lines.extend([
                f"Operator: {alert['name']}",
                f"Booking page: {alert['url']}",
                f"Page clue: {alert['clue']}",
                "",
            ])

        send_telegram(
            f"Possible Acatenango opening: {len(alerts)} operator(s)",
            "\n".join(lines),
        )

    if errors:
        # Report failures so a broken site doesn't silently look
        # like a successful availability check.
        error_body = (
            "Some operator pages could not be checked:\n\n"
            + "\n".join(errors[:10])
        )
        print(error_body)

        # Avoid sending error messages on every hourly run.
        # Failures are visible in the GitHub Actions logs.

    state["operators"] = updated
    state["last_run_utc"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
    )
    STATE_FILE.write_text(
        json.dumps(state, indent=2) + "\n",
        encoding="utf-8",
    )

    print("Monitor finished.")


if __name__ == "__main__":
    main()
