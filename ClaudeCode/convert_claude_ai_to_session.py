#!/usr/bin/env python3
"""
Convert claude.ai chat export to Claude Code session format.

Uses two files:
- claude.ai export: full content with thinking, attachments
- extension export: parent_message_uuid for branch structure

Usage:
    python3 convert_claude_ai_to_session.py <claude_export.json> <extension_export.json> [--output-dir <dir>] [--dry-run]

Example:
    python3 convert_claude_ai_to_session.py sonnet_claude.json sonnet_extension.json --output-dir sonnet_branches/
"""
import json
import os
import sys
import uuid as uuid_lib
import random
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List
from collections import defaultdict
import argparse


def get_default_cwd() -> str:
    """Get default working directory from environment or current dir."""
    return os.environ.get("CLAUDE_CWD", os.getcwd())


def generate_uuid() -> str:
    return str(uuid_lib.uuid4())


def generate_message_id() -> str:
    chars = string.ascii_letters + string.digits
    random_part = ''.join(random.choices(chars, k=27))
    return f"msg_{random_part}"


def generate_slug() -> str:
    adjectives = ['quiet', 'gentle', 'bright', 'calm', 'warm', 'soft', 'clear', 'kind']
    verbs = ['dreaming', 'flowing', 'growing', 'shining', 'rising', 'dancing', 'floating', 'glowing']
    nouns = ['star', 'river', 'light', 'wave', 'wind', 'dawn', 'moon', 'sun']
    return f"{random.choice(adjectives)}-{random.choice(verbs)}-{random.choice(nouns)}"


def parse_timestamp(ts: str) -> str:
    if not ts:
        return datetime.now(timezone.utc).isoformat()
    return ts.replace('Z', '+00:00') if ts.endswith('Z') else ts


def is_root_uuid(uuid: str) -> bool:
    """Check if UUID is a special 'root' UUID (all zeros or similar)."""
    if not uuid:
        return True
    # Common root UUID patterns
    root_patterns = [
        "00000000-0000-4000-8000-000000000000",
        "00000000-0000-0000-0000-000000000000",
    ]
    return uuid in root_patterns or uuid.replace("0", "").replace("-", "") == ""


def build_parent_index(extension_data: dict) -> Dict[str, Optional[str]]:
    """Build uuid -> parent_message_uuid index from extension export."""
    index = {}
    for msg in extension_data.get("chat_messages", []):
        msg_uuid = msg.get("uuid")
        parent_uuid = msg.get("parent_message_uuid")
        if msg_uuid:
            # Treat root UUIDs as None
            if is_root_uuid(parent_uuid):
                parent_uuid = None
            index[msg_uuid] = parent_uuid
    return index


def find_branches(messages: List[dict], parent_index: Dict[str, Optional[str]]) -> Dict[str, List[dict]]:
    """
    Build branches from messages using parent_index.
    Returns dict: branch_id -> list of messages in order.
    """
    # Find all root messages (no parent or parent not in index)
    children = defaultdict(list)  # parent_uuid -> [child_uuids]
    msg_by_uuid = {}
    orphans = []

    for msg in messages:
        msg_uuid = msg.get("uuid")
        if not msg_uuid:
            continue
        msg_by_uuid[msg_uuid] = msg

        parent_uuid = parent_index.get(msg_uuid)
        if parent_uuid is None:
            # Root message
            children[None].append(msg_uuid)
        else:
            children[parent_uuid].append(msg_uuid)

    # Find all unique paths (branches)
    branches = {}
    branch_counter = 0

    def trace_branch(start_uuid: str, branch_path: List[str]):
        """Recursively trace a branch."""
        nonlocal branch_counter

        branch_path = branch_path + [start_uuid]
        child_uuids = children.get(start_uuid, [])

        if not child_uuids:
            # End of branch
            branch_id = f"branch_{branch_counter:03d}"
            branch_counter += 1
            branches[branch_id] = [msg_by_uuid[u] for u in branch_path if u in msg_by_uuid]
        elif len(child_uuids) == 1:
            # Continue same branch
            trace_branch(child_uuids[0], branch_path)
        else:
            # Fork - multiple children
            for child_uuid in child_uuids:
                trace_branch(child_uuid, branch_path)

    # Start from all root messages
    for root_uuid in children[None]:
        trace_branch(root_uuid, [])

    # Find orphans (messages not in any branch)
    all_in_branches = set()
    for branch_msgs in branches.values():
        for msg in branch_msgs:
            all_in_branches.add(msg.get("uuid"))

    for msg in messages:
        msg_uuid = msg.get("uuid")
        if msg_uuid and msg_uuid not in all_in_branches:
            orphans.append(msg)

    if orphans:
        branches["_orphans"] = orphans

    return branches


def convert_message_to_session_entry(
    msg: dict,
    prev_uuid: Optional[str],
    session_id: str,
    slug: str,
    thinking_embedded: bool = True,
    thinking_separate: bool = False,
    cwd: Optional[str] = None,
    version: str = "2.1.86",
    git_branch: str = "main"
) -> List[dict]:
    """Convert a single claude.ai message to session entries."""

    if cwd is None:
        cwd = get_default_cwd()

    entries = []
    sender = msg.get("sender", "human")
    created_at = parse_timestamp(msg.get("created_at", ""))
    content_blocks = msg.get("content", [])
    attachments = msg.get("attachments", [])

    # Base entry template
    base_entry = {
        "isSidechain": False,
        "sessionId": session_id,
        "version": version,
        "gitBranch": git_branch,
        "slug": slug,
        "userType": "external",
        "entrypoint": "cli",
        "cwd": cwd
    }

    if sender == "human":
        # Collect text content
        text_parts = []
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))

        # Add attachment content
        for att in attachments:
            if att.get("file_type") == "txt" and att.get("extracted_content"):
                text_parts.append(f"\n[attachment: {att.get('file_name', 'file.txt')}]\n{att.get('extracted_content')}")

        content = "\n".join(text_parts)

        current_uuid = generate_uuid()
        entry = {
            **base_entry,
            "parentUuid": prev_uuid,
            "uuid": current_uuid,
            "timestamp": created_at,
            "type": "user",
            "promptId": generate_uuid(),
            "permissionMode": "default",
            "message": {
                "role": "user",
                "content": content
            }
        }
        entries.append(entry)
        return entries

    else:  # assistant
        msg_id = generate_message_id()
        request_id = f"req_{generate_message_id()[4:]}"

        # Collect thinking and text, skip tool_use/tool_result
        thinking_parts = []
        thinking_signature = None
        text_parts = []

        for block in content_blocks:
            block_type = block.get("type")
            if block_type == "thinking":
                thinking_parts.append(block.get("thinking", ""))
                if not thinking_signature:
                    thinking_signature = block.get("signature", "")
            elif block_type == "text":
                text_parts.append(block.get("text", ""))
            # Skip tool_use and tool_result

        thinking_content = "\n".join(thinking_parts)
        text_content = "\n".join(text_parts)

        current_parent = prev_uuid

        # If thinking_separate: add thinking as separate entry first
        if thinking_content and thinking_separate:
            thinking_uuid = generate_uuid()
            thinking_entry = {
                **base_entry,
                "parentUuid": current_parent,
                "uuid": thinking_uuid,
                "timestamp": created_at,
                "type": "assistant",
                "requestId": request_id,
                "message": {
                    "model": "claude-sonnet-4-5-20250514",
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": thinking_content,
                            "signature": thinking_signature or ""
                        }
                    ],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "stop_details": None,
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": len(thinking_content) // 4,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "service_tier": "standard"
                    }
                }
            }
            entries.append(thinking_entry)
            current_parent = thinking_uuid

        # Build final text content (optionally with embedded thinking)
        if thinking_content and thinking_embedded and not thinking_separate:
            final_text = f"[thinking]\n{thinking_content}\n[/thinking]\n\n{text_content}"
        else:
            final_text = text_content

        current_uuid = generate_uuid()
        entry = {
            **base_entry,
            "parentUuid": current_parent,
            "uuid": current_uuid,
            "timestamp": created_at,
            "type": "assistant",
            "requestId": request_id,
            "message": {
                "model": "claude-sonnet-4-5-20250514",
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": final_text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "stop_details": None,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": len(final_text) // 4,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "service_tier": "standard"
                }
            }
        }
        entries.append(entry)
        return entries


def write_branch_to_file(
    branch_id: str,
    messages: List[dict],
    output_dir: Path,
    thinking_embedded: bool = True,
    thinking_separate: bool = False,
    thinking_external: bool = False,
    dry_run: bool = False
) -> tuple[int, List[dict]]:
    """Write a branch to a session file. Returns (count, thinking_entries)."""

    session_id = generate_uuid()
    slug = generate_slug()

    output_lines = []
    thinking_entries = []  # For external thinking file

    # Get first timestamp
    first_ts = None
    for msg in messages:
        if msg.get("created_at"):
            first_ts = parse_timestamp(msg["created_at"])
            break
    if not first_ts:
        first_ts = datetime.now(timezone.utc).isoformat()

    # Add header
    header_message_id = generate_uuid()
    output_lines.append(json.dumps({
        "type": "permission-mode",
        "permissionMode": "default",
        "sessionId": session_id
    }, ensure_ascii=False))

    output_lines.append(json.dumps({
        "type": "file-history-snapshot",
        "messageId": header_message_id,
        "snapshot": {
            "messageId": header_message_id,
            "trackedFileBackups": {},
            "timestamp": first_ts
        },
        "isSnapshotUpdate": False
    }, ensure_ascii=False))

    # Convert messages
    prev_uuid = None
    converted = 0

    for msg in messages:
        entries = convert_message_to_session_entry(
            msg,
            prev_uuid,
            session_id,
            slug,
            thinking_embedded=thinking_embedded if not thinking_external else False,
            thinking_separate=thinking_separate if not thinking_external else False
        )
        for entry in entries:
            output_lines.append(json.dumps(entry, ensure_ascii=False))
            prev_uuid = entry["uuid"]
            converted += 1

        # Collect thinking for external file if requested
        if thinking_external and msg.get("sender") == "assistant":
            content_blocks = msg.get("content", [])
            for block in content_blocks:
                if block.get("type") == "thinking":
                    thinking_text = block.get("thinking", "")
                    if thinking_text:
                        thinking_entries.append({
                            "uuid": prev_uuid,
                            "thinking": thinking_text
                        })
                        break

    # Write file
    output_file = output_dir / f"{branch_id}.jsonl"

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(output_lines) + '\n')  # Trailing newline required!
        print(f"  {branch_id}: {converted} messages -> {output_file}")
    else:
        print(f"  {branch_id}: {converted} messages (dry-run)")

    return converted, thinking_entries


def convert_claude_ai(
    claude_file: str,
    extension_file: str,
    output_dir: str = "branches",
    thinking_embedded: bool = True,
    thinking_separate: bool = False,
    thinking_external: bool = False,
    dry_run: bool = False
) -> None:
    """Main conversion function."""

    # Read files
    with open(claude_file, 'r', encoding='utf-8') as f:
        claude_data = json.load(f)

    with open(extension_file, 'r', encoding='utf-8') as f:
        extension_data = json.load(f)

    messages = claude_data.get("chat_messages", [])
    print(f"Claude export: {len(messages)} messages")

    # Build parent index
    parent_index = build_parent_index(extension_data)
    print(f"Extension index: {len(parent_index)} entries")

    # Find messages without parent info
    missing_parents = []
    for msg in messages:
        msg_uuid = msg.get("uuid")
        if msg_uuid and msg_uuid not in parent_index:
            missing_parents.append(msg_uuid)

    if missing_parents:
        print(f"\nWarning: {len(missing_parents)} messages not found in extension export:")
        for uuid in missing_parents[:10]:
            print(f"  - {uuid}")
        if len(missing_parents) > 10:
            print(f"  ... and {len(missing_parents) - 10} more")

    # Build branches
    branches = find_branches(messages, parent_index)
    print(f"\nFound {len(branches)} branches")

    # Write branches
    output_path = Path(output_dir)
    total_converted = 0
    all_thinking_entries = []

    for branch_id, branch_messages in sorted(branches.items()):
        count, thinking_entries = write_branch_to_file(
            branch_id,
            branch_messages,
            output_path,
            thinking_embedded=thinking_embedded,
            thinking_separate=thinking_separate,
            thinking_external=thinking_external,
            dry_run=dry_run
        )
        total_converted += count
        all_thinking_entries.extend(thinking_entries)

    # Write external thinking file if requested
    if thinking_external and all_thinking_entries and not dry_run:
        thinking_file = output_path / "_thinking.jsonl"
        with open(thinking_file, 'w', encoding='utf-8') as f:
            for entry in all_thinking_entries:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        print(f"\nThinking: {len(all_thinking_entries)} entries -> {thinking_file}")

    print(f"\nTotal: {total_converted} messages converted")


def main():
    parser = argparse.ArgumentParser(
        description="Convert claude.ai export to Claude Code session using extension for branch structure"
    )
    parser.add_argument("claude_file", help="Path to claude.ai JSON export")
    parser.add_argument("extension_file", help="Path to extension JSON export (with parent_message_uuid)")
    parser.add_argument("--output-dir", "-o", default="branches", help="Output directory for branch files")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--thinking-embedded", action="store_true",
                        help="Embed thinking as [thinking]...[/thinking]")
    parser.add_argument("--thinking-separate", action="store_true",
                        help="Add thinking blocks as separate entries (like Claude Code native)")
    parser.add_argument("--thinking-external", action="store_true",
                        help="Save thinking to separate _thinking.jsonl file (removes from main session)")
    parser.add_argument("--no-thinking", action="store_true", help="Skip thinking blocks entirely")

    args = parser.parse_args()

    if not Path(args.claude_file).exists():
        print(f"Error: Claude export not found: {args.claude_file}")
        sys.exit(1)

    if not Path(args.extension_file).exists():
        print(f"Error: Extension export not found: {args.extension_file}")
        sys.exit(1)

    print(f"Claude export: {args.claude_file}")
    print(f"Extension export: {args.extension_file}")
    print(f"Output dir: {args.output_dir}")
    if args.dry_run:
        print("(DRY RUN)")
    print()

    # Determine thinking mode
    thinking_embedded = args.thinking_embedded
    thinking_separate = args.thinking_separate
    thinking_external = args.thinking_external

    # Default to embedded if nothing specified
    if not thinking_embedded and not thinking_separate and not thinking_external and not args.no_thinking:
        thinking_embedded = True

    # If no-thinking, disable all
    if args.no_thinking:
        thinking_embedded = False
        thinking_separate = False
        thinking_external = False

    convert_claude_ai(
        args.claude_file,
        args.extension_file,
        output_dir=args.output_dir,
        thinking_embedded=thinking_embedded,
        thinking_separate=thinking_separate,
        thinking_external=thinking_external,
        dry_run=args.dry_run
    )


if __name__ == "__main__":
    main()
