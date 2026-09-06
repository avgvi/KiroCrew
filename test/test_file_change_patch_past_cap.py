"""An edit past the per-file snapshot cap must still reach the UI.

The before/after snapshots are each cut from the START of the file, so an edit
beyond ``_MAX_SNAPSHOT`` leaves both sides byte-identical: a real change reads as
no change at all, and the budget was spent on a prefix that does not contain it.
The entry therefore carries a capped unified diff instead, which is small for a
small edit however large the file and whose hunk headers keep the true line
numbers.
"""

from __future__ import annotations

from kiro_crew.dashboard import chat_runner as cr


def _big(marker_line: str, *, lines: int = 400) -> str:
    """A body comfortably past the snapshot cap, with one distinctive line."""
    filler = "x" * 800
    body = [f"{filler}  # line {i}" for i in range(lines)]
    body[-1] = marker_line
    return "\n".join(body) + "\n"


class _Slot:
    """Minimal stand-in for the fields `_flush_file_changes` touches."""

    key = "slot-under-test"

    def __init__(self, changes):
        self._file_changes = changes
        self._dirty = False
        # One assistant message already present, so the flush attaches to it
        # rather than taking the synthetic-message path.
        self.messages = [{"role": "assistant", "content": "done", "meta": {}}]

    def append(self, *args, **kwargs):  # pragma: no cover - synthetic path
        raise AssertionError("an assistant message exists; nothing to synthesize")


def test_before_entry_carries_full_text_only_when_the_cap_bit(tmp_path):
    small = "one\ntwo\n"
    assert "before_full" not in cr._before_entry("/a.ts", small)

    big = _big("marker BEFORE")
    assert len(big) > cr._MAX_SNAPSHOT
    entry = cr._before_entry("/a.ts", big)
    # The capped prefix is what would be persisted; the full text rides along
    # transiently so a patch can be computed from it.
    assert entry["before_full"] == big
    assert len(entry["content"]) < len(big)


def test_edit_past_the_cap_is_carried_as_a_patch(tmp_path):
    before_full = _big("marker BEFORE")
    after_full = _big("marker AFTER")
    # Precondition: this is exactly the blind spot — the capped pair is equal.
    assert cr._truncate_snapshot(before_full) == cr._truncate_snapshot(after_full)

    p = tmp_path / "huge.tsx"
    p.write_text(after_full, encoding="utf-8")
    slot = _Slot([cr._before_entry(str(p), before_full)])
    cr._flush_file_changes(slot)

    changes = slot.messages[-1]["meta"]["file_changes"]
    assert len(changes) == 1
    row = changes[0]
    # The pair still reads as unchanged — that is the cap, not a bug to hide.
    assert row["before"] == row["after"]
    # ...and the patch is what tells the truth about the change.
    assert "marker AFTER" in row["patch"]
    assert "marker BEFORE" in row["patch"]
    assert row["patch"].startswith("---")
    # Transient key must never reach message meta.
    assert "before_full" not in row


def test_no_patch_when_the_capped_pair_can_express_the_change(tmp_path):
    p = tmp_path / "small.ts"
    p.write_text("one\ntwo\nthree\n", encoding="utf-8")
    slot = _Slot([cr._before_entry(str(p), "one\ntwo\n")])
    cr._flush_file_changes(slot)
    row = slot.messages[-1]["meta"]["file_changes"][0]
    assert "patch" not in row


def test_a_genuine_no_op_past_the_cap_gets_no_patch(tmp_path):
    """An idempotent write must stay a no-op, not acquire an empty patch."""
    body = _big("marker SAME")
    p = tmp_path / "huge.tsx"
    p.write_text(body, encoding="utf-8")
    slot = _Slot([cr._before_entry(str(p), body)])
    cr._flush_file_changes(slot)
    row = slot.messages[-1]["meta"]["file_changes"][0]
    assert "patch" not in row


def test_the_patch_is_credential_scrubbed(tmp_path):
    """The patch carries file content, so it obeys the same scrub as before/after
    — otherwise the oversized-file path is a way around that layer."""
    secret = "AKIAIOSFODNN7EXAMPLE"
    before_full = _big("marker BEFORE")
    after_full = _big(f"key = {secret}")
    p = tmp_path / "huge.tsx"
    p.write_text(after_full, encoding="utf-8")
    slot = _Slot([cr._before_entry(str(p), before_full)])
    cr._flush_file_changes(slot)
    row = slot.messages[-1]["meta"]["file_changes"][0]
    assert secret not in row["patch"]


def test_the_patch_is_capped(tmp_path):
    """A large change PAST the cap must not smuggle the file back in as a diff.

    The shared prefix is what puts the change past the cap — a whole-file rewrite
    differs inside the prefix too, so the capped pair is unequal and no patch is
    needed at all.
    """
    prefix = "\n".join("x" * 800 for _ in range(300)) + "\n"
    before_full = prefix + "\n".join(f"before line {i}" for i in range(20_000)) + "\n"
    after_full = prefix + "\n".join(f"after line {i}" for i in range(20_000)) + "\n"
    assert cr._truncate_snapshot(before_full) == cr._truncate_snapshot(after_full)
    p = tmp_path / "huge.tsx"
    p.write_text(after_full, encoding="utf-8")
    slot = _Slot([cr._before_entry(str(p), before_full)])
    cr._flush_file_changes(slot)
    row = slot.messages[-1]["meta"]["file_changes"][0]
    # The cap lives in chat_utils, which owns the ONE patch implementation: the
    # read path shrinks oversized pairs through it too, so a second copy in the
    # writer would let the two halves cap differently.
    from kiro_crew.dashboard import chat_utils as cu

    assert len(row["patch"]) <= cu._MAX_PATCH + 100
    assert "patch truncated" in row["patch"]
