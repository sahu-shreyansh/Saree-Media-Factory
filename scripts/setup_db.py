#!/usr/bin/env python3
"""Automated Baserow setup — creates user, database, Posts table with clean schema.

Run AFTER Baserow is running (docker compose up -d):
    python3 scripts/setup_db.py

Modes:
  --fresh     Create from scratch (new workspace + database + table)
  --migrate   Update existing table: add missing fields, add missing status options,
              delete legacy fields (interactive confirmation)

This script defines the SINGLE SOURCE OF TRUTH for all Baserow fields.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────

BASEROW_URL = "http://localhost"
ADMIN_EMAIL = "admin@janardan.local"
ADMIN_PASSWORD = "JanardanAdmin123!"

# ── Canonical Schema (Single Source of Truth) ─────────────────────────
# Only ONE table: Posts. All pipeline fields defined here.

STATUS_OPTIONS = [
    {"value": "Draft", "color": "light-gray"},
    {"value": "MANNEQUIN_UPLOADED", "color": "light-blue"},
    {"value": "UPSCALING_MANNEQUIN", "color": "blue"},
    {"value": "MANNEQUIN_UPSCALED", "color": "blue"},
    {"value": "GENERATING_MODEL", "color": "light-green"},
    {"value": "MODEL_GENERATED", "color": "green"},
    {"value": "UPSCALING_MODEL", "color": "green"},
    {"value": "MODEL_UPSCALED", "color": "green"},
    {"value": "GENERATING_HERO", "color": "light-green"},
    {"value": "HERO_READY", "color": "dark-green"},
    {"value": "APPROVED_FOR_ANGLES", "color": "dark-green"},
    {"value": "GENERATING_ANGLES", "color": "light-cyan"},
    {"value": "ANGLES_GENERATED", "color": "light-cyan"},
    {"value": "UPSCALING_ANGLES", "color": "light-cyan"},
    {"value": "IMAGES_READY", "color": "dark-cyan"},
    {"value": "APPROVED_FOR_VIDEO", "color": "dark-cyan"},
    {"value": "GENERATING_VIDEO", "color": "light-orange"},
    {"value": "PUBLISHED", "color": "dark-blue"},
    # Failure statuses
    {"value": "FAILED_STAGE_1", "color": "light-red"},
    {"value": "FAILED_STAGE_2", "color": "light-red"},
    {"value": "FAILED_STAGE_ANGLES", "color": "light-red"},
    {"value": "FAILED_STAGE_VIDEO", "color": "light-red"},
    # Dead letter
    {"value": "DEAD_LETTER", "color": "dark-red"},
]

# All required fields for the Posts table.
# "Name" is auto-created by Baserow as the primary field.
POSTS_FIELDS = [
    # ── Input files (uploaded by user) ────────────────────
    {"name": "Saree Front", "type": "file"},
    {"name": "Saree Closer", "type": "file"},
    {"name": "Saree Border", "type": "file"},
    {"name": "Saree Pallu", "type": "file"},

    # ── Pipeline status ───────────────────────────────────
    {"name": "Status", "type": "single_select", "select_options": STATUS_OPTIONS},
    {"name": "Processing", "type": "boolean"},

    # ── Stage 1: Mannequin Upscale ────────────────────────
    {"name": "mannequin_front_upscaled", "type": "url"},
    {"name": "mannequin_closer_upscaled", "type": "url"},
    {"name": "mannequin_border_upscaled", "type": "url"},
    {"name": "mannequin_pallu_upscaled", "type": "url"},

    # ── Stage 2: Model Generation ─────────────────────────
    {"name": "model_image_raw", "type": "url"},
    {"name": "model_image_upscaled", "type": "url"},

    # ── Stage 4: Angle Images ─────────────────────────────
    {"name": "angle_side_final", "type": "url"},
    {"name": "angle_back_final", "type": "url"},
    {"name": "angle_closeup_final", "type": "url"},
    {"name": "angle_movement_final", "type": "url"},

    # ── Stage 5: Video ────────────────────────────────────
    {"name": "video_url", "type": "url"},

    # ── Retry / Error tracking ────────────────────────────
    {"name": "retry_count", "type": "number", "number_decimal_places": 0},
    {"name": "error_message", "type": "long_text"},
    {"name": "last_error", "type": "long_text"},
    {"name": "last_attempt_at", "type": "text"},
]

# Fields that are REQUIRED — everything else in the live table is legacy junk
REQUIRED_FIELD_NAMES = {"Name"} | {f["name"] for f in POSTS_FIELDS}

# Legacy fields that should be deleted (from old V1/V2 schema)
LEGACY_FIELDS = {
    "ForntUpscaler", "CloserUpscaler", "BoradUpscaler", "PalluDesignUpscaler",
    "FRONT FULL VIEW", "FRONT Video URL", "fabric_analysis", "fabric_analysis_id",
    "Model_Image_URL", "Product_Image_URL", "Angle_Front_URL", "Angle_Close_URL",
    "Angle_Pallu_URL", "Angle_Border_URL", "Video_URL", "Error_Log",
    "Active_Prompt_ID", "Active",
    "mannequin_front_raw", "mannequin_closer_raw",
    "mannequin_border_raw", "mannequin_pallu_raw",
    "hero_image_raw", "hero_image_final", "angle_front_final",
}


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


def api(method: str, path: str, token: str | None = None, **kwargs) -> dict | list:
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


def get_auth_token() -> str:
    """Authenticate with Baserow and return JWT token."""
    print("\n📝 Authenticating...")
    user_resp = api("POST", "/user/", json={
        "name": "Janardan Admin",
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "authenticate": True,
        "language": "en",
    })
    if not user_resp:
        user_resp = api("POST", "/user/token-auth/", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
        })

    token = user_resp.get("access_token") or user_resp.get("token")
    if not token:
        print("❌ Failed to get auth token. Response:", user_resp)
        sys.exit(1)
    print(f"  ✅ Authenticated")
    return token


# ── Fresh Setup ───────────────────────────────────────────────────────

def cmd_fresh():
    """Create everything from scratch."""
    print("=" * 60)
    print("  Janardan — Fresh Baserow Setup")
    print("=" * 60)

    wait_for_baserow(BASEROW_URL)
    token = get_auth_token()

    # Create workspace
    print("\n📁 Creating workspace...")
    workspace = api("POST", "/workspaces/", token=token, json={
        "name": "Janardan Saree Automation",
    })
    workspace_id = workspace.get("id")
    if not workspace_id:
        workspaces = api("GET", "/workspaces/", token=token)
        if isinstance(workspaces, list) and workspaces:
            workspace_id = workspaces[0]["id"]
            print(f"  ↳ Using existing workspace (ID: {workspace_id})")
        else:
            print("❌ Failed to create workspace")
            sys.exit(1)
    else:
        print(f"  ✅ Workspace created (ID: {workspace_id})")

    # Create database
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

    # Create Posts table (only table needed)
    print("\n📊 Creating table: Posts...")
    table = api("POST", f"/database/tables/database/{db_id}/", token=token, json={
        "name": "Posts",
    })
    table_id = table.get("id")
    if not table_id:
        print("❌ Failed to create Posts table")
        sys.exit(1)
    print(f"  ✅ Posts table created (ID: {table_id})")

    # Add fields
    for field_def in POSTS_FIELDS:
        resp = api("POST", f"/database/fields/table/{table_id}/", token=token, json=field_def)
        if resp.get("id"):
            print(f"    + {field_def['name']} ({field_def['type']})")
        else:
            print(f"    ⚠ {field_def['name']} — creation failed")

    # Create database API token
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
        print(f"  ✅ Token created: {db_token}")

    _print_env_values(db_token, table_id)


# ── Migration ─────────────────────────────────────────────────────────

def cmd_migrate(table_id: int):
    """Update existing table: add missing fields, delete legacy fields."""
    print("=" * 60)
    print(f"  Janardan — Migrate Posts Table (ID: {table_id})")
    print("=" * 60)

    wait_for_baserow(BASEROW_URL)
    token = get_auth_token()

    # Fetch current fields
    print(f"\n📋 Fetching current fields for table {table_id}...")
    fields = api("GET", f"/database/fields/table/{table_id}/", token=token)
    if not fields:
        print("❌ Failed to fetch fields")
        sys.exit(1)

    current_fields = {f["name"]: f for f in fields}
    print(f"  Found {len(current_fields)} existing fields")

    # ── 1. Add missing fields ─────────────────────────────
    print("\n➕ Adding missing fields...")
    added = 0
    for field_def in POSTS_FIELDS:
        if field_def["name"] not in current_fields:
            # For single_select, don't include select_options in creation — add them separately
            create_def = {k: v for k, v in field_def.items() if k != "select_options"}
            resp = api("POST", f"/database/fields/table/{table_id}/", token=token, json=create_def)
            if resp.get("id"):
                print(f"    + {field_def['name']} ({field_def['type']})")
                added += 1

                # Add select options if needed
                if "select_options" in field_def:
                    _sync_select_options(resp["id"], field_def["select_options"], token)
            else:
                print(f"    ⚠ Failed: {field_def['name']}")
        else:
            # Check if Status needs new options
            if field_def["name"] == "Status" and "select_options" in field_def:
                status_field = current_fields["Status"]
                _sync_select_options(status_field["id"], field_def["select_options"], token)

    if added == 0:
        print("    (all fields already exist)")

    # ── 2. Delete legacy fields ───────────────────────────
    legacy_in_table = [
        f for name, f in current_fields.items()
        if name in LEGACY_FIELDS
    ]

    if legacy_in_table:
        print(f"\n🗑️  Found {len(legacy_in_table)} legacy fields to delete:")
        for f in legacy_in_table:
            print(f"    - {f['name']} (id={f['id']}, type={f['type']})")

        answer = input("\n  Delete these legacy fields? [y/N]: ").strip().lower()
        if answer == "y":
            for f in legacy_in_table:
                resp = requests.delete(
                    f"{BASEROW_URL}/api/database/fields/{f['id']}/",
                    headers={"Authorization": f"JWT {token}"},
                )
                if resp.status_code < 300:
                    print(f"    ✅ Deleted: {f['name']}")
                else:
                    print(f"    ❌ Failed to delete {f['name']}: {resp.status_code}")
        else:
            print("    Skipped deletion.")
    else:
        print("\n🗑️  No legacy fields found")

    # ── 3. Summary ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  ✅ MIGRATION COMPLETE!")
    print("=" * 60)

    # Re-fetch for verification
    fields = api("GET", f"/database/fields/table/{table_id}/", token=token)
    print(f"\n  Final field count: {len(fields)}")
    print("  Required fields present:")
    final_names = {f["name"] for f in fields}
    for req in sorted(REQUIRED_FIELD_NAMES):
        status = "✅" if req in final_names else "❌"
        print(f"    {status} {req}")


def _sync_select_options(field_id: int, desired_options: list[dict], token: str):
    """Ensure all desired options exist in a single_select field."""
    # Fetch current field to get existing options
    field = api("GET", f"/database/fields/{field_id}/", token=token)
    existing_values = {opt["value"] for opt in field.get("select_options", [])}

    missing = [opt for opt in desired_options if opt["value"] not in existing_values]
    if missing:
        # Merge existing + missing options
        all_opts = field.get("select_options", []) + missing
        api("PATCH", f"/database/fields/{field_id}/", token=token, json={
            "select_options": all_opts,
        })
        print(f"    + Added {len(missing)} status options: {[o['value'] for o in missing]}")


# ── Utilities ─────────────────────────────────────────────────────────

def _print_env_values(db_token: str, table_id: int):
    """Print .env values for the user."""
    print("\n" + "=" * 60)
    print("  ✅ SETUP COMPLETE!")
    print("=" * 60)
    print()
    print("Copy these values into your .env file:")
    print()
    print(f"BASEROW_URL={BASEROW_URL}")
    print(f"BASEROW_TOKEN={db_token}")
    print(f"BASEROW_POSTS_TABLE_ID={table_id}")
    print()
    print(f"Baserow Web UI: {BASEROW_URL}")
    print(f"Login: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")


def _update_env_file(db_token: str, table_id: int) -> None:
    """Update the .env file with Baserow values."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        print(f"  ❌ .env not found at {env_path}")
        return

    content = env_path.read_text()
    replacements = {
        "BASEROW_URL": BASEROW_URL,
        "BASEROW_TOKEN": db_token,
        "BASEROW_POSTS_TABLE_ID": str(table_id),
    }
    for key, value in replacements.items():
        pattern = rf"^{key}=.*$"
        replacement = f"{key}={value}"
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

    env_path.write_text(content)
    print(f"  ✅ .env updated at {env_path}")


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Janardan Baserow Setup")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("fresh", help="Create everything from scratch")

    migrate_p = sub.add_parser("migrate", help="Update existing table schema")
    migrate_p.add_argument("--table-id", type=int, required=True,
                           help="Posts table ID to migrate")

    args = parser.parse_args()
    if args.command == "fresh":
        cmd_fresh()
    elif args.command == "migrate":
        cmd_migrate(args.table_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
