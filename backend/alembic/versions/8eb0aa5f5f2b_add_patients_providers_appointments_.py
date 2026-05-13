"""add_patients_providers_appointments_events_notifications_waitlist_subscriptions

Revision ID: 8eb0aa5f5f2b
Revises: cf7eb24fa195
Create Date: 2026-05-10 07:10:01.577897

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '8eb0aa5f5f2b'
down_revision: Union[str, Sequence[str], None] = 'cf7eb24fa195'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All tenant-scoped tables that need RLS — includes the two tables from the
# previous migration (users, refresh_tokens) that were missing policies.
_RLS_TABLES = [
    "users",
    "refresh_tokens",
    "patients",
    "providers",
    "appointments",
    "appointment_events",
    "notifications",
    "waitlist",
    "subscriptions",
]


def upgrade() -> None:
    """Upgrade schema."""
    # ── New tables ────────────────────────────────────────────────────────────
    op.create_table(
        'patients',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('full_name', sa.Text(), nullable=False),
        sa.Column('phone', sa.Text(), nullable=False),
        sa.Column('email', sa.Text(), nullable=True),
        sa.Column('dob', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_patients_tenant_id'), 'patients', ['tenant_id'], unique=False)

    op.create_table(
        'subscriptions',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('stripe_subscription_id', sa.Text(), nullable=False),
        sa.Column('stripe_price_id', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sms_usage_count', sa.Integer(), nullable=False),
        sa.Column('sms_usage_limit', sa.Integer(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stripe_subscription_id'),
        sa.UniqueConstraint('tenant_id', name='uq_subscriptions_tenant_id'),
    )

    op.create_table(
        'providers',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('specialty', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_providers_tenant_id'), 'providers', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_providers_user_id'), 'providers', ['user_id'], unique=False)

    op.create_table(
        'appointments',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('patient_id', sa.UUID(), nullable=False),
        sa.Column('provider_id', sa.UUID(), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duration_minutes', sa.Integer(), nullable=False),
        sa.Column('appointment_type', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('risk_score', sa.Integer(), nullable=True),
        sa.Column('risk_bucket', sa.Text(), nullable=True),
        sa.Column('last_scored_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_appointments_patient_id'), 'appointments', ['patient_id'], unique=False)
    op.create_index(op.f('ix_appointments_provider_id'), 'appointments', ['provider_id'], unique=False)
    op.create_index(op.f('ix_appointments_tenant_id'), 'appointments', ['tenant_id'], unique=False)
    op.create_index('ix_appointments_tenant_scheduled', 'appointments', ['tenant_id', 'scheduled_at'], unique=False)
    op.create_index('ix_appointments_tenant_status', 'appointments', ['tenant_id', 'status'], unique=False)

    op.create_table(
        'waitlist',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('patient_id', sa.UUID(), nullable=False),
        sa.Column('provider_id', sa.UUID(), nullable=True),
        sa.Column('preferred_days', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column('preferred_times', sa.Text(), nullable=False),
        sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('filled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_waitlist_patient_id'), 'waitlist', ['patient_id'], unique=False)
    op.create_index(op.f('ix_waitlist_tenant_id'), 'waitlist', ['tenant_id'], unique=False)

    op.create_table(
        'appointment_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('appointment_id', sa.UUID(), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('actor_id', sa.UUID(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['appointment_id'], ['appointments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_appointment_events_appointment_id'), 'appointment_events', ['appointment_id'], unique=False)
    op.create_index(op.f('ix_appointment_events_tenant_id'), 'appointment_events', ['tenant_id'], unique=False)

    op.create_table(
        'notifications',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('appointment_id', sa.UUID(), nullable=False),
        sa.Column('channel', sa.Text(), nullable=False),
        sa.Column('direction', sa.Text(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('twilio_sid', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['appointment_id'], ['appointments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_notifications_appointment_id'), 'notifications', ['appointment_id'], unique=False)
    op.create_index(op.f('ix_notifications_tenant_id'), 'notifications', ['tenant_id'], unique=False)

    # ── Row-Level Security ────────────────────────────────────────────────────
    # Applied to all tenant-scoped tables, including users and refresh_tokens
    # which were created in the previous migration without policies.
    #
    # Policy: a session can only see rows where tenant_id matches the value set
    # by the application via:
    #   SELECT set_config('app.current_tenant', '<uuid>', true)
    #
    # NULLIF handles the case where the setting is unset (returns '' not NULL),
    # preventing a cast error and making unscoped sessions see zero rows.
    # FORCE ROW LEVEL SECURITY makes the policy apply to the table owner too
    # (superusers still bypass RLS via BYPASSRLS — used by test fixtures).
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)"
        )


def downgrade() -> None:
    """Downgrade schema."""
    # ── Remove RLS policies ───────────────────────────────────────────────────
    for table in reversed(_RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # ── Drop new tables (reverse dependency order) ────────────────────────────
    op.drop_index(op.f('ix_notifications_tenant_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_appointment_id'), table_name='notifications')
    op.drop_table('notifications')
    op.drop_index(op.f('ix_appointment_events_tenant_id'), table_name='appointment_events')
    op.drop_index(op.f('ix_appointment_events_appointment_id'), table_name='appointment_events')
    op.drop_table('appointment_events')
    op.drop_index(op.f('ix_waitlist_tenant_id'), table_name='waitlist')
    op.drop_index(op.f('ix_waitlist_patient_id'), table_name='waitlist')
    op.drop_table('waitlist')
    op.drop_index('ix_appointments_tenant_status', table_name='appointments')
    op.drop_index('ix_appointments_tenant_scheduled', table_name='appointments')
    op.drop_index(op.f('ix_appointments_tenant_id'), table_name='appointments')
    op.drop_index(op.f('ix_appointments_provider_id'), table_name='appointments')
    op.drop_index(op.f('ix_appointments_patient_id'), table_name='appointments')
    op.drop_table('appointments')
    op.drop_index(op.f('ix_providers_user_id'), table_name='providers')
    op.drop_index(op.f('ix_providers_tenant_id'), table_name='providers')
    op.drop_table('providers')
    op.drop_table('subscriptions')
    op.drop_index(op.f('ix_patients_tenant_id'), table_name='patients')
    op.drop_table('patients')
