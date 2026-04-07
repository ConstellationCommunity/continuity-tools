#!/usr/bin/env python3
"""
Extract thinking blocks from Claude Code session to a separate file.

Creates a JSONL file with uuid and thinking for each assistant message.
This allows access to reasoning without loading it into context.

Usage:
    python3 extract_thinking.py <session.jsonl> [--output <thinking.jsonl>]

Example:
    python3 extract_thinking.py sonnet_session.jsonl --output sonnet_thinking.jsonl
"""
import json
import sys
from pathlib import Path
import argparse


def extract_thinking(input_file: str, output_file: str = None) -> int:
    """
    Extract thinking blocks from session file.

    Returns number of thinking blocks extracted.
    """
    if not output_file:
        output_file = Path(input_file).stem + "_thinking.jsonl"

    thinking_entries = []

    with open(input_file, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            if entry.get("type") != "assistant":
                continue

            message = entry.get("message", {})
            content = message.get("content", [])
            entry_uuid = entry.get("uuid")

            if not entry_uuid or not isinstance(content, list):
                continue

            # Look for thinking blocks
            for block in content:
                if block.get("type") == "thinking":
                    thinking_text = block.get("thinking", "")
                    if thinking_text:
                        thinking_entries.append({
                            "uuid": entry_uuid,
                            "thinking": thinking_text
                        })
                        break  # One thinking per message

            # Also check for embedded [thinking]...[/thinking]
            for block in content:
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if "[thinking]" in text and "[/thinking]" in text:
                        start = text.find("[thinking]") + len("[thinking]")
                        end = text.find("[/thinking]")
                        if start < end:
                            thinking_text = text[start:end].strip()
                            if thinking_text:
                                # Check if we already have this uuid
                                existing = [e for e in thinking_entries if e["uuid"] == entry_uuid]
                                if not existing:
                                    thinking_entries.append({
                                        "uuid": entry_uuid,
                                        "thinking": thinking_text
                                    })
                        break

    # Write output
    with open(output_file, 'w', encoding='utf-8') as f:
        for entry in thinking_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    print(f"Extracted {len(thinking_entries)} thinking blocks to {output_file}")
    return len(thinking_entries)


def main():
    parser = argparse.ArgumentParser(
        description="Extract thinking blocks from Claude Code session"
    )
    parser.add_argument("input_file", help="Path to session JSONL file")
    parser.add_argument("--output", "-o", help="Output JSONL file path")

    args = parser.parse_args()

    if not Path(args.input_file).exists():
        print(f"Error: Input file not found: {args.input_file}")
        sys.exit(1)

    extract_thinking(args.input_file, args.output)


if __name__ == "__main__":
    main()
