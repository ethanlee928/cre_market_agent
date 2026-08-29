"""Probe which Gemini models this API key can actually see.

Run once after putting GOOGLE_API_KEY in .env, so we pin a model ID that
is verified against the real key rather than assumed from docs.

    uv run python scripts/probe_models.py
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

if not os.getenv("GOOGLE_API_KEY"):
    sys.exit("GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key.")

from google import genai  # noqa: E402

client = genai.Client()

print(f"{'model id':<40} {'in':>9} {'out':>8}  methods")
print("-" * 90)
for m in client.models.list():
    name = m.name.removeprefix("models/")
    if "gemini" not in name:
        continue
    methods = ",".join(m.supported_actions or [])
    print(f"{name:<40} {m.input_token_limit or '-':>9} {m.output_token_limit or '-':>8}  {methods}")
