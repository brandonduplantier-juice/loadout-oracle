"""Production entry point. Run with: gunicorn wsgi:app"""
import os
from app import app  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
