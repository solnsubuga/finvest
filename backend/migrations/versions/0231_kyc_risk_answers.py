"""Add the answers a client gives to the investing questions.

Revision ID: 0231_kyc_risk_answers
Revises: 0230_kyc_next_of_kin

Each time a client answers the four questions, a row is added with the answers, their points
and the profile they give. Rows are never changed or removed by any role: the latest is the
client's profile, and the earlier ones show what was known when each investment was suggested.
The answers are about the client's plans, not who they are, so they are not sealed.

See docs/client-profile.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0231_kyc_risk_answers"
down_revision: str | None = "0230_kyc_next_of_kin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"

TABLES = """
CREATE TABLE kyc_risk_answers (
    id push_id NOT NULL,
    -- The client's ID in the identity module. No foreign key: a module owns only its own tables.
    client_id push_id NOT NULL,
    answers jsonb NOT NULL,
    score smallint NOT NULL,
    profile text NOT NULL,
    answered_at timestamptz NOT NULL,
    CONSTRAINT pk_kyc_risk_answers PRIMARY KEY (id),
    CONSTRAINT ck_kyc_risk_answers_answers CHECK (jsonb_typeof(answers) = 'object'),
    CONSTRAINT ck_kyc_risk_answers_score CHECK (score BETWEEN 0 AND 20),
    CONSTRAINT ck_kyc_risk_answers_profile
        CHECK (profile IN ('careful', 'balanced', 'long_term'))
);
CREATE INDEX ix_kyc_risk_answers_client ON kyc_risk_answers (client_id, answered_at DESC, id DESC)
"""

RULES = [
    "REVOKE ALL ON kyc_risk_answers FROM PUBLIC",
    # No UPDATE or DELETE for anyone: what the client said, and when, is part of the record.
    f"GRANT SELECT, INSERT ON kyc_risk_answers TO {APP_ROLE}",
    "ALTER TABLE kyc_risk_answers ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY kyc_risk_answers_read ON kyc_risk_answers FOR SELECT TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY kyc_risk_answers_insert ON kyc_risk_answers FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
]


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    for statement in RULES:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_risk_answers) THEN
        RAISE EXCEPTION 'refusing to drop the answers clients gave to the investing questions';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE kyc_risk_answers")
