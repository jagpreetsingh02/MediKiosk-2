"""The physician-review axis, durable fact origin, and contradiction scope.

Session 3. Three changes, none of which adds a table, and one of which needs its backfill
explained rather than just performed.

═══ 1. `review_status` — A THIRD AXIS, NOT A BOOLEAN ═══

    review_status  pending | confirmed | rejected | edited
    reviewed_by    who acted
    reviewed_at    when

`clinical_fact` already had `confirmed_by_physician`, a boolean, and it could not express the
distinction this session is about: a fact nobody has looked at yet and a fact a physician
explicitly threw out are not the same clinical statement, and a boolean flattens them into
one `False`. `pending` and `edited` are withheld from ACTIVE clinical use but still shown,
marked, on review surfaces. `rejected` appears in no clinical view at all and survives only
for the audit trail.

That boolean was also never written by `promote()` — only by `seed.py` — so all 805 real
facts carried `False` while being fully committed. It is kept, because `report/brief.py` puts
it on the wire as `confirmedByPhysician` and the frontend is about to be written against that
payload, but it is now DERIVED from `review_status` and nothing branches on it.

⛔ BACKFILL: EXISTING ROWS BECOME `confirmed`, AND `pending` WOULD HAVE BEEN WRONG.

Every existing `clinical_fact` row reached the table through `promote()`, which runs only
from the `summary.commit` route, which Invariant 4 restricts to a clinician sending an
explicit `confirmed: true`. A physician therefore signed off on every one of them. Marking
them `pending` would be untrue, and it would also silently empty every active clinical view
for every existing patient the moment this deployed — a data migration that makes a
patient's history disappear is worse than one that fails loudly.

There is deliberately NO `server_default`. The same reasoning `e42b36db0938` gives for
`state`: a default left on the column means every future insert acquires a review status
nobody chose. New rows get `pending` from the model's Python-side default, which is the
honest answer for a fact that has just been promoted and not yet reviewed.

═══ 2. `origin`, and the two things a tier cannot say ═══

    clinical_fact.origin           patient_stated | document | prior_encounter | physician_entered
    source_evidence.prior_encounter_ref   which earlier visit a carried-forward fact came from

Invariant 2 fixes the capture-side tiers at exactly three, and a test asserts it, because a
fourth tier would be a place to hide an inference during an interview. The durable side has
two further questions to answer — was this carried forward from an earlier visit, was it
typed by a physician — and it already diverges from `SourceTier` for exactly this reason
(`FACT_STATES` has six values against three tiers). So origin lives here and `SourceTier` is
untouched.

`source_evidence.source_type` gains `prior_encounter` and `physician` WITHOUT a type change:
the longest new value is 15 characters and the column is `String(16)`. Deliberate — an ALTER
of a column type is the one operation here that would need SQLite batch mode, and
`tests/test_migrations.py` builds this whole chain on SQLite.

Backfilled from `tier`, which is the only truthful source available: a document-tier fact
originated in a document, and everything else was said by the patient. No row is guessed
into `prior_encounter` or `physician_entered` — nothing before this migration could produce
either.

═══ 3. `contradiction_record.scope` ═══

    in_encounter | cross_encounter

Both detectors already existed and only one was ever stored. `contracts.contradictions.detect`
runs over the capture ledger and its findings are persisted at promotion;
`history.reconcile_live_session` compares today's answers against the patient's own record,
and its findings reached one read endpoint and then evaporated. The cross-visit case is the
more clinically interesting of the two — "you told me no medicines, your own file says
metformin" — and it was the one that never reached the durable record or the audit trail.

Existing rows are all `in_encounter`, because that is the only kind anything has ever written.

═══ RLS ═══

No new tables, so `fdb61bb8d5ef`'s lockdown still covers everything and
`test_rls_is_enabled_for_every_table_the_schema_owns` stays green. Verified against the live
database before writing this: all six affected tables report `relrowsecurity = true` with
zero policies, which is deny-all for `anon`/`authenticated`. RLS is table-level, no policy
expression exists that could reference a new column, and `ADD COLUMN` does not touch
`relrowsecurity` — so adding columns cannot weaken it.

Revision ID: b4e1a7c93d52
Revises: c94a3c4e2107
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b4e1a7c93d52"
down_revision: str | None = "c94a3c4e2107"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ---- 1. the physician-review axis ---------------------------------------
    #
    # ADDED NULLABLE, BACKFILLED, THEN MADE NOT NULL — the pattern e42b36db0938 established
    # here. A bare NOT NULL ADD COLUMN aborts on a populated table, and a `server_default`
    # would sit on the column forever, handing every future insert a status nobody chose.
    op.add_column("clinical_fact", sa.Column("review_status", sa.String(length=16), nullable=True))
    op.execute("UPDATE clinical_fact SET review_status = 'confirmed' WHERE review_status IS NULL")
    op.alter_column("clinical_fact", "review_status", nullable=False)

    op.add_column("clinical_fact", sa.Column("reviewed_by", sa.String(length=255), nullable=True))
    op.add_column(
        "clinical_fact", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )

    # The derived boolean is brought into agreement with the axis it now shadows. It was
    # never written by promote(), so it is `False` on rows a physician demonstrably confirmed.
    op.execute("UPDATE clinical_fact SET confirmed_by_physician = true WHERE review_status = 'confirmed'")

    op.create_index(
        op.f("ix_clinical_fact_review_status"), "clinical_fact", ["review_status"], unique=False
    )

    # ---- 2. durable fact origin ---------------------------------------------
    op.add_column("clinical_fact", sa.Column("origin", sa.String(length=32), nullable=True))
    # From `tier`, the only truthful source available. Nothing before this migration could
    # produce a prior_encounter or physician_entered fact, so nothing is guessed into one.
    op.execute("UPDATE clinical_fact SET origin = 'document' WHERE tier = 'document'")
    op.execute("UPDATE clinical_fact SET origin = 'patient_stated' WHERE origin IS NULL")
    op.alter_column("clinical_fact", "origin", nullable=False)

    op.add_column(
        "source_evidence", sa.Column("prior_encounter_ref", sa.String(length=64), nullable=True)
    )
    op.create_index(
        op.f("ix_source_evidence_prior_encounter_ref"),
        "source_evidence",
        ["prior_encounter_ref"],
        unique=False,
    )

    # ---- 3. contradiction scope ---------------------------------------------
    op.add_column("contradiction_record", sa.Column("scope", sa.String(length=16), nullable=True))
    op.execute("UPDATE contradiction_record SET scope = 'in_encounter' WHERE scope IS NULL")
    op.alter_column("contradiction_record", "scope", nullable=False)
    op.create_index(
        op.f("ix_contradiction_record_scope"), "contradiction_record", ["scope"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_contradiction_record_scope"), table_name="contradiction_record")
    op.drop_column("contradiction_record", "scope")

    op.drop_index(op.f("ix_source_evidence_prior_encounter_ref"), table_name="source_evidence")
    op.drop_column("source_evidence", "prior_encounter_ref")

    op.drop_column("clinical_fact", "origin")

    op.drop_index(op.f("ix_clinical_fact_review_status"), table_name="clinical_fact")
    op.drop_column("clinical_fact", "reviewed_at")
    op.drop_column("clinical_fact", "reviewed_by")
    op.drop_column("clinical_fact", "review_status")
