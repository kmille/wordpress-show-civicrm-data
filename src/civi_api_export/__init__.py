import requests
import os
import sys
import logging
import json
from flask import Flask, request
import waitress


FORMAT = "%(asctime)s %(levelname)s: %(message)s"
logging.basicConfig(format=FORMAT, level=logging.INFO)
logging.getLogger("urllib3").setLevel(logging.WARNING)


app = Flask(__name__)

if os.getenv("DEBUG", "") == "1":
    logging.getLogger().setLevel(logging.DEBUG)


BASE_URL = API_KEY = None
QUERY_WHITELIST = []
error_counter = 0


def init() -> None:
    global BASE_URL, API_KEY, QUERY_WHITELIST

    try:
        BASE_URL = os.environ["CIVICRM_BASE_URL"]
        API_KEY = os.environ["CIVICRM_API_KEY"]
        QUERY_WHITELIST = os.environ["CIVICRM_SEARCHKIT_QUERIES"].split(",")
    except KeyError as e:
        logging.error(f"Missing environment variable: {e}")
        sys.exit(1)


def get_searchkit_data(searchkit_name: str) -> dict:
    logging.info(f"Asking CiviCRM API for {searchkit_name}")

    headers = {
        "X-Civi-Auth": f"Bearer {API_KEY}",
        "User-Agent": "Searchkit API Exporter",
    }

    params = {
        "savedSearch": searchkit_name,
        "display": None,
    }

    resp = requests.post(
        f"{BASE_URL}/civicrm/ajax/api4/SearchDisplay/run",
        params={"params": json.dumps(params)},
        headers=headers,
        timeout=5,
    )

    resp.raise_for_status()
    logging.debug(f"Got status code {resp.status_code}")

    json_data = resp.json()
    data = json_data["values"][0]["columns"][0]["val"]
    logging.debug(f"Returning value {data}")
    return data


@app.route("/api/v1/civi/get")
def export_civi_data() -> dict:
    global error_counter
    searchkit_query = request.args.get("query", "")
    if searchkit_query not in QUERY_WHITELIST:
        return {"error": "invalid query - not in whitelist"}, 400
    try:
        value = get_searchkit_data(searchkit_query)
        return {"value": value}
    except Exception as e:
        logging.error(f"Error getting Searchkit response: {e}")
        error_counter += 1
        return {"error": "api error"}, 500


@app.route("/api/v1/civi/list")
def list_whitelisted_search_queries():
    return {"whitelist": QUERY_WHITELIST}


@app.route("/api/v1/status")
def status():
    if error_counter == 0:
        return {"status": "ok"}
    else:
        return {"status": "failed",
                "error_counter": error_counter}


def run_backend() -> None:
    init()
    if __name__ == '__main__':
        app.run(debug=True,
                threaded=False,
                host="127.0.0.1",
                port=5000)
    else:
        threads = os.environ.get("THREADS", "4")
        print(f"Running Civi API Exporter backend with {threads} threads")
        waitress.serve(app, listen="0.0.0.0:5000", threads=threads)


if __name__ == '__main__':
    run_backend()
