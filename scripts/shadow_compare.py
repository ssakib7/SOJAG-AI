"""Shadow-mode comparison report (phase 6 of AGNO_REBUILD_PLAN.md).

While the new bot runs with SHADOW_MODE=true on mirrored webhook traffic, it records
every reply it WOULD have sent in the shadow_drafts table. The live Node bot logs its
actual replies to its container log as lines like:

    → [<senderId>] <reply text>

This script aligns the two per sender and writes a side-by-side markdown report so a
human can judge persona fidelity, routing sanity, and dropped/extra replies before
cutover.

Usage:
    docker logs dejure-fb-bot > old-bot.log        # on the VPS
    uv run python scripts/shadow_compare.py old-bot.log report.md
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.engine import db_session, dispose_engine  # noqa: E402
from app.db.models import ShadowDraftRow  # noqa: E402

# Old-bot reply lines. Docker timestamps (if `docker logs -t`) are tolerated as a prefix.
OLD_REPLY = re.compile(r"^(?:(?P<ts>\S+)\s+)?→ \[(?P<sender>\d+)\] (?P<text>.*)$")


def parse_old_log(path: Path) -> dict[str, list[dict]]:
    replies: dict[str, list[dict]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = OLD_REPLY.match(line.strip())
        if m:
            replies[m.group("sender")].append({"ts": m.group("ts") or "", "text": m.group("text")})
    return replies


async def load_drafts() -> dict[str, list[dict]]:
    drafts: dict[str, list[dict]] = defaultdict(list)
    async with db_session() as db:
        rows = (
            await db.execute(
                select(ShadowDraftRow).where(ShadowDraftRow.kind == "message").order_by(ShadowDraftRow.at)
            )
        ).scalars().all()
    for row in rows:
        drafts[row.sender_id].append({"ts": row.at.isoformat() if row.at else "", "text": row.text})
    return drafts


def build_report(old: dict[str, list[dict]], new: dict[str, list[dict]]) -> str:
    senders = sorted(set(old) | set(new))
    lines = [
        "# Shadow-mode comparison report",
        "",
        f"Senders seen — live bot: {len(old)}, shadow bot: {len(new)}, union: {len(senders)}",
        "",
        "| | |",
        "|---|---|",
        f"| Live replies | {sum(len(v) for v in old.values())} |",
        f"| Shadow drafts | {sum(len(v) for v in new.values())} |",
        f"| Senders only live answered | {len([s for s in senders if s in old and s not in new])} |",
        f"| Senders only shadow answered | {len([s for s in senders if s in new and s not in old])} |",
        "",
        "Review each conversation below: tone (স্যার/ম্যাডাম, sentence length), facts "
        "(prices, dates), and whether either side replied when the other stayed silent.",
        "",
    ]
    for sender in senders:
        lines.append(f"## Sender {sender}")
        lines.append("")
        lines.append("| Live bot | Shadow bot |")
        lines.append("|---|---|")
        left = old.get(sender, [])
        right = new.get(sender, [])
        for i in range(max(len(left), len(right))):
            cell_l = left[i]["text"].replace("|", "\\|") if i < len(left) else "—"
            cell_r = right[i]["text"].replace("|", "\\|") if i < len(right) else "—"
            lines.append(f"| {cell_l} | {cell_r} |")
        lines.append("")
    return "\n".join(lines)


async def main(old_log: Path, out: Path) -> None:
    old = parse_old_log(old_log)
    new = await load_drafts()
    out.write_text(build_report(old, new), encoding="utf-8")
    print(f"Report written to {out} ({len(set(old) | set(new))} senders).")
    await dispose_engine()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2])))
