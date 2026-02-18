#!/usr/bin/env python3
"""Automated Baserow setup — creates user, database, and all 3 tables.

Run AFTER Baserow is running (docker compose up -d):
    python3 scripts/setup_baserow.py

This script will:
  1. Create an admin user in Baserow
  2. Create a "Janardan Saree Automation" workspace + database
  3. Create 3 tables with all required fields:
     - Posts (Social Media Post Management)
     - Client Content Hub
     - fabric_analysis
  4. Generate a database API token
  5. Print the .env values you need to set
"""

from __future__ import annotations

import json
import sys
import time

import requests

# ── Config ────────────────────────────────────────────────────────────

BASEROW_URL = "http://localhost"
ADMIN_EMAIL = "admin@janardan.local"
ADMIN_PASSWORD = "JanardanAdmin123!"

# ── Table schemas ─────────────────────────────────────────────────────

POSTS_TABLE_FIELDS = [
    {"name": "Saree", "type": "file"},
    {"name": "Status", "type": "single_select", "select_options": [
        {"value": "Draft", "color": "light-gray"},
        {"value": "MANNEQUIN_UPLOADED", "color": "light-blue"},
        {"value": "MANNEQUIN_UPSCALED", "color": "blue"},
        {"value": "ANALYZING_FABRIC", "color": "light-orange"},
        {"value": "FABRIC_ANALYZED", "color": "orange"},
        {"value": "GENERATING_MODEL", "color": "light-green"},
        {"value": "MODEL_GENERATED", "color": "green"},
        {"value": "Approved", "color": "dark-green"},
        {"value": "GENERATING_VIDEO", "color": "light-cyan"},
        {"value": "VIDEO_GENERATED", "color": "dark-cyan"},
        {"value": "Published", "color": "dark-blue"},
        {"value": "FAILED", "color": "dark-red"},
    ]},
    {"name": "ForntUpscaler", "type": "url"},
    {"name": "CloserUpscaler", "type": "url"},
    {"name": "BoradUpscaler", "type": "url"},
    {"name": "PalluDesignUpscaler", "type": "url"},
    {"name": "FRONT FULL VIEW", "type": "url"},
    {"name": "FRONT Video URL", "type": "url"},
    {"name": "fabric_analysis", "type": "long_text"},
    {"name": "fabric_analysis_id", "type": "number", "number_decimal_places": 0},
]

CLIENT_HUB_TABLE_FIELDS = [
    {"name": "FRONT Image URL", "type": "url"},
    {"name": "Status", "type": "single_select", "select_options": [
        {"value": "Draft", "color": "light-gray"},
        {"value": "Approved", "color": "dark-green"},
        {"value": "Published", "color": "dark-blue"},
        {"value": "FAILED", "color": "dark-red"},
    ]},
    {"name": "FRONT Video URL", "type": "url"},
    {"name": "FRONT Video", "type": "long_text"},
]

FABRIC_ANALYSIS_TABLE_FIELDS = [
    {"name": "product_row_id", "type": "number", "number_decimal_places": 0},
    {"name": "dominant_colors", "type": "long_text"},
    {"name": "weave_type", "type": "text"},
    {"name": "thread_density_warp", "type": "number", "number_decimal_places": 1},
    {"name": "thread_density_weft", "type": "number", "number_decimal_places": 1},
    {"name": "pattern_type", "type": "text"},
    {"name": "pattern_geometry", "type": "long_text"},
    {"name": "border_width_cm", "type": "number", "number_decimal_places": 1},
    {"name": "border_style", "type": "text"},
    {"name": "border_motif", "type": "text"},
    {"name": "pallu_length_cm", "type": "number", "number_decimal_places": 1},
    {"name": "pallu_style", "type": "text"},
    {"name": "pallu_design", "type": "text"},
    {"name": "fabric_type", "type": "text"},
    {"name": "transparency_level", "type": "text"},
    {"name": "transparency_score", "type": "number", "number_decimal_places": 3},
    {"name": "sheen_level", "type": "text"},
    {"name": "drape_stiffness", "type": "text"},
    {"name": "fabric_weight", "type": "text"},
    {"name": "has_zari", "type": "boolean"},
    {"name": "zari_type", "type": "text"},
    {"name": "zari_coverage_percent", "type": "number", "number_decimal_places": 2},
    {"name": "zari_areas", "type": "long_text"},
    {"name": "topography_map_url", "type": "url"},
    {"name": "transparency_map_url", "type": "url"},
    {"name": "body_texture_crop_url", "type": "url"},
    {"name": "border_crop_url", "type": "url"},
    {"name": "pallu_crop_url", "type": "url"},
    {"name": "single_motif_crop_url", "type": "url"},
    {"name": "zari_detail_crop_url", "type": "url"},
    {"name": "raw_analysis_json", "type": "long_text"},
    {"name": "analysis_confidence", "type": "number", "number_decimal_places": 3},
]


# ── Helpers ───────────────────────────────────────────────────────────

def wait_for_baserow(url: str, max_retries: int = 30) -> None:
    """Wait until Baserow API is reachable."""
    print(f"⏳ Waiting for Baserow at {url} ...", end="", flush=True)
    for i in range(max_retries):
        try:
            resp = requests.get(f"{url}/api/_health/", timeout=5)
            if resp.status_code == 200:
                print(" ✅ Ready!")
                return
        except requests.ConnectionError:
            pass
        print(".", end="", flush=True)
        time.sleep(3)
    print("\n❌ Baserow not reachable after 90s. Is it running?")
    sys.exit(1)


def api(method: str, path: str, token: str | None = None, **kwargs) -> dict:
    """Make an API call to Baserow."""
    url = f"{BASEROW_URL}/api{path}"
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"JWT {token}"
    headers["Content-Type"] = "application/json"

    resp = requests.request(method, url, headers=headers, **kwargs)
    if resp.status_code >= 400:
        print(f"  ❌ {method} {path} → {resp.status_code}: {resp.text[:200]}")
        return {}
    return resp.json() if resp.text else {}


# ── Main setup ────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Janardan — Baserow Automated Setup")
    print("=" * 60)
    print()

    # 1. Wait for Baserow
    wait_for_baserow(BASEROW_URL)

    # 2. Create admin user
    print("\n📝 Creating admin user...")
    user_resp = api("POST", "/user/", json={
        "name": "Janardan Admin",
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "authenticate": True,
        "language": "en",
    })

    if not user_resp:
        # User might already exist — try to log in
        print("  ↳ User may already exist, trying to log in...")
        user_resp = api("POST", "/user/token-auth/", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
        })

    token = user_resp.get("access_token") or user_resp.get("token")
    if not token:
        print("❌ Failed to get auth token. Response:", user_resp)
        sys.exit(1)
    print(f"  ✅ Authenticated (token: {token[:20]}...)")

    # 3. Create workspace
    print("\n📁 Creating workspace...")
    workspace = api("POST", "/workspaces/", token=token, json={
        "name": "Janardan Saree Automation",
    })
    workspace_id = workspace.get("id")
    if not workspace_id:
        # Get existing workspace
        workspaces = api("GET", "/workspaces/", token=token)
        if isinstance(workspaces, list) and workspaces:
            workspace_id = workspaces[0]["id"]
            print(f"  ↳ Using existing workspace: {workspaces[0]['name']} (ID: {workspace_id})")
        else:
            print("❌ Failed to create workspace")
            sys.exit(1)
    else:
        print(f"  ✅ Workspace created (ID: {workspace_id})")

    # 4. Create database
    print("\n🗄️  Creating database...")
    db = api("POST", f"/applications/workspace/{workspace_id}/", token=token, json={
        "name": "Saree Pipeline",
        "type": "database",
    })
    db_id = db.get("id")
    if not db_id:
        print("❌ Failed to create database")
        sys.exit(1)
    print(f"  ✅ Database created (ID: {db_id})")

    # 5. Create tables
    table_ids = {}
    tables_to_create = [
        ("Posts", POSTS_TABLE_FIELDS),
        ("Client Content Hub", CLIENT_HUB_TABLE_FIELDS),
        ("fabric_analysis", FABRIC_ANALYSIS_TABLE_FIELDS),
    ]

    for table_name, fields in tables_to_create:
        print(f"\n📊 Creating table: {table_name}...")
        table = api("POST", f"/database/tables/database/{db_id}/", token=token, json={
            "name": table_name,
        })
        table_id = table.get("id")
        if not table_id:
            print(f"  ❌ Failed to create table: {table_name}")
            continue

        table_ids[table_name] = table_id
        print(f"  ✅ Table created (ID: {table_id})")

        # Add fields
        for field_def in fields:
            field_name = field_def["name"]
            resp = api("POST", f"/database/fields/table/{table_id}/", token=token, json=field_def)
            if resp.get("id"):
                print(f"    + {field_name} ({field_def['type']})")
            else:
                print(f"    ⚠ {field_name} — may already exist")

    # 6. Create database API token
    print("\n🔑 Creating database API token...")
    token_resp = api("POST", "/database/tokens/", token=token, json={
        "name": "janardan-pipeline",
        "workspace": workspace_id,
    })
    db_token = token_resp.get("key")
    if not db_token:
        print("  ⚠ Could not create DB token automatically")
        db_token = "CREATE_MANUALLY_IN_BASEROW_UI"
    else:
        # Grant permissions to all tables
        for t_name, t_id in table_ids.items():
            api("PATCH", f"/database/tokens/{token_resp['id']}/", token=token, json={
                "permissions": {
                    "create": [{"type": "table", "id": t_id}],
                    "read": [{"type": "table", "id": t_id}],
                    "update": [{"type": "table", "id": t_id}],
                    "delete": [{"type": "table", "id": t_id}],
                }
            })
        print(f"  ✅ Token created: {db_token}")

    # 7. Print results
    print("\n" + "=" * 60)
    print("  ✅ SETUP COMPLETE!")
    print("=" * 60)
    print()
    print("Copy these values into your .env file:")
    print()
    print(f"BASEROW_URL={BASEROW_URL}")
    print(f"BASEROW_TOKEN={db_token}")
    print(f"BASEROW_POSTS_TABLE_ID={table_ids.get('Posts', 0)}")
    print(f"BASEROW_CLIENT_HUB_TABLE_ID={table_ids.get('Client Content Hub', 0)}")
    print(f"BASEROW_FABRIC_ANALYSIS_TABLE_ID={table_ids.get('fabric_analysis', 0)}")
    print()
    print(f"Baserow Web UI: {BASEROW_URL}")
    print(f"Login: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
    print()

    # 8. Offer to auto-update .env
    try:
        answer = input("Auto-update your .env file with these values? [y/N]: ").strip().lower()
        if answer == "y":
            _update_env_file(db_token, table_ids)
    except (EOFError, KeyboardInterrupt):
        print("\nSkipped auto-update.")


def _update_env_file(db_token: str, table_ids: dict) -> None:
    """Update the .env file with Baserow values."""
    import re
    from pathlib import Path

    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        print(f"  ❌ .env not found at {env_path}")
        return

    content = env_path.read_text()

    replacements = {
        "BASEROW_URL": BASEROW_URL,
        "BASEROW_TOKEN": db_token,
        "BASEROW_POSTS_TABLE_ID": str(table_ids.get("Posts", 0)),
        "BASEROW_CLIENT_HUB_TABLE_ID": str(table_ids.get("Client Content Hub", 0)),
        "BASEROW_FABRIC_ANALYSIS_TABLE_ID": str(table_ids.get("fabric_analysis", 0)),
    }

    for key, value in replacements.items():
        pattern = rf"^{key}=.*$"
        replacement = f"{key}={value}"
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

    env_path.write_text(content)
    print(f"  ✅ .env updated at {env_path}")


if __name__ == "__main__":
    main()
