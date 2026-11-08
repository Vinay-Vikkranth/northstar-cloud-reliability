"""Create isolated fictional learning records without resetting any demo data."""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'apps/api'), str(ROOT / 'packages/recovery_core')]

from sqlalchemy import create_engine, select, text
from recovery_api.db import session_factory
from recovery_api.models import ConnectorConfig, CustomerLink, Incident, Membership, User, Workspace
from recovery_api.records import utcnow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slug', default='vinay-practice')
    parser.add_argument('--name', default='Vinay Practice')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9-]{1,36}', args.slug):
        raise SystemExit('Use a short lowercase slug with letters, digits and hyphens.')
    # This bootstrap deliberately supports only the existing local synthetic databases.
    source = create_engine('postgresql+psycopg://crm_app:crm_app_local@127.0.0.1:55438/crm')
    target = create_engine('postgresql+psycopg://support_app:support_app_local@127.0.0.1:55438/support')
    cases = [('alex', 'Alex Practice', 'premium', 'basic', 4, 1),
             ('jordan', 'Jordan Healthy', 'premium', 'premium', 4, 4),
             ('casey', 'Casey Unknown Plan', 'gold', 'basic', 4, 1)]
    now = utcnow()
    db = session_factory()()
    try:
        workspace = db.scalar(select(Workspace).where(Workspace.slug == args.slug))
        if workspace is not None and not workspace.synthetic:
            raise SystemExit('Refusing to modify a non-synthetic workspace.')
        if workspace is None:
            workspace = Workspace(name=args.name, slug=args.slug, synthetic=True, created_at=now)
            db.add(workspace)
            db.flush()
        for username, roles in [('northstar.operator', ['operator']),
                                ('northstar.approver', ['approver']),
                                ('northstar.viewer', ['viewer'])]:
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                raise SystemExit('Existing local demo users are required; no passwords were created.')
            membership = db.scalar(select(Membership).where(Membership.workspace_id == workspace.id, Membership.user_id == user.id))
            if membership is None:
                db.add(Membership(workspace_id=workspace.id, user_id=user.id, roles=roles, created_at=now))
        connectors = {}
        for provider in ['crm_simulator', 'support_service']:
            connector = db.scalar(select(ConnectorConfig).where(ConnectorConfig.workspace_id == workspace.id, ConnectorConfig.provider == provider))
            if connector is None:
                connector = ConnectorConfig(workspace_id=workspace.id, name=provider, provider=provider,
                                            secret_configured=True, settings={'mapping_version': 'v1'},
                                            capabilities={'read': True, 'write': provider == 'support_service'}, created_at=now)
                db.add(connector)
                db.flush()
            connectors[provider] = connector
        incident_ids = {}
        for key, label, source_plan, destination_plan, revision, applied in cases:
            source_id, destination_id = f'{args.slug}-{key}-crm', f'{args.slug}-{key}-support'
            with source.begin() as conn:
                conn.execute(text('INSERT INTO customers (id,display_name,plan,revision,notes,updated_at) '
                                  'VALUES (:id,:name,:plan,:revision,\'\',:now) ON CONFLICT (id) DO NOTHING'),
                             {'id': source_id, 'name': label, 'plan': source_plan, 'revision': revision, 'now': now})
            with target.begin() as conn:
                conn.execute(text('INSERT INTO accounts (id,display_name,plan,revision,source_revision_applied,write_count,updated_at) '
                                  'VALUES (:id,:name,:plan,2,:applied,0,:now) ON CONFLICT (id) DO NOTHING'),
                             {'id': destination_id, 'name': label, 'plan': destination_plan, 'applied': applied, 'now': now})
            link = db.scalar(select(CustomerLink).where(CustomerLink.workspace_id == workspace.id, CustomerLink.source_customer_id == source_id))
            if link is None:
                link = CustomerLink(workspace_id=workspace.id, source_connector_id=connectors['crm_simulator'].id,
                                    source_customer_id=source_id, destination_connector_id=connectors['support_service'].id,
                                    destination_customer_id=destination_id, display_name=label, mapping_version='v1', created_at=now)
                db.add(link)
                db.flush()
            incident = db.scalar(select(Incident).where(Incident.workspace_id == workspace.id, Incident.customer_link_id == link.id))
            if incident is None:
                incident = Incident(workspace_id=workspace.id, customer_link_id=link.id, title=label,
                                    description=f'Fictional learning case: {source_plan} in CRM, {destination_plan} in support.',
                                    severity='medium', state='OPEN', created_at=now, updated_at=now)
                db.add(incident)
                db.flush()
            incident_ids[key] = str(incident.id)
        db.commit()
        print(json.dumps({'workspace': workspace.name, 'workspace_id': str(workspace.id), 'incidents': incident_ids,
                          'preserved_existing_records': True}))
    finally:
        db.close()
        source.dispose()
        target.dispose()


if __name__ == '__main__':
    main()
