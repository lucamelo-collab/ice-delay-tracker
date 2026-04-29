import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
THRESHOLD_MINUTES = int(os.environ.get("THRESHOLD_MINUTES", "20"))
TEST_NOTIFICATION = os.environ.get("TEST_NOTIFICATION", "false").lower() == "true"

API_BASE = "https://v6.db.transport.rest"

# Station-IDs:
# Kassel-Wilhelmshöhe: 8003200
# Fulda: 8000115
KASSEL_WILHELMSHOEHE = "8003200"
FULDA = "8000115"

LOOKBACK_MINUTES = 75
RESULTS_PER_STATION = 40

NOTIFIED_FILE = Path("notified_trains.json")

ARRIVAL_CHECKS = [
    {
        "arrival_station_id": FULDA,
        "arrival_station_name": "Fulda",
        "must_have_previous_station_id": KASSEL_WILHELMSHOEHE,
        "must_have_previous_station_name": "Kassel-Wilhelmshöhe",
        "label": "Kassel-Wilhelmshöhe → Fulda",
    },
    {
        "arrival_station_id": KASSEL_WILHELMSHOEHE,
        "arrival_station_name": "Kassel-Wilhelmshöhe",
        "must_have_previous_station_id": FULDA,
        "must_have_previous_station_name": "Fulda",
        "label": "Fulda → Kassel-Wilhelmshöhe",
    },
]


def parse_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_time(dt):
    if not dt:
        return "unbekannt"
    return dt.astimezone().strftime("%H:%M")


def send_notification(title: str, message: str):
    if not NTFY_TOPIC:
        raise RuntimeError("NTFY_TOPIC fehlt. Bitte in GitHub Secrets eintragen.")

    response = requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "high",
            "Tags": "train,warning",
        },
        timeout=20,
    )
    response.raise_for_status()


def load_notified_ids():
    if not NOTIFIED_FILE.exists():
        return set()

    try:
        data = json.loads(NOTIFIED_FILE.read_text(encoding="utf-8"))
        return set(data.get("notified_ids", []))
    except Exception as error:
        print(f"Konnte {NOTIFIED_FILE} nicht lesen: {error}")
        return set()


def save_notified_ids(notified_ids):
    # Damit die Datei nicht endlos wächst, behalten wir nur die letzten 500 Einträge.
    sorted_ids = sorted(notified_ids)[-500:]
    data = {
        "notified_ids": sorted_ids,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    NOTIFIED_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_json(url, params=None):
    try:
        response = requests.get(url, params=params, timeout=30)

        if response.status_code == 503:
            print("Datenquelle gerade nicht erreichbar: HTTP 503 Service Unavailable.")
            return None

        if response.status_code == 429:
            print("Datenquelle meldet zu viele Anfragen: HTTP 429 Too Many Requests.")
            return None

        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as error:
        print(f"Fehler beim Abruf der Bahndaten: {error}")
        return None


def get_arrivals(station_id):
    now = datetime.now(timezone.utc)
    start_time = now - timedelta(minutes=LOOKBACK_MINUTES)

    params = {
        "when": start_time.isoformat(),
        "duration": str(LOOKBACK_MINUTES),
        "results": str(RESULTS_PER_STATION),
        "remarks": "true",
        "stopovers": "false",
        "includeRelatedStations": "false",
        "nationalExpress": "true",
        "national": "false",
        "regionalExpress": "false",
        "regional": "false",
        "suburban": "false",
        "bus": "false",
        "ferry": "false",
        "subway": "false",
        "tram": "false",
        "taxi": "false",
    }

    data = get_json(f"{API_BASE}/stops/{station_id}/arrivals", params=params)
    if not data:
        return []

    return data


def get_trip(trip_id):
    if not trip_id:
        return None

    encoded_trip_id = quote(trip_id, safe="")
    url = f"{API_BASE}/trips/{encoded_trip_id}"

    params = {
        "stopovers": "true",
        "remarks": "false",
        "polyline": "false",
    }

    return get_json(url, params=params)


def is_ice(arrival):
    line = arrival.get("line") or {}
    line_name = line.get("name") or ""
    product = line.get("product") or ""
    product_name = line.get("productName") or ""

    return (
        line_name.startswith("ICE")
        or product == "nationalExpress"
        or product_name.startswith("ICE")
    )


def trip_contains_route_in_correct_order(trip_data, previous_station_id, arrival_station_id):
    if not trip_data:
        return False

    trip = trip_data.get("trip") or trip_data
    stopovers = trip.get("stopovers") or []

    previous_index = None
    arrival_index = None

    for index, stopover in enumerate(stopovers):
        stop = stopover.get("stop") or {}
        stop_id = str(stop.get("id") or "")

        if stop_id == str(previous_station_id):
            previous_index = index

        if stop_id == str(arrival_station_id):
            arrival_index = index

    if previous_index is None or arrival_index is None:
        return False

    return previous_index < arrival_index


def build_notification_id(arrival, check):
    trip_id = arrival.get("tripId") or "unknown-trip"
    planned_when = arrival.get("plannedWhen") or "unknown-planned"
    arrival_station_id = check["arrival_station_id"]

    return f"{trip_id}|{arrival_station_id}|{planned_when}"


def check_arrival_board(check, notified_ids):
    print(f"Prüfe Ankünfte: {check['label']}")

    arrivals = get_arrivals(check["arrival_station_id"])
    alerts_sent = 0
    now = datetime.now(timezone.utc)

    for arrival in arrivals:
        if not is_ice(arrival):
            continue

        line = arrival.get("line") or {}
        train_name = line.get("name") or "Unbekannter ICE"

        planned_arrival = parse_iso(arrival.get("plannedWhen"))
        actual_arrival = parse_iso(arrival.get("when"))

        if not planned_arrival or not actual_arrival:
            print(f"{train_name}: Keine vollständigen Ankunftszeiten vorhanden.")
            continue

        # Nur bereits angekommene bzw. zeitlich erreichte Ankünfte prüfen.
        if actual_arrival > now:
            print(
                f"{train_name}: Noch nicht angekommen. "
                f"Geplant {format_time(planned_arrival)}, prognostiziert {format_time(actual_arrival)}."
            )
            continue

        delay_minutes = round((actual_arrival - planned_arrival).total_seconds() / 60)

        if delay_minutes <= THRESHOLD_MINUTES:
            continue

        notification_id = build_notification_id(arrival, check)

        if notification_id in notified_ids:
            print(f"{train_name}: Bereits gemeldet, überspringe.")
            continue

        trip_id = arrival.get("tripId")
        trip_data = get_trip(trip_id)

        # Kleine Pause, damit wir die API nicht unnötig hart belasten.
        time.sleep(0.5)

        if not trip_contains_route_in_correct_order(
            trip_data,
            check["must_have_previous_station_id"],
            check["arrival_station_id"],
        ):
            print(
                f"{train_name}: Mehr als {THRESHOLD_MINUTES} Minuten verspätet, "
                f"aber Strecke {check['label']} nicht sicher bestätigt."
            )
            continue

        message = (
            f"{train_name}\n"
            f"Strecke: {check['label']}\n"
            f"Tatsächliche Verspätung bei Ankunft: {delay_minutes} Minuten\n"
            f"Geplante Ankunft: {format_time(planned_arrival)}\n"
            f"Tatsächliche Ankunft: {format_time(actual_arrival)}\n"
            f"Ankunftsbahnhof: {check['arrival_station_name']}"
        )

        print(message)
        send_notification("ICE tatsächlich verspätet angekommen", message)

        notified_ids.add(notification_id)
        alerts_sent += 1

    return alerts_sent


def main():
    if TEST_NOTIFICATION:
        send_notification(
            "Test ICE Tracker",
            "Testnachricht: Dein ICE-Verspätungstracker kann Benachrichtigungen aufs Handy senden.",
        )
        print("Testbenachrichtigung wurde gesendet.")
        return

    notified_ids = load_notified_ids()
    total_alerts_sent = 0

    for check in ARRIVAL_CHECKS:
        total_alerts_sent += check_arrival_board(check, notified_ids)

    save_notified_ids(notified_ids)

    if total_alerts_sent == 0:
        print(
            f"Keine tatsächlich angekommenen ICEs auf Kassel ↔ Fulda "
            f"mit mehr als {THRESHOLD_MINUTES} Minuten Verspätung gefunden."
        )
    else:
        print(f"{total_alerts_sent} Benachrichtigung(en) gesendet.")


if __name__ == "__main__":
    main()
