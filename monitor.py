
import os
import re
import json
import smtplib
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from email.message import EmailMessage
from pathlib import Path

TARGET_DATE = re.compile(
    r"\b(?:nov(?:ember)?\.?\s+4(?:,?\s+2026)?|2026-11-04)\b",
    re.IGNORECASE,
)

AVAILABILITY = re.compile(
    r"\b(?:available|availability|spots?\s+left|"
    r"book\s+now|select\s+this\s+date|choose\s+this\s+date|"
    r"check\s+availability|reserve|openings?|"
    r"places?\s+left|spaces?\s+available)\b",
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


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def fetch_page(url):
    request = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; AvailabilityMonitor/1.0)"},
    )
    with urlopen(request, timeout=25) as response:
        html = response.read().decode("utf-8", errors="replace")

    parser = TextExtractor()
    parser.feed(html)
    return parser.get_text()


def find_candidate(text):
    """Return a date-related availability clue, not a booking guarantee."""
    for match in TARGET_DATE.finditer(text):
        start = max(0, match.start() - 350)
        end = min(len(text), match.end() + 350)
        context = text[start:end]

        if AVAILABILITY.search(context):
            return context

    return None


def send_email(subject, body):
    username = os.environ["MAIL_USERNAME"]
    password = os.environ["MAIL_APP_PASSWORD"]
    recipient = os.environ["ALERT_EMAIL"]

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = username
    message["To"] = recipient
    message.set_content(body)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(message)


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    updated = dict(state)
    alerts = []

    for name, url in OPERATORS.items():
        try:
            text = fetch_page(url)
            clue = find_candidate(text)

            if clue:
                # Only alert on a transition into a possible opening.
                if state.get(name) != "open":
                    alerts.append((name, url, clue))
                updated[name] = "open"
            else:
                updated[name] = "closed"

            print(f"{name}: {'POSSIBLE OPENING' if clue else 'No matching clue'}")

        except Exception as exc:
            # A failed fetch is unknown, not proof that a tour is full.
            print(f"{name}: CHECK FAILED: {exc}")
            # Preserve the previous state on errors.

    if alerts:
        lines = [
            "Possible Acatenango overnight availability detected.",
            "Target: November 4, 2026; 2 hikers.",
            "",
            "Verify the date and two-person inventory before paying.",
            "",
        ]

        for name, url, clue in alerts:
            lines.extend([
                f"Operator: {name}",
                f"Booking page: {url}",
                f"Matching page text: {clue}",
                "",
            ])

        send_email(
            f"Possible Acatenango opening — Nov 4 — {len(alerts)} operator(s)",
            "\n".join(lines),
        )
        print(f"Sent email for {len(alerts)} possible opening(s).")

    STATE_FILE.write_text(json.dumps(updated, indent=2) + "\n")


if __name__ == "__main__":
    main()
