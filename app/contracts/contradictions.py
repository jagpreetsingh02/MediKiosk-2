"""Contradiction detection — two sources that disagree, neither of which is discarded.

The case this exists for: a patient says "I don't take any medicines" and the prescription in
their hand says *Metformin 500 mg BD*. Silently preferring either source is wrong. Preferring
the document treats the patient as unreliable; preferring the patient throws away a drug
interaction. Overwriting either one destroys the very thing a physician needs to see.

So nothing is overwritten and nothing wins. Both facts stay in the ledger, the disagreement is
recorded as a :class:`Contradiction`, and the physician resolves it — or asks the patient the
clarifying question the rule supplies. Resolving a clinical conflict is a clinical judgement,
and this system does not make clinical judgements (Invariant 1).

Rules live in ``data/ontology/contradictions.yaml``. This module only evaluates them.
"""
from __future__ import annotations

import functools
import hashlib
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.contracts.provenance import Fact
from app.contracts.record import FactLedger
from app.core.config import settings

Status = Literal["open", "resolved_patient", "resolved_document", "resolved_other"]


class ContradictionSide(BaseModel):
    """One half of a disagreement, carrying everything needed to render it."""

    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)

    fact_id: str
    path: str
    value: Any
    tier: str
    verbatim: str
    confidence: float
    #: "the patient" / "a document they uploaded" — plain words for the physician screen.
    origin: str


class Contradiction(BaseModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)

    contradiction_id: str
    rule_id: str
    label: str
    #: Which detector produced this. Added because `patient_side`/`document_side` stopped
    #: being accurate the moment document-vs-document conflicts existed: for a
    #: `dosage_conflict` BOTH sides are documents, and the field names are a leftover from
    #: when only the patient-denies-a-document case existed. The names are kept for now
    #: because `frontend/src/lib/api.ts` types against them; each side's `origin` carries the
    #: true source label, and the rename to side_a/side_b happens when that client is
    #: rewritten. Read `kind` rather than inferring the shape from the field names.
    kind: Literal["denial", "cross_tier", "dosage_conflict"] = "denial"
    patient_side: ContradictionSide
    document_side: ContradictionSide
    #: A question the kiosk (or the physician) can put to the patient to settle it.
    clarifying_question: str | None = None
    status: Status = "open"
    resolved_by: str | None = None


class DenialRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    path: str
    denies_when: dict[str, Any]
    contradicted_by_group: str | None = None
    contradicted_by_path: str | None = None
    entry_field: str | None = None
    question: str | None = None

    def is_denial(self, value: Any) -> bool:
        if "equals" in self.denies_when:
            return value == self.denies_when["equals"]
        if "contains_value" in self.denies_when:
            wanted = self.denies_when["contains_value"]
            return wanted in value if isinstance(value, list) else value == wanted
        return False


class DosageConflictRule(BaseModel):
    """Two sources stating a different dose for the same drug. Neither is preferred."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    group: str
    match_field: str
    compare_field: str
    #: When true, two readings of the SAME document are not a conflict. A prescription that
    #: appears to list one drug at two doses is an OCR fault; the verification lane already
    #: has a human reading that page, and raising it here would put an unanswerable clinical
    #: question in front of the physician instead.
    require_different_sources: bool = True
    question: str | None = None


class ContradictionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    denials: list[DenialRule]
    cross_tier_paths: list[str] = Field(default_factory=list)
    dosage_conflicts: list[DosageConflictRule] = Field(default_factory=list)


@functools.lru_cache(maxsize=1)
def load_rules() -> ContradictionRules:
    path = settings.path(settings.ontology_dir) / "contradictions.yaml"
    return ContradictionRules.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _origin(fact: Fact) -> str:
    if fact.tier.value == "document":
        document_id = getattr(fact.source, "document_id", "a document")
        page = getattr(fact.source, "page", None)
        return f"{document_id}, page {page}" if page else str(document_id)
    return "the patient, during the interview"


def _side(fact: Fact) -> ContradictionSide:
    return ContradictionSide(
        fact_id=fact.fact_id,
        path=fact.path,
        value=fact.value,
        tier=fact.tier.value,
        verbatim=fact.source.verbatim,
        confidence=fact.confidence,
        origin=_origin(fact),
    )


def _identity(rule_id: str, a: Fact, b: Fact) -> str:
    key = f"{rule_id}|{a.fact_id}|{b.fact_id}"
    return "cx_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def detect(ledger: FactLedger) -> list[Contradiction]:
    """Find every disagreement in the ledger. Pure; records nothing and changes nothing."""
    rules = load_rules()
    active = ledger.active_facts()
    by_path: dict[str, Fact] = {f.path: f for f in active}

    found: list[Contradiction] = []

    # ---- a denial contradicted by an entry that exists anyway -------------
    for rule in rules.denials:
        denial = by_path.get(rule.path)
        if denial is None or not rule.is_denial(denial.value):
            continue

        conflicting: list[Fact] = []
        if rule.contradicted_by_group and rule.entry_field:
            prefix, suffix = rule.contradicted_by_group, f".{rule.entry_field}"
            conflicting = [
                f for f in active if f.path.startswith(f"{prefix}[") and f.path.endswith(suffix)
            ]
        elif rule.contradicted_by_path:
            conflicting = [f for f in active if f.path == rule.contradicted_by_path]

        for other in conflicting:
            # A denial only conflicts with evidence from a DIFFERENT source. The same
            # utterance cannot contradict itself, and a patient who says "no medicines" and
            # then names one has simply corrected themselves — the ledger already supersedes.
            if other.source.verbatim == denial.source.verbatim:
                continue
            found.append(
                Contradiction(
                    contradiction_id=_identity(rule.id, denial, other),
                    rule_id=rule.id,
                    label=rule.label,
                    kind="denial",
                    patient_side=_side(denial),
                    document_side=_side(other),
                    clarifying_question=rule.question,
                )
            )

    # ---- the same field answered differently by two different tiers -------
    for path in rules.cross_tier_paths:
        at_path = [f for f in ledger.facts if f.path == path]
        if len(at_path) < 2:
            continue
        by_tier: dict[str, Fact] = {}
        for fact in at_path:
            by_tier.setdefault(fact.tier.value, fact)
        if len(by_tier) < 2:
            continue
        tiers = sorted(by_tier)
        first, second = by_tier[tiers[0]], by_tier[tiers[1]]
        if str(first.value) == str(second.value):
            continue
        found.append(
            Contradiction(
                contradiction_id=_identity("CX-VALUE", first, second),
                rule_id="CX-VALUE",
                label=f"Two sources give a different answer for {path}",
                kind="cross_tier",
                patient_side=_side(first),
                document_side=_side(second),
                clarifying_question=None,
            )
        )

    # ---- two documents disagreeing about a dose ---------------------------
    found.extend(_dosage_conflicts(rules, active))

    return found


def _entry_index(path: str, group: str) -> str | None:
    """`medications[2].dose` -> `2`, for the group asked about. None if it is not one."""
    if not path.startswith(f"{group}["):
        return None
    closing = path.find("]")
    return path[len(group) + 1 : closing] if closing > 0 else None


def _dosage_conflicts(rules: ContradictionRules, active: list[Fact]) -> list[Contradiction]:
    """Same drug, two sources, different dose. Reports both and prefers neither.

    Matching is on the NORMALISED name, so `TAB. METFORMIN` and `Metformin` are one drug and
    a physician is not asked to reconcile a capitalisation. The comparison is over recorded
    values only — a missing dose on one side is not a disagreement, it is a gap, and treating
    an absence as a conflicting value is how a clarifying question gets asked about something
    nobody ever claimed.
    """
    from app.modules.encounter.promote import normalize_medicine

    out: list[Contradiction] = []
    for rule in rules.dosage_conflicts:
        names: dict[str, Fact] = {}
        values: dict[str, Fact] = {}
        for fact in active:
            index = _entry_index(fact.path, rule.group)
            if index is None:
                continue
            if fact.path.endswith(f".{rule.match_field}"):
                names[index] = fact
            elif fact.path.endswith(f".{rule.compare_field}"):
                values[index] = fact

        # drug -> the entries that named it, in ledger order
        by_drug: dict[str, list[str]] = {}
        for index, name_fact in names.items():
            if index in values:
                by_drug.setdefault(normalize_medicine(str(name_fact.value)), []).append(index)

        for indices in by_drug.values():
            for position, first_index in enumerate(indices):
                for second_index in indices[position + 1 :]:
                    first, second = values[first_index], values[second_index]
                    if str(first.value).strip().casefold() == str(second.value).strip().casefold():
                        continue
                    if rule.require_different_sources and _same_source(first, second):
                        continue
                    out.append(
                        Contradiction(
                            contradiction_id=_identity(rule.id, first, second),
                            rule_id=rule.id,
                            label=f"{rule.label}: {names[first_index].value}",
                            kind="dosage_conflict",
                            patient_side=_side(first),
                            document_side=_side(second),
                            clarifying_question=rule.question,
                        )
                    )
    return out


def _same_source(a: Fact, b: Fact) -> bool:
    """Two facts read off the same document. See `require_different_sources`."""
    first = getattr(a.source, "document_id", None)
    second = getattr(b.source, "document_id", None)
    return first is not None and first == second
