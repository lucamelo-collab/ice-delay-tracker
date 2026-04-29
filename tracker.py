import os
import requests
from datetime import datetime, timezone

NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
THRESHOLD_MINUTES = int(os.environ.get("THRESHOLD_MINUTES", "20"))

# Station-IDs laut DB/HAFAS:
# Kassel-Wilhelmshöhe: 8003200
# Fulda: 8000115
FROM_STATION = "8003200"
TO_STATION = "8000115"

API_BASE = "https://v6.db.transport.rest"


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


def get_journeys():
    params = {
        "from": FROM_STATION,
        "to": TO_STATION,
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


def parse_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_journeys():
    journeys = get_journeys()
    alerts = []

    for journey in journeys:
        legs = journey.get("legs", [])
        for leg in legs:
            line = leg.get("line") or {}
            product_name = line.get("productName") or ""
            name = line.get("name") or "Unbekannter Zug"

            if not product_name.startswith("ICE") and not name.startswith("ICE"):
                continue

            arrival = parse_iso(leg.get("plannedArrival"))
            actual_arrival = parse_iso(leg.get("arrival"))

            delay_seconds = leg.get("arrivalDelay")

            if delay_seconds is None:
                continue

            delay_minutes = round(delay_seconds / 60)

            if delay_minutes > THRESHOLD_MINUTES:
                origin = leg.get("origin", {}).get("name", "Kassel-Wilhelmshöhe")
                destination = leg.get("destination", {}).get("name", "Fulda")

                planned_text = arrival.strftime("%H:%M") if arrival else "unbekannt"
                actual_text = actual_arrival.strftime("%H:%M") if actual_arrival else "unbekannt"

                alerts.append(
                    f"{name}: {origin} → {destination}\n"
                    f"Verspätung bei Ankunft: {delay_minutes} Minuten\n"
                    f"Geplant: {planned_text}, aktuell/prognostiziert: {actual_text}"
                )

    return alerts


def main():
    alerts = check_journeys()

    if not alerts:
        print("Keine ICE-Ankunft mit mehr als 20 Minuten Verspätung gefunden.")
        return

    for alert in alerts:
        print(alert)
        send_notification("ICE-Verspätung Kassel → Fulda", alert)


if __name__ == "__main__":
    main()
