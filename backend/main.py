# backend/main.py

"""
ASGI entry point. Run from the backend/ directory (so .env resolves):

    uvicorn main:app --reload --port 8000

Use a single worker for now - pipeline state is in-memory.
"""

from api.app import create_app

app = create_app()
