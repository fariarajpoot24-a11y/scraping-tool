"""Flask backend: serves the web page and exposes POST /api/scrape."""

import requests
from flask import Flask, jsonify, request, send_from_directory

from scraper import ScrapeError, scrape

app = Flask(__name__, static_folder="static", static_url_path="/static")


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/api/scrape")
def api_scrape():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    try:
        max_pages = max(1, min(15, int(data.get("max_pages", 6))))
    except (TypeError, ValueError):
        max_pages = 6

    try:
        return jsonify(scrape(url, max_pages))
    except ScrapeError as exc:
        return jsonify(error=str(exc)), 400
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "error"
        return jsonify(error=f"The website replied with an error ({code})."), 502
    except requests.Timeout:
        return jsonify(error="The website took too long to respond."), 504
    except requests.RequestException:
        return jsonify(error="Could not connect to the website."), 502
    except Exception:
        app.logger.exception("Unexpected scraping error")
        return jsonify(error="Something went wrong while reading that site."), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
