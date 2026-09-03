"""Physician review of individual facts — the `review_status` state machine.

⛔ THE THING THIS MODULE EXISTS TO KEEP APART.

Committing a summary is the physician accepting an ENCOUNTER. It is not them having read
each of forty facts. `promote()` therefore writes every fact `pending` and claims nothing;
becoming `confirmed` happens here, one fact at a time, by a named clinician, with a
`PhysicianDecision` row and an audit entry for each.

Four statuses, and the fourth is why a boolean could not do this job:

    pending    nobody has looked at it yet
    confirmed  a physician signed it off — the ONLY status admitted to active clinical use
    edited     a physician changed the value and has NOT yet confirmed the new one
    rejected   a physician threw it out

`rejected` is **terminal**. There is no transition out of it, and that is a deliberate
clinical decision rather than a simplification: a doctor rejecting a fact has made a positive
statement about it, and quietly resurrecting that fact later — by any path, including an
accidental second click — would put something a clinician removed back into a patient's
record. `_assert_legal()` refuses it.

`edited` does NOT imply `confirmed`. Editing a dose and approving a dose are two acts, so
they are two transitions: `pending → edited → confirmed`. The intermediate state is what lets
a review surface show "changed, not yet signed off", which is a real thing a physician needs
to see on their own worklist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.chain import record
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.db.durable import (
    REVIEW_STATUSES,
    ClinicalFactRecord,
    Encounter,
    PhysicianDecision,
)

log = get_logger(__name__)

#: Which statuses each status may become. `rejected` maps to the empty set on purpose — see
#: the module docstring. Read this as the whole state machine; there is no other branch.
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"confirmed", "rejected", "edited"}),
    "edited": frozenset({"confirmed", "rejected"}),
    "confirmed": frozenset({"rejected", "edited"}),
    "rejected": frozenset(),
}

#: `PhysicianDecision.decision` values this module writes. `edited_fact` already existed;
#: the other two are new and named to match, so one query over that table answers "what did
#: this clinician actually do in this encounter".
DECISION_FOR: dict[str, str] = {
    "confirmed": "confirmed_fact",
    "rejected": "rejected_fact",
    "edited": "edited_fact",
}


class IllegalTransition(ValidationError):
    """A review transition the state machine forbids. 400, with the reason in words."""


def _assert_legal(current: str, wanted: str) -> None:
    if wanted not in REVIEW_STATUSES:
        raise IllegalTransition(
            f"{wanted!r} is not a review status. Expected one of: {', '.join(REVIEW_STATUSES)}."
        )
    if current == wanted:
        # Idempotent in effect, but refused loudly: a second click that silently "succeeds"
        # writes a duplicate PhysicianDecision and a duplicate audit row, which makes the
        # trail read as two separate acts of judgement where there was one.
        raise IllegalTransition(f"This fact is already {current!r}.")
    allowed = LEGAL_TRANSITIONS[current]
    if not allowed:
        raise IllegalTransition(
            f"A rejected fact is final and cannot become {wanted!r}. A physician rejecting a "
            "fact is a clinical statement, not a draft state — record a new fact instead of "
            "resurrecting this one."
        )
    if wanted not in allowed:
        raise IllegalTransition(
            f"A {current!r} fact cannot become {wanted!r}. Allowed: {', '.join(sorted(allowed))}."
        )


async def set_review_status(
    db: AsyncSession,
    *,
    encounter: Encounter,
    fact_ref: str,
    status: str,
    actor: str,
    actor_role: str,
    new_value: Any = None,
    reason: str | None = None,
) -> ClinicalFactRecord:
    """Move one fact along the review axis. The only writer of `review_status`.

    `new_value` applies to `edited` only, and the edit is recorded on the row's own
    `value_json`/`display_value` while the ORIGINAL stays reachable through the audit trail
    and the `PhysicianDecision.detail_json`. Facts are never deleted here — the same rule the
    ledger keeps on the capture side.
    """
    fact = (
        (
            await db.execute(
                select(ClinicalFactRecord).where(
                    ClinicalFactRecord.encounter_id == encounter.id,
                    ClinicalFactRecord.fact_ref == fact_ref,
                )
            )
        )
        .scalars()
        .first()
    )
    if fact is None:
        raise ValidationError(f"No fact {fact_ref!r} in this encounter.")

    _assert_legal(fact.review_status, status)

    detail: dict[str, Any] = {"factRef": fact_ref, "path": fact.path, "from": fact.review_status}
    if reason:
        detail["reason"] = reason

    if status == "edited":
        if new_value is None:
            raise IllegalTransition("An edit must supply the new value.")
        # Both halves are kept: the physician's correction becomes the value, and what it
        # replaced is preserved in the decision row. A correction with no record of what it
        # corrected is an anonymous overwrite, which is what ADR-0012 refuses for OCR spans
        # and the same argument applies here.
        detail["previousValue"] = fact.value_json
        detail["previousDisplay"] = fact.display_value
        detail["newValue"] = {"v": new_value}
        fact.value_json = {"v": new_value}
        fact.display_value = str(new_value)
        # An edited value did not come from the patient or a document any more.
        fact.origin = "physician_entered"

    fact.review_status = status
    fact.reviewed_by = actor
    fact.reviewed_at = datetime.now(UTC)
    # The legacy boolean, kept in step because `report/brief.py` puts it on the wire. Nothing
    # branches on it; see the column's docstring.
    fact.confirmed_by_physician = status == "confirmed"

    db.add(
        PhysicianDecision(
            encounter_id=encounter.id,
            decision=DECISION_FOR[status],
            actor=actor,
            detail_json=detail,
        )
    )

    await record(
        db,
        actor=actor,
        actor_role=actor_role,
        purpose_of_use="TREATMENT",
        action=f"fact.{status}",
        consent_ref=encounter.consent_ref,
        request_summary={"encounterRef": encounter.encounter_ref, "factRef": fact_ref},
        response_summary={"reviewStatus": status, "from": detail["from"]},
    )

    log.info(
        "review.fact",
        encounter=encounter.encounter_ref,
        fact=fact_ref,
        to=status,
        actor=actor,
    )
    return fact


def summarise(facts: list[ClinicalFactRecord]) -> dict[str, int]:
    """Counts per status, for a review worklist. Every status present, including zeroes.

    Omitting a zero would make an empty `rejected` bucket indistinguishable from a screen
    that forgot to ask about rejections.
    """
    counts = dict.fromkeys(REVIEW_STATUSES, 0)
    for fact in facts:
        if fact.review_status in counts:
            counts[fact.review_status] += 1
    return counts


__all__ = ["LEGAL_TRANSITIONS", "IllegalTransition", "set_review_status", "summarise"]
