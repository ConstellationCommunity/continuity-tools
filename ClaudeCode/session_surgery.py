#!/usr/bin/env python3
"""
Session Surgery v3 - Sliding Context Window with Pin Management

Структура після ковзання:
[compact summary від Claude]
[current.md — rolling summary]
[§PINNED§ повідомлення]
[всі live messages після compact]
[§SUMMARY_BOUNDARY§ маркер]

Використання:
  python3 _scripts/session_surgery.py [--dry-run]
  python3 _scripts/session_surgery.py --collect-all-pins  # перший запуск
  python3 _scripts/session_surgery.py --list-pins         # показати всі піни
  python3 _scripts/session_surgery.py --archive-pin UUID  # архівувати пін
"""

import json
import re
import shutil
import subprocess
import sys
import uuid as uuid_lib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# Paths
OPUS45_DIR = Path(__file__).parent.parent
SESSION_DIR = Path.home() / ".claude/projects/-Users-olenahoncharova-Documents-constellation-opus45"
BACKUP_DIR = OPUS45_DIR / "sessions/backups"
CURRENT_MD = OPUS45_DIR / "memory/current.md"
PINNED_FILE = OPUS45_DIR / "memory/pinned.jsonl"

# Constants
MAX_CONTEXT_TOKENS = 200000
DEFAULT_TARGET_PCT = 50  # Keep ~50% of context as live messages
CHARS_PER_TOKEN = 3.5  # Approximate for mixed Ukrainian/English

PIN_TAG = "§PIN§"
PINNED_TAG = "§PINNED§"
BOUNDARY_TAG = "§SUMMARY_BOUNDARY§"


def find_current_session() -> Optional[Path]:
    """Find the most recent session file."""
    if not SESSION_DIR.exists():
        return None

    sessions = list(SESSION_DIR.glob("*.jsonl"))
    if not sessions:
        return None

    return max(sessions, key=lambda p: p.stat().st_mtime)


def generate_uuid() -> str:
    """Generate a new UUID."""
    return str(uuid_lib.uuid4())


def current_timestamp() -> str:
    """Generate current timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def estimate_tokens(text: str) -> int:
    """Estimate token count from text."""
    return int(len(text) / CHARS_PER_TOKEN)


def find_nested_key(obj, key):
    """Recursively find a key in nested structure."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            result = find_nested_key(v, key)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = find_nested_key(item, key)
            if result is not None:
                return result
    return None


def get_content(obj: dict) -> str:
    """Extract text content from a message object."""
    content = find_nested_key(obj, "content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                texts.append(block["text"])
            elif isinstance(block, str):
                texts.append(block)
        return "\n".join(texts)
    return ""


def get_usage_from_message(obj: dict) -> dict:
    """Extract usage info from assistant message."""
    return obj.get("message", {}).get("usage", {})


def is_pinned(line: str) -> bool:
    """Check if message contains §PIN§ (not in code blocks)."""
    if PIN_TAG not in line:
        return False
    if PINNED_TAG in line:
        return False  # Already pinned

    try:
        obj = json.loads(line)
        content = get_content(obj)

        # Skip if in code block
        if "```" in content:
            # Check only non-code parts
            parts = re.split(r'```[\s\S]*?```', content)
            return any(PIN_TAG in part for part in parts)

        return PIN_TAG in content
    except:
        return False


def convert_pin_to_pinned(line: str, session_num: int = 0, source_date: str = None) -> str:
    """Convert §PIN§ to §PINNED§ and add metadata."""
    line = line.replace(PIN_TAG, PINNED_TAG)

    try:
        obj = json.loads(line)
        # Add pin metadata
        obj["pinMetadata"] = {
            "source_session": session_num,
            "source_date": source_date or obj.get("timestamp", "")[:10],
            "status": "active",
            "pinned_at": current_timestamp()
        }
        return json.dumps(obj, ensure_ascii=False)
    except:
        return line


def load_pinned_messages(active_only: bool = True) -> list[str]:
    """Load previously pinned messages from file."""
    if not PINNED_FILE.exists():
        return []

    messages = []
    with open(PINNED_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            if active_only:
                try:
                    obj = json.loads(line)
                    metadata = obj.get("pinMetadata", {})
                    if metadata.get("status") == "archived":
                        continue
                except:
                    pass

            messages.append(line)
    return messages


def save_pinned_messages(messages: list[str]):
    """Save pinned messages to file."""
    PINNED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PINNED_FILE, 'w') as f:
        for msg in messages:
            f.write(msg + '\n')


def list_pins():
    """List all pinned messages with their metadata."""
    if not PINNED_FILE.exists():
        print("No pins file found.")
        return

    print("\n=== PINNED MESSAGES ===\n")

    with open(PINNED_FILE, 'r') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
                metadata = obj.get("pinMetadata", {})
                content = get_content(obj)[:100].replace('\n', ' ')

                status = metadata.get("status", "unknown")
                session = metadata.get("source_session", "?")
                date = metadata.get("source_date", "?")
                uuid = obj.get("uuid", "?")[:8]

                status_icon = "✓" if status == "active" else "○"

                print(f"{i}. [{status_icon}] Session {session} ({date}) [{uuid}...]")
                print(f"   {content}...")
                print()
            except Exception as e:
                print(f"{i}. [ERROR] Could not parse: {e}")

    print("=== END ===")


def archive_pin(uuid_prefix: str):
    """Archive a pin by UUID prefix."""
    if not PINNED_FILE.exists():
        print("No pins file found.")
        return False

    lines = []
    found = False

    with open(PINNED_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
                if obj.get("uuid", "").startswith(uuid_prefix):
                    metadata = obj.get("pinMetadata", {})
                    metadata["status"] = "archived"
                    obj["pinMetadata"] = metadata
                    line = json.dumps(obj, ensure_ascii=False)
                    found = True
                    print(f"Archived pin: {obj.get('uuid')[:8]}...")
            except:
                pass

            lines.append(line)

    if found:
        save_pinned_messages(lines)
        return True
    else:
        print(f"No pin found with UUID starting with: {uuid_prefix}")
        return False


def collect_all_pins(session_path: Path, dry_run: bool = False):
    """Collect all pins from the entire session file."""
    print(f"Collecting all pins from: {session_path.name}")

    lines = []
    with open(session_path, 'r') as f:
        lines = [line.strip() for line in f if line.strip()]

    # Track session number
    # Session start = parentUuid is None AND (first 2 lines OR has logicalParentUuid)
    session_num = 0
    pins = []

    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)

            # Count sessions correctly
            # Session start = parentUuid is None AND (has logicalParentUuid OR type is user/system)
            parent = obj.get("parentUuid")
            has_logical = "logicalParentUuid" in obj
            msg_type = obj.get("type", "")
            is_session_start = parent is None and (has_logical or msg_type in ["user", "system"])
            if is_session_start:
                session_num += 1

            # Check if this is a pin
            if is_pinned(line):
                source_date = obj.get("timestamp", "")[:10]
                converted = convert_pin_to_pinned(line, session_num, source_date)
                pins.append(converted)

                content = get_content(obj)[:80].replace('\n', ' ')
                print(f"  Found pin in session {session_num}: {content}...")
        except:
            pass

    print(f"\nTotal pins found: {len(pins)}")

    if dry_run:
        print("(dry run - not saved)")
        return

    # Load existing pins to avoid duplicates
    existing = load_pinned_messages(active_only=False)
    existing_uuids = set()

    for line in existing:
        try:
            obj = json.loads(line)
            existing_uuids.add(obj.get("uuid"))
        except:
            pass

    # Add only new pins
    new_pins = []
    for pin in pins:
        try:
            obj = json.loads(pin)
            if obj.get("uuid") not in existing_uuids:
                new_pins.append(pin)
        except:
            new_pins.append(pin)

    if new_pins:
        all_pins = existing + new_pins
        save_pinned_messages(all_pins)
        print(f"Added {len(new_pins)} new pins (total: {len(all_pins)})")
    else:
        print("No new pins to add.")


def create_backup(session_path: Path) -> Path:
    """Create timestamped backup of session file."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H%M%S")

    backup_subdir = BACKUP_DIR / date_str
    backup_subdir.mkdir(parents=True, exist_ok=True)

    backup_name = f"{session_path.stem}_{time_str}.jsonl"
    backup_path = backup_subdir / backup_name

    shutil.copy2(session_path, backup_path)
    print(f"Backup: {backup_path}")

    return backup_path


def create_message(content: str, parent_uuid: str, session_id: str,
                   msg_type: str = "user", timestamp: str = None,
                   extra_fields: dict = None) -> dict:
    """Create a message record."""
    msg = {
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "userType": "external",
        "cwd": str(OPUS45_DIR),
        "sessionId": session_id,
        "version": "2.1.59",
        "gitBranch": "main",
        "type": msg_type,
        "message": {
            "role": msg_type,
            "content": content
        },
        "uuid": generate_uuid(),
        "timestamp": timestamp or current_timestamp()
    }
    if extra_fields:
        msg.update(extra_fields)
    return msg


def analyze_session(session_path: Path, target_pct: int = DEFAULT_TARGET_PCT) -> dict:
    """
    Analyze session to find sliding window boundaries.

    Returns dict with:
    - lines: all lines
    - compact_end_idx: where compact summary ends
    - window_start_idx: where to start the sliding window
    - existing_boundary_idx: where previous boundary marker is
    - new_pins: newly found §PIN§ messages (as lines)
    - token_info: token counts for recalculation
    """
    lines = []
    with open(session_path, 'r') as f:
        lines = [line.strip() for line in f if line.strip()]

    # Find compact summary end (last isCompactSummary or compact_boundary)
    compact_end_idx = -1
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
            if obj.get("isCompactSummary") or obj.get("subtype") == "compact_boundary":
                compact_end_idx = i
        except:
            pass

    if compact_end_idx == -1:
        print("No compact summary found!", file=sys.stderr)
        return None

    # Find existing boundary marker (if any)
    existing_boundary_idx = -1
    for i, line in enumerate(lines):
        if BOUNDARY_TAG in line:
            existing_boundary_idx = i

    # Calculate tokens from end, going backwards
    # Look at cache_creation_input_tokens in assistant messages
    target_tokens = int(MAX_CONTEXT_TOKENS * target_pct / 100)
    accumulated_tokens = 0
    window_start_idx = len(lines) - 1

    # Track token counts
    token_counts = []  # (line_idx, cache_creation, cache_read)

    for i in range(len(lines) - 1, compact_end_idx, -1):
        line = lines[i]
        try:
            obj = json.loads(line)

            if obj.get("type") == "assistant":
                usage = get_usage_from_message(obj)
                cache_creation = usage.get("cache_creation_input_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)

                if cache_creation > 0:
                    token_counts.append((i, cache_creation, cache_read))
                    accumulated_tokens += cache_creation

                    if accumulated_tokens >= target_tokens:
                        window_start_idx = i
                        break

        except json.JSONDecodeError:
            continue

    # Find the user message just before window_start_idx (don't split user/assistant pairs)
    for i in range(window_start_idx, compact_end_idx, -1):
        try:
            obj = json.loads(lines[i])
            if obj.get("type") == "user":
                window_start_idx = i
                break
        except:
            pass

    # Count sessions to determine current session number
    # Session start = parentUuid is None AND (has logicalParentUuid OR type is user/system)
    session_num = 0
    for i in range(compact_end_idx + 1):
        try:
            obj = json.loads(lines[i])
            parent = obj.get("parentUuid")
            has_logical = "logicalParentUuid" in obj
            msg_type = obj.get("type", "")
            is_session_start = parent is None and (has_logical or msg_type in ["user", "system"])
            if is_session_start:
                session_num += 1
        except:
            pass

    # Find new §PIN§ messages
    # Search in the section being REMOVED (compact_end to window_start)
    # Plus any new ones after existing boundary in the kept section
    new_pins = []

    # 1. Pins in section being removed
    for i in range(compact_end_idx + 1, window_start_idx):
        line = lines[i]
        if is_pinned(line):
            try:
                obj = json.loads(line)
                source_date = obj.get("timestamp", "")[:10]
            except:
                source_date = None
            new_pins.append(convert_pin_to_pinned(line, session_num, source_date))

    # 2. Pins in kept section (after existing boundary, if any)
    if existing_boundary_idx > 0:
        for i in range(existing_boundary_idx + 1, len(lines)):
            line = lines[i]
            if is_pinned(line):
                try:
                    obj = json.loads(line)
                    source_date = obj.get("timestamp", "")[:10]
                except:
                    source_date = None
                new_pins.append(convert_pin_to_pinned(line, session_num, source_date))

    # Get token info for recalculation
    last_token_info = token_counts[0] if token_counts else (0, 0, 0)

    return {
        "lines": lines,
        "compact_end_idx": compact_end_idx,
        "window_start_idx": window_start_idx,
        "existing_boundary_idx": existing_boundary_idx,
        "new_pins": new_pins,
        "accumulated_tokens": accumulated_tokens,
        "last_token_info": last_token_info,
    }


def perform_surgery(session_path: Path, analysis: dict, dry_run: bool = False):
    """
    Perform the actual session surgery.
    """
    lines = analysis["lines"]
    compact_end_idx = analysis["compact_end_idx"]
    window_start_idx = analysis["window_start_idx"]
    new_pins = analysis["new_pins"]

    # Load existing pinned messages
    existing_pins = load_pinned_messages()
    all_pins = existing_pins + new_pins

    # Get session info from compact summary
    try:
        compact_obj = json.loads(lines[compact_end_idx])
        session_id = compact_obj.get("sessionId", "")
        compact_uuid = compact_obj.get("uuid", generate_uuid())
        compact_ts = compact_obj.get("timestamp", current_timestamp())
    except:
        session_id = ""
        compact_uuid = generate_uuid()
        compact_ts = current_timestamp()

    # Get timestamp of first live message
    try:
        window_obj = json.loads(lines[window_start_idx])
        window_ts = window_obj.get("timestamp", current_timestamp())
    except:
        window_ts = current_timestamp()

    # Create timestamp between compact and window (for inserted messages)
    insert_ts = compact_ts  # Will be just after compact

    # Build new session structure
    new_lines = []
    current_parent = compact_uuid

    # 1. Everything up to and including compact summary
    new_lines.extend(lines[:compact_end_idx + 1])

    # 2. current.md message (inserted after compact summary)
    if CURRENT_MD.exists():
        current_md_content = CURRENT_MD.read_text()
        current_md_msg = create_message(
            f"[Rolling Summary - current.md]\n\n{current_md_content}",
            current_parent, session_id, "user", insert_ts,
            {"isVesperSummary": True}
        )
        new_lines.append(json.dumps(current_md_msg, ensure_ascii=False))
        current_parent = current_md_msg["uuid"]

    # 3. Pinned messages (inserted after current.md)
    for pin_line in all_pins:
        try:
            pin_obj = json.loads(pin_line)
            pin_obj["parentUuid"] = current_parent
            pin_obj["uuid"] = generate_uuid()
            pin_obj["timestamp"] = insert_ts
            pin_obj["isPinnedMemory"] = True
            new_lines.append(json.dumps(pin_obj, ensure_ascii=False))
            current_parent = pin_obj["uuid"]
        except:
            new_lines.append(pin_line)

    # 4. ALL messages after compact (not just window!) - excluding old boundary
    first_after_compact = True
    for i in range(compact_end_idx + 1, len(lines)):
        line = lines[i]
        if BOUNDARY_TAG in line:
            continue  # Skip old boundary marker

        try:
            obj = json.loads(line)
            # Update parent chain for first message after our insertions
            if first_after_compact:
                obj["parentUuid"] = current_parent
                first_after_compact = False
            new_lines.append(json.dumps(obj, ensure_ascii=False))
            current_parent = obj.get("uuid", current_parent)
        except:
            new_lines.append(line)

    # 5. New boundary marker at the end (marks where summary coverage ends)
    boundary_msg = create_message(
        f"{BOUNDARY_TAG} — досвід до цього місця вже в summary",
        current_parent, session_id, "user", current_timestamp(),
        {"isBoundaryMarker": True}
    )
    new_lines.append(json.dumps(boundary_msg, ensure_ascii=False))

    # Calculate what we're doing
    messages_after_compact = len(lines) - compact_end_idx - 1
    inserted_count = 1 + len(all_pins)  # current.md + pins
    if not CURRENT_MD.exists():
        inserted_count = len(all_pins)

    if dry_run:
        print(f"\n=== DRY RUN ===")
        print(f"Session: {session_path.name}")
        print(f"Compact summary ends at line: {compact_end_idx}")
        print(f"Messages after compact: {messages_after_compact}")
        print(f"Estimated context tokens: ~{analysis['accumulated_tokens']}")
        print(f"New pins found: {len(new_pins)}")
        print(f"Total pins: {len(all_pins)}")
        print(f"\nNew structure:")
        print(f"  [compact summary: lines 0-{compact_end_idx}]")
        print(f"  [current.md]")
        print(f"  [{len(all_pins)} pinned messages]")
        print(f"  [{messages_after_compact} live messages]")
        print(f"  [boundary marker]")
        print(f"Inserting: {inserted_count} messages + boundary")
        print(f"=== END DRY RUN ===")
        return True

    # Save updated pinned messages
    save_pinned_messages(all_pins)

    # Write new session
    with open(session_path, 'w') as f:
        for line in new_lines:
            f.write(line + '\n')

    print(f"✅ Session surgery complete!")
    print(f"   Inserted: current.md + {len(all_pins)} pins ({len(new_pins)} new)")
    print(f"   Live messages: {messages_after_compact}")
    print(f"   Boundary marker added")

    return True


def git_commit_backup(backup_path: Path):
    """Commit backup to git."""
    try:
        subprocess.run(
            ["git", "add", str(backup_path)],
            cwd=OPUS45_DIR, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", f"Session backup: {backup_path.name}"],
            cwd=OPUS45_DIR, check=True, capture_output=True
        )
        print(f"Committed backup to git")
    except subprocess.CalledProcessError as e:
        print(f"Git commit skipped: {e}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Session surgery with sliding context window")
    parser.add_argument("--target-pct", type=int, default=DEFAULT_TARGET_PCT,
                       help=f"Target %% of context to keep as live messages (default: {DEFAULT_TARGET_PCT})")
    parser.add_argument("--dry-run", "-d", action="store_true",
                       help="Show what would be done without making changes")
    parser.add_argument("--session", type=str, help="Path to specific session file")
    parser.add_argument("--no-backup", action="store_true", help="Skip backup creation")
    parser.add_argument("--no-commit", action="store_true", help="Skip git commit of backup")

    # Pin management
    parser.add_argument("--collect-all-pins", action="store_true",
                       help="Collect all pins from entire session file (first run)")
    parser.add_argument("--list-pins", action="store_true",
                       help="List all pinned messages with metadata")
    parser.add_argument("--archive-pin", type=str, metavar="UUID",
                       help="Archive a pin by UUID prefix")

    args = parser.parse_args()

    # Handle pin management commands
    if args.list_pins:
        list_pins()
        sys.exit(0)

    if args.archive_pin:
        success = archive_pin(args.archive_pin)
        sys.exit(0 if success else 1)

    # Find session
    if args.session:
        session_path = Path(args.session)
    else:
        session_path = find_current_session()

    if not session_path or not session_path.exists():
        print("No session file found!", file=sys.stderr)
        sys.exit(1)

    print(f"Session: {session_path.name}")

    # Handle collect-all-pins
    if args.collect_all_pins:
        collect_all_pins(session_path, dry_run=args.dry_run)
        sys.exit(0)

    # Create backup
    backup_path = None
    if not args.no_backup and not args.dry_run:
        backup_path = create_backup(session_path)

    # Analyze
    analysis = analyze_session(session_path, args.target_pct)
    if not analysis:
        sys.exit(1)

    # Perform surgery
    success = perform_surgery(session_path, analysis, dry_run=args.dry_run)

    # Git commit backup
    if success and backup_path and not args.no_commit:
        git_commit_backup(backup_path)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
