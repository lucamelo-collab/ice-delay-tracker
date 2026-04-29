import os
import requests
from datetime import datetime

NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
THRESHOLD_MINUTES = int(os.environ.get("THRESHOLD_MINUTES", "20"))
TEST_NOTIFICATION = os.environ.get("TEST_NOTIFICATION", "false").lower() == "true"

# Station-IDs:
# Kassel-Wilhelmshöhe: 8003200
# Fulda: 8000115
KASSEL_WILHELMSHOEHE = "8003200"
FULDA = "8000115"

API_BASE = "https://v6.db.transport.rest"

ROUTES = [
    {
        "from": KASSEL_WILHELMSHOEHE,
        "to": FULDA,
        "label": "Kassel-Wilhelmshöhe → Fulda",
    },
    {
        "from": FULDA,
        "to": KASSEL_WILHELMSHOEHE,
        "label": "Fulda → Kassel-Wilhelmshöhe",
    },
]


def send_notification(title: str, message: str):
    if not NTFY_TOPIC:
        raise RuntimeError("NTFY_TOPIC fehlt. Bitte in GitHub Secrets eintragen.")

    url = f"https://ntfy.sh/{NTFY_TOPIC}"

    response = requests.post(
        url,
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "high",
            "Tags": "train,warning",
        },
        timeout=20,
    )

    response.raise_for_status()


def parse_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_journeys(from_station: str, to_station: str):
    params = {
        "from": from_station,
        "to": to_station,
        "results": 8,
        "nationalExpress": "true",
        "national": "false",
        "regionalExp": "false",
        "regional": "false",
        "suburban": "false",
        "bus": "false",
        "ferry": "false",
        "subway": "false",
        "tram": "false",
        "taxi": "false",
        "remarks": "true",
        "stopovers": "true",
    }

    response = requests.get(f"{API_BASE}/journeys", params=params, timeout=30)
    response.raise_for_status()

    return response.json().get("journeys", [])


def check_route(route):
    journeys = get_journeys(route["from"], route["to"])
    alerts = []

    for journey in journeys:
        legs = journey.get("legs", [])

        for leg in legs:
            line = leg.get("line") or {}
            product_name = line.get("productName") or ""
            name = line.get("name") or "Unbekannter Zug"

            if not product_name.startswith("ICE") and not name.startswith("ICE"):
                continue

            delay_seconds = leg.get("arrivalDelay")

            if delay_seconds is None:
                continue

            delay_minutes = round(delay_seconds / 60)

            if delay_minutes <= THRESHOLD_MINUTES:
                continue

            planned_arrival = parse_iso(leg.get("plannedArrival"))
            actual_arrival = parse_iso(leg.get("arrival"))

            planned_text = planned_arrival.strftime("%H:%M") if planned_arrival else "unbekannt"
            actual_text = actual_arrival.strftime("%H:%M") if actual_arrival else "unbekannt"

            origin = leg.get("origin", {}).get("name", "unbekannt")
            destination = leg.get("destination", {}).get("name", "unbekannt")

            message = (
                f"{name}\n"
                f"Strecke: {origin} → {destination}\n"
                f"Überwachung: {route['label']}\n"
                f"Verspätung bei Ankunft: {delay_minutes} Minuten\n"
                f"Geplant: {planned_text}\n"
                f"Aktuell/prognostiziert: {actual_text}"
            )

            alerts.append(message)

    return alerts


def main():
    if TEST_NOTIFICATION:
        send_notification(
            "Test ICE Tracker",
            "Testnachricht: Dein ICE-Verspätungstracker kann Benachrichtigungen aufs Handy senden."
        )
        print("Testbenachrichtigung wurde gesendet.")
        return

    all_alerts = []

    for route in ROUTES:
        print(f"Prüfe Route: {route['label']}")
        route_alerts = check_route(route)
        all_alerts.extend(route_alerts)

    if not all_alerts:
        print(f"Keine ICE-Ankunft mit mehr als {THRESHOLD_MINUTES} Minuten Verspätung gefunden.")
        return

    for alert in all_alerts:
        print(alert)
        send_notification("ICE-Verspätung erkannt", alert)


if __name__ == "__main__":
    main()
