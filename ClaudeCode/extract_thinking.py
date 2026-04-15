#!/usr/bin/env python3
"""
Extract thinking blocks from Claude Code session to a separate file.

Creates a JSONL file with thinking linked to the corresponding text response.
Each entry contains:
- uuid: UUID of the text response (not the thinking entry)
- timestamp: when the response was created
- preview: first N characters of the response text
- thinking: the full thinking content

This allows access to reasoning without loading it into context,
and makes it easy to find thinking related to specific responses.

Usage:
    python3 extract_thinking.py <session.jsonl> [--output <thinking.jsonl>] [--preview-length 200]

Example:
    python3 extract_thinking.py sonnet_session.jsonl --output sonnet_thinking.jsonl
"""
import json
import sys
from pathlib import Path
from typing import Optional, List, Dict
import argparse


def extract_thinking(input_file: str, output_file: str = None, preview_length: int = 200) -> int:
    """
    Extract thinking blocks from session file, linking to text responses.

    Returns number of thinking blocks extracted.
    """
    if not output_file:
        output_file = Path(input_file).stem + "_thinking.jsonl"

    # First pass: read all entries
    entries = []
    with open(input_file, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                entries.append(entry)
            except json.JSONDecodeError:
                continue

    thinking_entries = []

    # Second pass: find thinking and link to next text response
    i = 0
    while i < len(entries):
        entry = entries[i]

        if entry.get("type") != "assistant":
            i += 1
            continue

        message = entry.get("message", {})
        content = message.get("content", [])

        if not isinstance(content, list):
            i += 1
            continue

        # Check if this is a thinking entry
        thinking_text = None
        is_thinking_entry = False

        for block in content:
            if block.get("type") == "thinking":
                thinking_text = block.get("thinking", "")
                is_thinking_entry = True
                break

        # Also check for embedded [thinking]...[/thinking]
        if not thinking_text:
            for block in content:
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if "[thinking]" in text and "[/thinking]" in text:
                        start = text.find("[thinking]") + len("[thinking]")
                        end = text.find("[/thinking]")
                        if start < end:
                            thinking_text = text[start:end].strip()
                            is_thinking_entry = True
                        break

        if thinking_text:
            # Find the next assistant entry with text content
            text_uuid = None
            text_timestamp = None
            text_preview = None

            # Look for text in the same entry first (native format has both)
            for block in content:
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if text and not text.startswith("[thinking]"):
                        text_uuid = entry.get("uuid")
                        text_timestamp = entry.get("timestamp")
                        text_preview = text[:preview_length].replace('\n', ' ')
                        break

            # If not found, look in next entries
            if text_uuid is None:
                for j in range(i + 1, min(i + 5, len(entries))):  # Look ahead up to 5 entries
                    next_entry = entries[j]
                    if next_entry.get("type") != "assistant":
                        continue

                    next_message = next_entry.get("message", {})
                    next_content = next_message.get("content", [])

                    if not isinstance(next_content, list):
                        continue

                    for block in next_content:
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                text_uuid = next_entry.get("uuid")
                                text_timestamp = next_entry.get("timestamp")
                                text_preview = text[:preview_length].replace('\n', ' ')
                                break
                    if text_uuid:
                        break

            # Fallback to current entry if no text found
            if text_uuid is None:
                text_uuid = entry.get("uuid")
                text_timestamp = entry.get("timestamp")
                text_preview = "(no text response found)"

            thinking_entries.append({
                "uuid": text_uuid,
                "timestamp": text_timestamp,
                "preview": text_preview,
                "thinking": thinking_text
            })

        i += 1

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
    parser.add_argument("--preview-length", "-p", type=int, default=200,
                        help="Number of characters in preview (default: 200)")

    args = parser.parse_args()

    if not Path(args.input_file).exists():
        print(f"Error: Input file not found: {args.input_file}")
        sys.exit(1)

    extract_thinking(args.input_file, args.output, args.preview_length)


if __name__ == "__main__":
    main()
