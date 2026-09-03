"""Session 3 — the physician-review axis, durable origin, and cross-visit contradictions.

The property most of this file exists to pin is the one a boolean could not express:

    pending   nobody has looked at it        → withheld from ACTIVE use, shown on review
    edited    changed, not yet signed off    → withheld from ACTIVE use, shown on review
    confirmed a physician signed it off      → the only status admitted to active use
    rejected  a physician threw it out       → in NO clinical view, at any time

`rejected` is not "pending, but more so". A doctor removing a fact has made a positive
clinical statement, and the tests below check that statement is honoured on every read path
rather than on the one that happened to get written first.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.contracts.contradictions import detect, load_rules
from app.contracts.history import Demographics
from app.contracts.projection import project
from app.contracts.provenance import (
    BoundingBox,
    DocumentSpan,
    SourceTier,
)
from app.contracts.record import FactLedger, record_fact
from app.db.durable import (
    ACTIVE_REVIEW_STATUSES,
    REVIEW_STATUSES,
    REVIEWABLE_REVIEW_STATUSES,
    ClinicalFactRecord,
    ContradictionRecord,
    Encounter,
    MedicationEvent,
    PhysicianDecision,
)
from app.db.models import IntakeSession
from app.modules.encounter import history as H
from app.modules.encounter.promote import promote
from app.modules.encounter.review import (
    LEGAL_TRANSITIONS,
    IllegalTransition,
    set_review_status,
)
from app.redflags.engine import evaluate
from tests.helpers import tap


async def _session(db, *, abha_ref: str) -> IntakeSession:
    row = IntakeSession(
        session_ref=f"sess_{abha_ref[-8:]}",
        abha_ref=abha_ref,
        consent_ref="consent_test",
        language="en",
        status="ready_for_review",
        priority="routine",
        created_at=datetime.now(UTC),
    )
    db.add(row)
    await db.flush()
    return row


async def _promote(db, row, ledger, *, by: str = "dr.test"):
    history = project(ledger, demographics=Demographics(age_years=52, gender="male"))
    return await promote(
        db,
        session_row=row,
        ledger=ledger,
        history=history,
        escalation=evaluate(ledger),
        contradictions=[],
        summary_payload={"status": "draft"},
        traceable=True,
        confirmed_by=by,
    )


async def _encounter(db, encounter_ref: str) -> Encounter:
    return (
        await db.execute(select(Encounter).where(Encounter.encounter_ref == encounter_ref))
    ).scalars().first()


async def _facts(db, encounter_id: int) -> list[ClinicalFactRecord]:
    return list(
        (
            await db.execute(
                select(ClinicalFactRecord).where(
                    ClinicalFactRecord.encounter_id == encounter_id
                )
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------- promotion


async def test_promotion_writes_pending_not_confirmed(db_session) -> None:
    """⛔ COMMITTING A SUMMARY IS NOT REVIEWING FORTY FACTS.

    Before this session `promote()` wrote every fact unconditionally and the record could not
    tell the two acts apart. A physician accepting an encounter is one judgement; signing off
    each fact in it is another, and only the second is what `confirmed` now means.
    """
    ledger = FactLedger("s")
    tap(ledger, "chief_complaint.text", "pain")
    result = await _promote(db_session, await _session(db_session, abha_ref="abha:pend01"), ledger)

    encounter = await _encounter(db_session, result.encounter_ref)
    facts = await _facts(db_session, encounter.id)
    assert facts, "nothing was promoted"
    assert {f.review_status for f in facts} == {"pending"}


async def test_promotion_records_a_durable_origin(db_session) -> None:
    """Origin is the durable axis Invariant 2 forbids adding to `SourceTier`."""
    ledger = FactLedger("s")
    tap(ledger, "chief_complaint.text", "pain")
    result = await _promote(db_session, await _session(db_session, abha_ref="abha:orig01"), ledger)

    facts = await _facts(db_session, (await _encounter(db_session, result.encounter_ref)).id)
    assert {f.origin for f in facts} == {"patient_stated"}


async def test_promoted_medications_always_carry_a_fact_ref(db_session) -> None:
    """⛔ THE GUARD ON `_backed_by`'s NULL BRANCH.

    That helper admits a row whose `source_fact_ref` is NULL, because some rows genuinely
    have no backing fact and `NULL IN (...)` would silently drop them. That is only safe
    while promotion never produces such a row for real data — so this asserts it, rather than
    leaving a comment claiming it. If promotion ever emits a medication with no fact ref, the
    review gate has a hole and this fails first.
    """
    ledger = FactLedger("s")
    tap(ledger, "chief_complaint.text", "pain")
    tap(ledger, "medications[0].name", "Metformin")
    result = await _promote(db_session, await _session(db_session, abha_ref="abha:medref"), ledger)

    encounter = await _encounter(db_session, result.encounter_ref)
    meds = list(
        (
            await db_session.execute(
                select(MedicationEvent).where(MedicationEvent.encounter_id == encounter.id)
            )
        )
        .scalars()
        .all()
    )
    assert meds, "no medication was promoted"
    for med in meds:
        assert med.source_fact_ref is not None, (
            "a promoted medication has no backing fact, so it would bypass review entirely"
        )


# ---------------------------------------------------------------- transitions


def test_the_transition_graph_makes_rejected_terminal() -> None:
    """Read as a whole: `rejected` is the only status with nowhere to go."""
    assert LEGAL_TRANSITIONS["rejected"] == frozenset()
    for status, allowed in LEGAL_TRANSITIONS.items():
        assert status in REVIEW_STATUSES
        assert allowed <= set(REVIEW_STATUSES)


@pytest.mark.parametrize(
    ("first", "second"),
    [("confirmed", None), ("rejected", None), ("edited", "confirmed"), ("edited", "rejected")],
)
async def test_legal_transitions_are_recorded(db_session, first, second) -> None:
    ledger = FactLedger("s")
    tap(ledger, "chief_complaint.text", "pain")
    result = await _promote(
        db_session, await _session(db_session, abha_ref=f"abha:t{first[:4]}{second or ''}"), ledger
    )
    encounter = await _encounter(db_session, result.encounter_ref)
    fact = (await _facts(db_session, encounter.id))[0]

    updated = await set_review_status(
        db_session,
        encounter=encounter,
        fact_ref=fact.fact_ref,
        status=first,
        actor="dr.who",
        actor_role="clinician",
        new_value="revised" if first == "edited" else None,
    )
    assert updated.review_status == first
    assert updated.reviewed_by == "dr.who"
    assert updated.reviewed_at is not None

    if second:
        again = await set_review_status(
            db_session,
            encounter=encounter,
            fact_ref=fact.fact_ref,
            status=second,
            actor="dr.who",
            actor_role="clinician",
        )
        assert again.review_status == second


async def test_a_rejected_fact_can_never_be_revived(db_session) -> None:
    """⛔ TERMINAL MEANS TERMINAL, BY EVERY ROUTE.

    A doctor rejecting a fact is a clinical statement, and an accidental second click must
    not put it back into a patient's record.
    """
    ledger = FactLedger("s")
    tap(ledger, "chief_complaint.text", "pain")
    result = await _promote(db_session, await _session(db_session, abha_ref="abha:term01"), ledger)
    encounter = await _encounter(db_session, result.encounter_ref)
    fact = (await _facts(db_session, encounter.id))[0]

    await set_review_status(
        db_session,
        encounter=encounter,
        fact_ref=fact.fact_ref,
        status="rejected",
        actor="dr.who",
        actor_role="clinician",
    )
    for attempt in ("confirmed", "pending", "edited"):
        with pytest.raises(IllegalTransition):
            await set_review_status(
                db_session,
                encounter=encounter,
                fact_ref=fact.fact_ref,
                status=attempt,
                actor="dr.who",
                actor_role="clinician",
                new_value="x",
            )


async def test_editing_does_not_imply_confirmation(db_session) -> None:
    """Changing a value and approving it are two acts, so they are two transitions."""
    ledger = FactLedger("s")
    tap(ledger, "chief_complaint.text", "pain")
    result = await _promote(db_session, await _session(db_session, abha_ref="abha:edit01"), ledger)
    encounter = await _encounter(db_session, result.encounter_ref)
    fact = (await _facts(db_session, encounter.id))[0]

    edited = await set_review_status(
        db_session,
        encounter=encounter,
        fact_ref=fact.fact_ref,
        status="edited",
        actor="dr.who",
        actor_role="clinician",
        new_value="burning pain",
    )
    assert edited.review_status == "edited"
    assert edited.review_status not in ACTIVE_REVIEW_STATUSES
    assert edited.display_value == "burning pain"
    # The edit changed who is speaking, and the record says so.
    assert edited.origin == "physician_entered"


async def test_an_edit_keeps_what_it_replaced(db_session) -> None:
    """A correction with no record of what it corrected is an anonymous overwrite."""
    ledger = FactLedger("s")
    tap(ledger, "chief_complaint.text", "pain")
    result = await _promote(db_session, await _session(db_session, abha_ref="abha:keep01"), ledger)
    encounter = await _encounter(db_session, result.encounter_ref)
    fact = (await _facts(db_session, encounter.id))[0]
    before = fact.display_value

    await set_review_status(
        db_session,
        encounter=encounter,
        fact_ref=fact.fact_ref,
        status="edited",
        actor="dr.who",
        actor_role="clinician",
        new_value="something else",
    )
    decisions = list(
        (
            await db_session.execute(
                select(PhysicianDecision).where(
                    PhysicianDecision.encounter_id == encounter.id,
                    PhysicianDecision.decision == "edited_fact",
                )
            )
        )
        .scalars()
        .all()
    )
    assert decisions, "an edit wrote no PhysicianDecision"
    assert decisions[-1].detail_json["previousDisplay"] == before


async def test_a_no_op_transition_is_refused(db_session) -> None:
    """Two clicks must not read as two separate acts of judgement in the audit trail."""
    ledger = FactLedger("s")
    tap(ledger, "chief_complaint.text", "pain")
    result = await _promote(db_session, await _session(db_session, abha_ref="abha:noop01"), ledger)
    encounter = await _encounter(db_session, result.encounter_ref)
    fact = (await _facts(db_session, encounter.id))[0]

    await set_review_status(
        db_session, encounter=encounter, fact_ref=fact.fact_ref,
        status="confirmed", actor="dr.who", actor_role="clinician",
    )
    with pytest.raises(IllegalTransition):
        await set_review_status(
            db_session, encounter=encounter, fact_ref=fact.fact_ref,
            status="confirmed", actor="dr.who", actor_role="clinician",
        )


# ---------------------------------------------------------------- never resurfaces


async def _one_fact_encounter(db, abha: str, path: str, value: str):
    ledger = FactLedger("s")
    tap(ledger, "chief_complaint.text", value)
    tap(ledger, path, value)
    result = await _promote(db, await _session(db, abha_ref=abha), ledger)
    encounter = await _encounter(db, result.encounter_ref)
    return result, encounter


async def test_a_rejected_fact_is_gone_from_every_clinical_read(db_session) -> None:
    """⛔ THE CENTRAL PROPERTY OF THIS SESSION, CHECKED ON EVERY PATH AT ONCE.

    `rejected` is a harder end-state than `pending`. A view that shows "unconfirmed" items
    must still not show a rejected one — otherwise a physician's removal is only a
    relabelling, and the fact reappears under a different heading.
    """
    result, encounter = await _one_fact_encounter(
        db_session, "abha:gone01", "hpi.site", "chest"
    )
    facts = await _facts(db_session, encounter.id)
    target = next(f for f in facts if f.path == "hpi.site")

    # Confirm everything first, so the only thing that changes below is the rejection.
    for fact in facts:
        await set_review_status(
            db_session, encounter=encounter, fact_ref=fact.fact_ref,
            status="confirmed", actor="dr.who", actor_role="clinician",
        )
    await db_session.flush()
    before = await H.current_features(db_session, encounter.id)
    assert "hpi.site" in before, "the fixture never had the feature to begin with"

    await set_review_status(
        db_session, encounter=encounter, fact_ref=target.fact_ref,
        status="rejected", actor="dr.who", actor_role="clinician",
    )
    await db_session.flush()

    # 1. retrieval features — the working set, which admits pending and edited
    assert "hpi.site" not in await H.current_features(db_session, encounter.id)

    # 2. click-to-source — refused for a clinical reader
    assert (
        await H.evidence_for_fact(
            db_session, encounter_id=encounter.id, fact_ref=target.fact_ref
        )
        is None
    )

    # 3. ...but reachable for the auditor, whose job is seeing what was removed
    audited = await H.evidence_for_fact(
        db_session,
        encounter_id=encounter.id,
        fact_ref=target.fact_ref,
        include_rejected=True,
    )
    assert audited is not None and audited["reviewStatus"] == "rejected"

    # 4. the brief
    from app.modules.report import loader as L

    patient = await H.get_patient(db_session, patient_ref=result.patient_ref)
    rows = await L.load(db_session, patient, encounter_ref=encounter.encounter_ref)
    assert target.fact_ref not in {f.fact_ref for f in rows.facts}


async def test_pending_is_withheld_from_active_use_but_still_visible_on_review(
    db_session,
) -> None:
    """The other half: unconfirmed is shown-and-marked, not hidden."""
    result, encounter = await _one_fact_encounter(
        db_session, "abha:pend02", "hpi.site", "chest"
    )
    # Everything is `pending` straight out of promotion.
    assert "hpi.site" in await H.current_features(db_session, encounter.id), (
        "a review surface cannot review what it refuses to show"
    )

    patient = await H.get_patient(db_session, patient_ref=result.patient_ref)
    prior = await H.similar_encounters(
        db_session,
        patient_id=patient.id,
        current_features={"hpi.site": {"chest"}},
        exclude_encounter_id=None,
    )
    assert prior == [], (
        "unreviewed history was retrieved against — retrieval is a clinical claim and must "
        "rest on facts a physician signed off"
    )


async def test_prior_encounters_are_retrieved_only_once_confirmed(db_session) -> None:
    """The asymmetry in `_feature_set`, checked from both sides."""
    result, encounter = await _one_fact_encounter(
        db_session, "abha:conf01", "hpi.site", "chest"
    )
    patient = await H.get_patient(db_session, patient_ref=result.patient_ref)

    for fact in await _facts(db_session, encounter.id):
        await set_review_status(
            db_session, encounter=encounter, fact_ref=fact.fact_ref,
            status="confirmed", actor="dr.who", actor_role="clinician",
        )
    await db_session.flush()

    found = await H.similar_encounters(
        db_session,
        patient_id=patient.id,
        current_features={"hpi.site": {"chest"}},
        exclude_encounter_id=None,
    )
    assert found, "a confirmed prior encounter should now be retrievable"


async def test_the_status_sets_are_disjoint_where_it_matters() -> None:
    """A cheap guard against someone widening ACTIVE to include rejected."""
    assert "rejected" not in ACTIVE_REVIEW_STATUSES
    assert "rejected" not in REVIEWABLE_REVIEW_STATUSES
    assert ACTIVE_REVIEW_STATUSES <= REVIEWABLE_REVIEW_STATUSES


# ---------------------------------------------------------------- contradictions


def _doc_fact(ledger: FactLedger, path: str, value: str, *, document: str, page: int = 1):
    """A document-tier fact, so dosage conflicts have two real document sources."""
    return record_fact(
        ledger,
        path=path,
        value=value,
        tier=SourceTier.DOCUMENT,
        source=DocumentSpan(
            verbatim=value,
            document_id=document,
            page=page,
            bbox=BoundingBox(x=0.1, y=0.1, width=0.4, height=0.02),
            ocr_confidence=0.95,
            ocr_backend="textlayer",
        ),
        confidence=0.95,
    )


def test_the_dosage_rule_is_loaded_from_yaml() -> None:
    rule = next(r for r in load_rules().dosage_conflicts if r.id == "CX-DOSE-01")
    assert (rule.group, rule.match_field, rule.compare_field) == ("medications", "name", "dose")
    assert rule.require_different_sources is True


def test_two_documents_disagreeing_on_a_dose_is_a_contradiction() -> None:
    """The requirement's second example: conflicting dosages across two documents."""
    ledger = FactLedger("s")
    _doc_fact(ledger, "medications[0].name", "Metformin", document="doc_a")
    _doc_fact(ledger, "medications[0].dose", "500mg", document="doc_a")
    _doc_fact(ledger, "medications[1].name", "METFORMIN", document="doc_b")
    _doc_fact(ledger, "medications[1].dose", "1000mg", document="doc_b")

    conflicts = [c for c in detect(ledger) if c.kind == "dosage_conflict"]
    assert len(conflicts) == 1, [c.label for c in conflicts]
    conflict = conflicts[0]
    assert conflict.rule_id == "CX-DOSE-01"
    # Neither side wins, and both name where they came from.
    assert {conflict.patient_side.value, conflict.document_side.value} == {"500mg", "1000mg"}
    assert "doc_a" in conflict.patient_side.origin
    assert "doc_b" in conflict.document_side.origin


def test_the_same_dose_written_twice_is_not_a_conflict() -> None:
    ledger = FactLedger("s")
    _doc_fact(ledger, "medications[0].name", "Metformin", document="doc_a")
    _doc_fact(ledger, "medications[0].dose", "500mg", document="doc_a")
    _doc_fact(ledger, "medications[1].name", "Metformin", document="doc_b")
    _doc_fact(ledger, "medications[1].dose", "500MG", document="doc_b")

    assert [c for c in detect(ledger) if c.kind == "dosage_conflict"] == []


def test_one_document_disagreeing_with_itself_is_an_ocr_fault_not_a_conflict() -> None:
    """`require_different_sources`. That belongs in the verification lane, not in front of
    a physician as an unanswerable clinical question."""
    ledger = FactLedger("s")
    _doc_fact(ledger, "medications[0].name", "Metformin", document="doc_a")
    _doc_fact(ledger, "medications[0].dose", "500mg", document="doc_a")
    _doc_fact(ledger, "medications[1].name", "Metformin", document="doc_a")
    _doc_fact(ledger, "medications[1].dose", "850mg", document="doc_a")

    assert [c for c in detect(ledger) if c.kind == "dosage_conflict"] == []


def test_a_missing_dose_is_a_gap_not_a_disagreement() -> None:
    """An absence is not a conflicting value; asking about one nobody claimed is noise."""
    ledger = FactLedger("s")
    _doc_fact(ledger, "medications[0].name", "Metformin", document="doc_a")
    _doc_fact(ledger, "medications[0].dose", "500mg", document="doc_a")
    _doc_fact(ledger, "medications[1].name", "Metformin", document="doc_b")

    assert [c for c in detect(ledger) if c.kind == "dosage_conflict"] == []


def test_every_contradiction_carries_its_kind() -> None:
    """The discriminator that lets a reader stop inferring shape from field names."""
    ledger = FactLedger("s")
    tap(ledger, "drug_allergy.taking_medicines", False)
    _doc_fact(ledger, "medications[0].name", "Metformin", document="doc_a")
    kinds = {c.kind for c in detect(ledger)}
    assert kinds, "the fixture produced no contradiction at all"
    assert kinds <= {"denial", "cross_tier", "dosage_conflict"}


async def test_cross_encounter_contradictions_are_persisted(db_session) -> None:
    """⛔ GAP 2. These were computed on every read and stored nowhere.

    A patient denying medicines while their own record holds a documented prescription is the
    single most clinically interesting disagreement this system finds, and it was the one kind
    that never reached the durable record, the brief or the audit trail — because the only
    writer of `ContradictionRecord` was the in-ledger detector, which by construction cannot
    see a previous visit.
    """
    # Visit 1: a documented prescription.
    ledger_one = FactLedger("s1")
    tap(ledger_one, "chief_complaint.text", "review")
    _doc_fact(ledger_one, "medications[0].name", "Metformin", document="doc_a")
    _doc_fact(ledger_one, "medications[0].dose", "500mg", document="doc_a")
    first = await _promote(
        db_session, await _session(db_session, abha_ref="abha:cross01"), ledger_one
    )
    encounter_one = await _encounter(db_session, first.encounter_ref)
    for fact in await _facts(db_session, encounter_one.id):
        await set_review_status(
            db_session, encounter=encounter_one, fact_ref=fact.fact_ref,
            status="confirmed", actor="dr.who", actor_role="clinician",
        )
    await db_session.flush()

    # Visit 2: the patient says they take nothing.
    second_session = IntakeSession(
        session_ref="sess_cross02", abha_ref="abha:cross01", consent_ref="c",
        language="en", status="ready_for_review", created_at=datetime.now(UTC),
    )
    db_session.add(second_session)
    await db_session.flush()
    ledger_two = FactLedger("s2")
    tap(ledger_two, "chief_complaint.text", "review")
    tap(ledger_two, "drug_allergy.taking_medicines", False)
    second = await _promote(db_session, second_session, ledger_two)
    await db_session.flush()

    encounter_two = await _encounter(db_session, second.encounter_ref)
    rows = list(
        (
            await db_session.execute(
                select(ContradictionRecord).where(
                    ContradictionRecord.encounter_id == encounter_two.id,
                    ContradictionRecord.scope == "cross_encounter",
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows, "a cross-visit disagreement was found and not persisted"
    assert all(r.status == "open" for r in rows), "a contradiction was auto-resolved"
    assert all(r.side_a_json and r.side_b_json for r in rows), "a side was dropped"


async def test_in_encounter_contradictions_keep_their_scope(db_session) -> None:
    """Both scopes land in one table and must remain tellable apart."""
    ledger = FactLedger("s")
    tap(ledger, "chief_complaint.text", "pain")
    tap(ledger, "drug_allergy.taking_medicines", False)
    _doc_fact(ledger, "medications[0].name", "Metformin", document="doc_a")

    history = project(ledger, demographics=Demographics(age_years=52, gender="male"))
    result = await promote(
        db_session,
        session_row=await _session(db_session, abha_ref="abha:inenc01"),
        ledger=ledger,
        history=history,
        escalation=evaluate(ledger),
        contradictions=detect(ledger),
        summary_payload={"status": "draft"},
        traceable=True,
        confirmed_by="dr.test",
    )
    await db_session.flush()
    encounter = await _encounter(db_session, result.encounter_ref)
    rows = list(
        (
            await db_session.execute(
                select(ContradictionRecord).where(
                    ContradictionRecord.encounter_id == encounter.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows
    assert any(r.scope == "in_encounter" for r in rows)
