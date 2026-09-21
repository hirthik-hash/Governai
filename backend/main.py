# backend/main.py

"""
ASGI entry point. Run from the backend/ directory (so .env resolves):

    uvicorn main:app --reload --port 8000

Use a single worker for now - pipeline state is in-memory.

In development this creates governai_dev.db, loads the demo users and
resources, and gives every demo user the DEMO_USER_PASSWORD (see
.env.example) so you can log in at /docs.
"""

from api.bootstrap import build_app

app = build_app()
