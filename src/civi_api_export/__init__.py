import requests
import os
import sys
import logging
import json
from flask import Flask
import waitress


FORMAT = "%(asctime)s %(levelname)s: %(message)s"
logging.basicConfig(format=FORMAT, level=logging.INFO)
logging.getLogger("urllib3").setLevel(logging.WARNING)


app = Flask(__name__)

if os.getenv("DEBUG", "") == "1":
    logging.getLogger().setLevel(logging.DEBUG)


BASE_URL = API_KEY = QUERIES = None
error_counter = 0


def init() -> None:
    global BASE_URL, API_KEY, QUERIES

    try:
        BASE_URL = os.environ["CIVICRM_BASE_URL"]
        API_KEY = os.environ["CIVICRM_API_KEY"]
        QUERIES = os.environ["CIVICRM_SEARCHKIT_QUERIES"]
    except KeyError as e:
        logging.error(f"Missing environment variable: {e}")
        sys.exit(1)


def get_searchkit_data(searchkit_name: str) -> dict:
    global error_counter
    logging.info(f"Asking CiviCRM API for {searchkit_name}")

    headers = {
        "X-Civi-Auth": f"Bearer {API_KEY}",
        "User-Agent": "Searchkit API Exporter",
    }

    params = {
        "savedSearch": searchkit_name,
        "display": None,
    }

    try:
        resp = requests.post(
            f"{BASE_URL}/civicrm/ajax/api4/SearchDisplay/run",
            params={"params": json.dumps(params)},
            headers=headers
        )

        resp.raise_for_status()
        logging.debug(f"Got status code {resp.status_code}")

        json_data = resp.json()
        data = json_data["values"][0]["columns"][0]["val"]
        logging.debug(f"Returning value {data}")
        return data
    except Exception as e:
        logging.error(f"Error getting Searchkit response: {e}")
        error_counter += 1
        return "API Fehler"


@app.route("/api/v1/civi")
def export_civi_data() -> dict:
    data = {}
    for searchkit_name in QUERIES.split(","):
        if searchkit_name != "":
            data[searchkit_name] = get_searchkit_data(searchkit_name)
    return data


@app.route("/api/v1/status")
def status():
    if error_counter == 0:
        return {"status": "ok"}
    else:
        return {"status": "failed"}


def run_backend() -> None:
    init()
    if __name__ == '__main__':
        app.run(debug=True,
                host="127.0.0.1",
                port=5000)
    else:
        threads = os.environ.get("THREADS", "4")
        print(f"Running Civi API Exporter backend with {threads} threads")
        waitress.serve(app, listen="0.0.0.0:5000", threads=threads)


if __name__ == '__main__':
    run_backend()
