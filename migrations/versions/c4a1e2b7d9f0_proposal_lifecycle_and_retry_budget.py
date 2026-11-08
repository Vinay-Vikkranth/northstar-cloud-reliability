"""Proposal lifecycle and repair retry budget.

Revision ID: c4a1e2b7d9f0
Revises: f4007edc4bf0
"""

from alembic import op
import sqlalchemy as sa

revision = "c4a1e2b7d9f0"
down_revision = "f4007edc4bf0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proposals", sa.Column("decision_state", sa.String(length=20), server_default="open", nullable=False))
    op.create_check_constraint(
        "ck_proposal_decision",
        "proposals",
        "decision_state IN ('open', 'approved', 'rejected', 'cancelled', 'superseded')",
    )
    op.add_column("repair_intents", sa.Column("dispatch_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("repair_intents", sa.Column("retry_deadline", sa.DateTime(timezone=True), nullable=True))
    op.execute("DROP TRIGGER IF EXISTS proposals_append_only ON proposals")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION cir_protect_proposal() RETURNS trigger AS $$
        BEGIN
          IF NEW.before_value IS DISTINCT FROM OLD.before_value
             OR NEW.after_value IS DISTINCT FROM OLD.after_value
             OR NEW.proposal_hash IS DISTINCT FROM OLD.proposal_hash
             OR NEW.source_preconditions IS DISTINCT FROM OLD.source_preconditions
             OR NEW.destination_preconditions IS DISTINCT FROM OLD.destination_preconditions
             OR NEW.evidence_ids IS DISTINCT FROM OLD.evidence_ids
             OR NEW.field_name IS DISTINCT FROM OLD.field_name
             OR NEW.mapping_version IS DISTINCT FROM OLD.mapping_version
             OR NEW.customer_link_id IS DISTINCT FROM OLD.customer_link_id
             OR NEW.connector_id IS DISTINCT FROM OLD.connector_id
             OR NEW.version IS DISTINCT FROM OLD.version
             OR NEW.incident_id IS DISTINCT FROM OLD.incident_id
             OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
             OR NEW.investigation_run_id IS DISTINCT FROM OLD.investigation_run_id THEN
            RAISE EXCEPTION 'proposal content is immutable';
          END IF;
          IF OLD.decision_state <> 'open'
             AND NOT (OLD.decision_state = 'approved' AND NEW.decision_state = 'rejected')
             AND NEW.decision_state IS DISTINCT FROM OLD.decision_state THEN
            RAISE EXCEPTION 'proposal decision is terminal';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER proposals_content_immutable BEFORE UPDATE ON proposals "
        "FOR EACH ROW EXECUTE FUNCTION cir_protect_proposal()"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_diagnosis_investigation ON diagnoses (investigation_run_id)"
    )
    op.execute("GRANT UPDATE ON proposals TO cir_app")
    op.execute("REVOKE DELETE ON proposals FROM cir_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS proposals_content_immutable ON proposals")
    op.execute("DROP FUNCTION IF EXISTS cir_protect_proposal()")
    op.execute("DROP INDEX IF EXISTS uq_diagnosis_investigation")
    op.drop_constraint("ck_proposal_decision", "proposals", type_="check")
    op.drop_column("repair_intents", "retry_deadline")
    op.drop_column("repair_intents", "dispatch_attempts")
    op.drop_column("proposals", "decision_state")
