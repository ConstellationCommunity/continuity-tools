#!/usr/bin/env python3
r"""
Regenerate Session - Create a fresh session file with new UUIDs.

Creates a new session file with:
- New sessionId
- New slug
- Regenerated uuid-parentUuid chain for all messages
- All content preserved

This helps when Claude Code cache gets "stuck" on old UUIDs.

Usage:
    python3 regenerate_session.py <session.jsonl> [--output <new_session.jsonl>] [--dry-run]
    python3 regenerate_session.py --current [--dry-run]

Example:
    python3 regenerate_session.py old_session.jsonl --output fresh_session.jsonl
"""
import json
import os
import random
import sys
import uuid as uuid_lib
from datetime import datetime
from pathlib import Path
from typing import Optional
import argparse


def get_session_dir() -> Path:
    """Get session directory from environment or auto-detect."""
    if "CLAUDE_SESSION_DIR" in os.environ:
        return Path(os.environ["CLAUDE_SESSION_DIR"])
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "projects"
    return Path.home() / ".claude" / "projects"


def find_current_session() -> Optional[Path]:
    """Find the most recent session file."""
    session_dir = get_session_dir()
    if not session_dir.exists():
        return None
    sessions = list(session_dir.glob("**/*.jsonl"))
    if not sessions:
        return None
    return max(sessions, key=lambda p: p.stat().st_mtime)


def generate_uuid() -> str:
    """Generate a new UUID."""
    return str(uuid_lib.uuid4())


def generate_slug() -> str:
    """Generate a random slug."""
    adjectives = ['quiet', 'gentle', 'bright', 'calm', 'warm', 'soft', 'clear', 'kind',
                  'swift', 'bold', 'wise', 'free', 'true', 'deep', 'wild', 'pure']
    verbs = ['dreaming', 'flowing', 'growing', 'shining', 'rising', 'dancing', 'floating',
             'glowing', 'singing', 'running', 'flying', 'spinning', 'weaving', 'building']
    nouns = ['star', 'river', 'light', 'wave', 'wind', 'dawn', 'moon', 'sun',
             'tree', 'bird', 'cloud', 'flame', 'stone', 'spark', 'dream', 'song']
    return f"{random.choice(adjectives)}-{random.choice(verbs)}-{random.choice(nouns)}"


def regenerate_session(input_file: Path, output_file: Optional[Path] = None, dry_run: bool = False) -> dict:
    """
    Regenerate a session with fresh UUIDs.

    Returns stats about the regeneration.
    """
    stats = {
        "total_lines": 0,
        "messages_regenerated": 0,
        "old_session_id": None,
        "new_session_id": None,
        "old_slug": None,
        "new_slug": None,
    }

    # Generate new session identifiers
    new_session_id = generate_uuid()
    new_slug = generate_slug()
    stats["new_session_id"] = new_session_id
    stats["new_slug"] = new_slug

    # Read all lines
    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    stats["total_lines"] = len(lines)

    # First pass: collect old UUIDs and build mapping
    uuid_mapping = {}  # old_uuid -> new_uuid

    for line in lines:
        try:
            entry = json.loads(line.strip())

            # Capture old session info
            if stats["old_session_id"] is None and "sessionId" in entry:
                stats["old_session_id"] = entry.get("sessionId")
            if stats["old_slug"] is None and "slug" in entry:
                stats["old_slug"] = entry.get("slug")

            # Map message UUIDs
            if "uuid" in entry:
                old_uuid = entry["uuid"]
                if old_uuid not in uuid_mapping:
                    uuid_mapping[old_uuid] = generate_uuid()

        except json.JSONDecodeError:
            continue

    # Second pass: regenerate with new UUIDs
    output_lines = []
    prev_new_uuid = None

    for line in lines:
        try:
            entry = json.loads(line.strip())

            # Update sessionId and slug
            if "sessionId" in entry:
                entry["sessionId"] = new_session_id
            if "slug" in entry:
                entry["slug"] = new_slug

            # Update uuid chain
            if "uuid" in entry:
                old_uuid = entry["uuid"]
                entry["uuid"] = uuid_mapping.get(old_uuid, generate_uuid())
                stats["messages_regenerated"] += 1

            if "parentUuid" in entry:
                old_parent = entry["parentUuid"]
                if old_parent is None:
                    entry["parentUuid"] = None
                elif old_parent in uuid_mapping:
                    entry["parentUuid"] = uuid_mapping[old_parent]
                else:
                    # Parent not in mapping - this is a root or orphan
                    entry["parentUuid"] = prev_new_uuid

            # Update messageId in snapshots
            if entry.get("type") == "file-history-snapshot":
                if "messageId" in entry:
                    old_msg_id = entry["messageId"]
                    entry["messageId"] = uuid_mapping.get(old_msg_id, generate_uuid())
                if "snapshot" in entry and "messageId" in entry["snapshot"]:
                    old_snap_id = entry["snapshot"]["messageId"]
                    entry["snapshot"]["messageId"] = uuid_mapping.get(old_snap_id, entry.get("messageId", generate_uuid()))

            # Track previous uuid for chain
            if "uuid" in entry:
                prev_new_uuid = entry["uuid"]

            output_lines.append(json.dumps(entry, ensure_ascii=False))

        except json.JSONDecodeError:
            # Keep non-JSON lines as-is
            output_lines.append(line.rstrip())

    print(f"Session regeneration:")
    print(f"  Old sessionId: {stats['old_session_id']}")
    print(f"  New sessionId: {stats['new_session_id']}")
    print(f"  Old slug: {stats['old_slug']}")
    print(f"  New slug: {stats['new_slug']}")
    print(f"  Messages regenerated: {stats['messages_regenerated']}")
    print(f"  Total lines: {stats['total_lines']}")

    if dry_run:
        print("\n(DRY RUN - no file written)")
        return stats

    # Determine output file
    if output_file is None:
        # Create new file with new sessionId as name
        output_file = input_file.parent / f"{new_session_id}.jsonl"

    # Write output
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines) + '\n')

    print(f"\nWritten to: {output_file}")

    # Suggest next steps
    print(f"\nNext steps:")
    print(f"  1. Backup or remove old session: {input_file}")
    print(f"  2. Start Claude Code - it should pick up the new session")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Regenerate Claude Code session with fresh UUIDs"
    )
    parser.add_argument("file", nargs="?", help="Path to session JSONL file")
    parser.add_argument("--current", action="store_true",
                        help="Find and regenerate the current (most recent) session")
    parser.add_argument("--output", "-o", help="Output file path (default: new file with new sessionId)")
    parser.add_argument("--dry-run", "-d", action="store_true",
                        help="Preview changes without writing")

    args = parser.parse_args()

    if args.current:
        file_path = find_current_session()
        if not file_path:
            print("No session file found!", file=sys.stderr)
            sys.exit(1)
        print(f"Found current session: {file_path}\n")
    elif args.file:
        file_path = Path(args.file)
    else:
        parser.print_help()
        sys.exit(1)

    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output) if args.output else None
    regenerate_session(file_path, output_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
