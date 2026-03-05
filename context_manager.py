"""Conversation context manager with automatic compression."""
import json
import logging
import time
from typing import Optional

log = logging.getLogger("context")

# When history exceeds this many messages, compress older ones
COMPRESS_THRESHOLD = 20
# Keep this many recent messages uncompressed  
KEEP_RECENT = 10
# Max tokens for summary (approximate by chars)
SUMMARY_MAX_CHARS = 1500


async def maybe_compress_history(
    client,  # AsyncOpenAI
    model: str,
    messages: list[dict],
) -> list[dict]:
    """If the message list is long, compress older messages into a summary.
    
    Returns a new message list with:
    - system prompt (unchanged)
    - [summary of older messages] as a system message
    - recent messages (unchanged)
    """
    # Don't count system message
    non_system = [m for m in messages if m["role"] != "system"]
    system_msgs = [m for m in messages if m["role"] == "system"]
    
    if len(non_system) <= COMPRESS_THRESHOLD:
        return messages  # No compression needed
    
    # Split: older messages to compress, recent to keep
    to_compress = non_system[:-KEEP_RECENT]
    to_keep = non_system[-KEEP_RECENT:]
    
    log.info(f"Compressing {len(to_compress)} older messages, keeping {len(to_keep)} recent")
    
    # Build compression prompt
    conversation_text = _format_messages_for_summary(to_compress)
    
    try:
        summary = await _generate_summary(client, model, conversation_text)
    except Exception as e:
        log.error(f"Compression failed: {e}, using truncation fallback")
        # Fallback: just keep recent messages
        return system_msgs + to_keep
    
    # Build new message list
    summary_msg = {
        "role": "system",
        "content": f"[Previous conversation summary]\n{summary}\n[End of summary \u2014 recent messages follow]",
    }
    
    return system_msgs + [summary_msg] + to_keep


async def _generate_summary(client, model: str, conversation_text: str) -> str:
    """Call the LLM to summarize a conversation chunk."""
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Summarize the following conversation concisely. "
                    "Preserve: key decisions, action items, important facts, user preferences, and context needed for continuation. "
                    "Omit: greetings, filler, repeated information. "
                    "Use bullet points. Keep under 1500 characters. "
                    "Write in the same language as the conversation."
                ),
            },
            {"role": "user", "content": conversation_text},
        ],
        max_tokens=1024,
    )
    return response.choices[0].message.content or ""


def _format_messages_for_summary(messages: list[dict]) -> str:
    """Format messages into readable text for summarization."""
    lines = []
    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")
        
        if role == "tool":
            # Summarize tool results briefly
            content_short = content[:300] + "..." if len(content) > 300 else content
            lines.append(f"[Tool result]: {content_short}")
        elif role == "assistant":
            if msg.get("tool_calls"):
                tool_names = []
                for tc in msg["tool_calls"]:
                    if isinstance(tc, dict):
                        name = tc.get("function", {}).get("name", "?")
                    else:
                        name = tc.function.name
                    tool_names.append(name)
                lines.append(f"Assistant: [called tools: {', '.join(tool_names)}]")
                if content:
                    lines.append(f"Assistant: {content[:500]}")
            else:
                lines.append(f"Assistant: {content[:500]}")
        elif role == "user":
            lines.append(f"User: {content[:500]}")
    
    return "\n".join(lines)
