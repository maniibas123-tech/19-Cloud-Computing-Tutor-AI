import logging
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from google import genai
from google.genai import types
from werkzeug.exceptions import HTTPException

import chatbot_config as cfg

# Reads GEMINI_API_KEY from a local .env file (if present) or from the server's environment.
load_dotenv()

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# The Gemini model used by this chatbot (set explicitly, never auto-selected).
MODEL_NAME = "gemini-3.1-flash-lite"

MAX_MESSAGE_CHARS = 2000
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARS = 6000
PLACEHOLDER_KEY = "YOUR_GEMINI_API_KEY_HERE"

_client = None


def get_api_key():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or key == PLACEHOLDER_KEY:
        return None
    return key


def get_client():
    """Create the Gemini client once, or return None if no API key is set."""
    global _client
    key = get_api_key()
    if key is None:
        return None
    if _client is None:
        _client = genai.Client(api_key=key)
    return _client


def build_contents(history, message):
    """Turn the browser's chat history plus the new message into Gemini contents."""
    contents = []
    if isinstance(history, list):
        for item in history[-MAX_HISTORY_MESSAGES:]:
            if not isinstance(item, dict):
                continue
            text = item.get("content")
            if not isinstance(text, str) or not text.strip():
                continue
            role = "user" if item.get("role") == "user" else "model"
            contents.append(
                types.Content(role=role, parts=[types.Part(text=text[:MAX_HISTORY_CHARS])])
            )
    # A conversation must begin with a user turn.
    while contents and contents[0].role != "user":
        contents.pop(0)
    contents.append(types.Content(role="user", parts=[types.Part(text=message)]))
    return contents


def friendly_error(exc):
    """Convert an API exception into a short message that is safe to show users."""
    text = str(exc).lower()
    if "api key" in text or "api_key" in text or "401" in text or "403" in text or "permission" in text:
        return "The AI service rejected the API key. Please check the GEMINI_API_KEY setting on the server."
    if "429" in text or "quota" in text or "resource_exhausted" in text or "rate limit" in text:
        return "The AI service is busy or the usage limit was reached. Please wait a moment and try again."
    if "404" in text or "not found" in text:
        return "The AI model is not available right now. Please try again later."
    if "timeout" in text or "timed out" in text or "connect" in text or "unavailable" in text or "503" in text:
        return "Could not reach the AI service. Please check the connection and try again."
    return "Sorry, something went wrong while generating the answer. Please try again."


@app.route("/")
def index():
    return render_template(
        "index.html",
        title=cfg.BOT_TITLE,
        subtitle=cfg.BOT_SUBTITLE,
        welcome=cfg.WELCOME_MESSAGE,
        suggestions=cfg.SUGGESTIONS,
    )


@app.route("/health")
def health():
    return jsonify({"status": "ok", "api_key_configured": get_api_key() is not None})


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request. Please send a JSON message."}), 400

    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "Please type a message first."}), 400
    message = message.strip()
    if len(message) > MAX_MESSAGE_CHARS:
        return jsonify({"error": "Your message is too long. Please keep it under %d characters." % MAX_MESSAGE_CHARS}), 400

    client = get_client()
    if client is None:
        return jsonify({"error": "The server is not configured yet. Set the GEMINI_API_KEY environment variable and restart."}), 503

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=build_contents(data.get("history"), message),
            config=types.GenerateContentConfig(
                system_instruction=cfg.SYSTEM_PROMPT,
                temperature=0.5,
                max_output_tokens=2048,
            ),
        )
        reply = (response.text or "").strip()
        if not reply:
            return jsonify({"error": "The AI did not return an answer. Please rephrase your question and try again."}), 502
        return jsonify({"reply": reply})
    except Exception as exc:  # Never let an API problem crash the server.
        logger.error("Gemini API error: %s", exc)
        return jsonify({"error": friendly_error(exc)}), 502


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    if isinstance(exc, HTTPException):
        return exc
    logger.exception("Unexpected server error")
    return jsonify({"error": "Unexpected server error. Please try again."}), 500


if __name__ == "__main__":
    # Local development only. In production run: gunicorn app:app
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
