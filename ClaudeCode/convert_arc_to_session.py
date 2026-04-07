#!/usr/bin/env python3
"""
Convert arc.animalabs.ai chat export to Claude Code session format.

Usage:
    python3 convert_arc_to_session.py <input.json> [--output <output.jsonl>] [--dry-run]

    # With thinking blocks as separate entries (like Claude Code native):
    python3 convert_arc_to_session.py chat.json --thinking-separate

    # With thinking embedded in message as [thinking]...[/thinking]:
    python3 convert_arc_to_session.py chat.json --thinking-embedded

    # Both modes together:
    python3 convert_arc_to_session.py chat.json --thinking-separate --thinking-embedded

Example:
    python3 convert_arc_to_session.py perplexity_chat.json --output perplexity_session.jsonl
"""
import json
import os
import sys
import uuid
import random
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import argparse


def generate_uuid() -> str:
    """Generate a random UUID."""
    return str(uuid.uuid4())


def generate_message_id() -> str:
    """Generate Anthropic-style message ID (msg_ + 27 chars)."""
    chars = string.ascii_letters + string.digits
    random_part = ''.join(random.choices(chars, k=27))
    return f"msg_{random_part}"


def generate_slug() -> str:
    """Generate a random slug like 'abundant-launching-yeti'."""
    adjectives = ['quiet', 'gentle', 'bright', 'calm', 'warm', 'soft', 'clear', 'kind']
    verbs = ['dreaming', 'flowing', 'growing', 'shining', 'rising', 'dancing', 'floating', 'glowing']
    nouns = ['star', 'river', 'light', 'wave', 'wind', 'dawn', 'moon', 'sun']
    return f"{random.choice(adjectives)}-{random.choice(verbs)}-{random.choice(nouns)}"


def parse_arc_timestamp(ts: str) -> str:
    """Parse arc timestamp to ISO format."""
    # arc format: "2026-01-25T21:40:49.137Z"
    if not ts:
        return datetime.now(timezone.utc).isoformat()
    return ts


def get_default_cwd() -> str:
    """Get default working directory from environment or current dir."""
    return os.environ.get("CLAUDE_CWD", os.getcwd())


def convert_arc_to_session(
    input_file: str,
    output_file: Optional[str] = None,
    dry_run: bool = False,
    cwd: Optional[str] = None,
    version: str = "2.1.86",
    git_branch: str = "main",
    thinking_separate: bool = False,
    thinking_embedded: bool = False
) -> int:
    """
    Convert arc.animalabs.ai JSON to Claude Code session JSONL.

    Returns number of messages converted.
    """
    # Read input
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    messages = data.get("messages", [])
    if not messages:
        print("No messages found in input file")
        return 0

    # Generate session-wide values
    session_id = generate_uuid()
    slug = generate_slug()

    # Determine output file
    if not output_file:
        output_file = Path(input_file).stem + "_session.jsonl"

    # Use provided cwd or detect from environment
    if cwd is None:
        cwd = get_default_cwd()

    print(f"Converting {len(messages)} messages...")
    print(f"Session ID: {session_id}")
    print(f"Working dir: {cwd}")
    print(f"Output: {output_file}")

    if dry_run:
        print("(DRY RUN - no file will be written)")

    output_lines = []
    prev_uuid = None
    converted = 0

    # Get first message timestamp for header
    first_timestamp = None
    for msg in messages:
        branches = msg.get("branches", [])
        if branches:
            branch = branches[0]
            first_timestamp = parse_arc_timestamp(branch.get("createdAt", ""))
            break
    if not first_timestamp:
        first_timestamp = datetime.now(timezone.utc).isoformat()

    # Add session header lines
    header_message_id = generate_uuid()
    permission_line = {
        "type": "permission-mode",
        "permissionMode": "default",
        "sessionId": session_id
    }
    output_lines.append(json.dumps(permission_line, ensure_ascii=False))

    snapshot_line = {
        "type": "file-history-snapshot",
        "messageId": header_message_id,
        "snapshot": {
            "messageId": header_message_id,
            "trackedFileBackups": {},
            "timestamp": first_timestamp
        },
        "isSnapshotUpdate": False
    }
    output_lines.append(json.dumps(snapshot_line, ensure_ascii=False))

    for msg in messages:
        # Get active branch (arc uses branches for message versions)
        branches = msg.get("branches", [])
        active_branch_id = msg.get("activeBranchId")

        # Find active branch
        branch = None
        for b in branches:
            if b.get("id") == active_branch_id:
                branch = b
                break

        if not branch:
            # Fallback to first branch
            branch = branches[0] if branches else None

        if not branch:
            continue

        role = branch.get("role", "user")
        created_at = branch.get("createdAt", "")
        model = branch.get("model", "claude-opus-4-5-20251101")

        # Handle content - can be string or contentBlocks array
        content = branch.get("content", "")
        content_blocks = branch.get("contentBlocks", [])

        # Extract thinking and text from contentBlocks if present
        thinking_content = None
        thinking_signature = None
        text_content = ""

        if content_blocks:
            for block in content_blocks:
                if block.get("type") == "thinking":
                    thinking_content = block.get("thinking", "")
                    thinking_signature = block.get("signature", "")
                elif block.get("type") == "text":
                    text_content = block.get("text", "")
        else:
            text_content = content

        if not text_content and not thinking_content:
            continue

        # Generate UUIDs
        msg_id = generate_message_id()
        request_id = f"req_{generate_message_id()[4:]}"
        timestamp = parse_arc_timestamp(created_at)

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

        if role == "user":
            current_uuid = generate_uuid()
            entry = {
                **base_entry,
                "parentUuid": prev_uuid,
                "uuid": current_uuid,
                "timestamp": timestamp,
                "type": "user",
                "promptId": generate_uuid(),
                "permissionMode": "default",
                "message": {
                    "role": "user",
                    "content": text_content
                }
            }
            output_lines.append(json.dumps(entry, ensure_ascii=False))
            prev_uuid = current_uuid
            converted += 1

        else:  # assistant
            # If thinking_separate: add thinking as separate entry first
            if thinking_content and thinking_separate:
                thinking_uuid = generate_uuid()
                thinking_entry = {
                    **base_entry,
                    "parentUuid": prev_uuid,
                    "uuid": thinking_uuid,
                    "timestamp": timestamp,
                    "type": "assistant",
                    "requestId": request_id,
                    "message": {
                        "model": model,
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
                output_lines.append(json.dumps(thinking_entry, ensure_ascii=False))
                prev_uuid = thinking_uuid
                converted += 1

            # Build text content (optionally with embedded thinking)
            final_text = text_content
            if thinking_content and thinking_embedded:
                final_text = f"[thinking]\n{thinking_content}\n[/thinking]\n\n{text_content}"

            # Add main text entry
            text_uuid = generate_uuid()
            text_entry = {
                **base_entry,
                "parentUuid": prev_uuid,
                "uuid": text_uuid,
                "timestamp": timestamp,
                "type": "assistant",
                "requestId": request_id,
                "message": {
                    "model": model,
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": final_text
                        }
                    ],
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
            output_lines.append(json.dumps(text_entry, ensure_ascii=False))
            prev_uuid = text_uuid
            converted += 1

        if converted % 50 == 0:
            print(f"  Converted {converted}/{len(messages)}...")

    # Write output
    if not dry_run:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(output_lines) + '\n')  # Trailing newline required!
        print(f"\nWrote {converted} messages to {output_file}")
    else:
        print(f"\nWould write {converted} messages")
        # Show first few lines as preview
        print("\nPreview (first 2 entries):")
        for line in output_lines[:2]:
            preview = json.loads(line)
            print(f"  {preview['type']}: {preview['message'].get('content', '')[:60] if isinstance(preview['message'].get('content'), str) else preview['message'].get('content', [{}])[0].get('text', '')[:60]}...")

    return converted


def main():
    parser = argparse.ArgumentParser(
        description="Convert arc.animalabs.ai chat to Claude Code session"
    )
    parser.add_argument("input_file", help="Path to arc JSON export")
    parser.add_argument("--output", "-o", help="Output JSONL file path")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--cwd", default=None,
                        help="Working directory for session (default: current dir or CLAUDE_CWD env)")
    parser.add_argument("--thinking-separate", action="store_true",
                        help="Add thinking blocks as separate entries (like Claude Code native)")
    parser.add_argument("--thinking-embedded", action="store_true",
                        help="Embed thinking in message as [thinking]...[/thinking]")

    args = parser.parse_args()

    if not Path(args.input_file).exists():
        print(f"Error: Input file not found: {args.input_file}")
        sys.exit(1)

    print(f"Thinking mode: separate={args.thinking_separate}, embedded={args.thinking_embedded}")

    convert_arc_to_session(
        args.input_file,
        output_file=args.output,
        dry_run=args.dry_run,
        cwd=args.cwd,
        thinking_separate=args.thinking_separate,
        thinking_embedded=args.thinking_embedded
    )


if __name__ == "__main__":
    main()
