# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the agent said on the way versus what it answered."""






from __future__ import annotations



_MIN_ANSWER_CHARS = 20


def split_narration(text: str, final_start: int | None = None) -> tuple[str, str]:
    """``(narration, answer)``: the lines the agent wrote between tool calls and the text it wrote after the last one."""






    text = (text or "").strip("\n")
    if not text.strip():
        return "", ""
    if final_start == 0:
        return "", text.strip()
    if final_start is not None and 0 < final_start < len(text):
        narration, answer = text[:final_start].strip(), text[final_start:].strip()
    else:
        parts = [p for p in text.split("\n\n") if p.strip()]
        narration, answer = "\n\n".join(parts[:-1]).strip(), (parts[-1].strip() if parts else "")
    if not narration or len(answer) < _MIN_ANSWER_CHARS:
        return "", text.strip()
    return narration, answer


def fold_narration(bubble, trace, final_start: int | None = None) -> None:
    """Move the agent's on-the-way lines from the bubble into the trace, so the message under the block is the answer alone (the block holds the."""

    narration, answer = split_narration(bubble.text(), final_start)
    if not narration:
        return
    for paragraph in narration.split("\n\n"):
        if paragraph.strip():
            trace.add_narration(" ".join(paragraph.split()))
    bubble.set_text(answer)
