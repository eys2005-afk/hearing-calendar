"""Flask server exposing hearing calendar data as JSON + HTML frontend."""

import logging
from datetime import date

from flask import Flask, jsonify, render_template, request

from fetcher import fetch_month

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

DEFAULT_USER_ID = "1"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/hearings")
def api_hearings():
    """
    Query params:
      user_id  (default 1)
      year     (default current year)
      month    (default current month)
    Returns JSON list of hearing dicts.
    """
    today = date.today()
    user_id = request.args.get("user_id", DEFAULT_USER_ID)
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
    except ValueError:
        return jsonify({"error": "Invalid year/month"}), 400

    try:
        hearings = fetch_month(user_id, year, month)
        return jsonify(hearings)
    except Exception as e:
        logging.exception("Failed to fetch hearings")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
