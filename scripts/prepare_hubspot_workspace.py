"""Link one explicitly confirmed fictional HubSpot contact to a new local support fixture.

Reads HubSpot only. Never resets existing accounts or modifies HubSpot.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'apps/api'), str(ROOT / 'packages/recovery_core')]
from sqlalchemy import create_engine, select, text
from recovery_api.db import session_factory
from recovery_api.hubspot import HubSpotClient
from recovery_api.models import ConnectorConfig, CustomerLink, Incident, Membership, User, Workspace
from recovery_api.records import utcnow
from recovery_api.settings import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-fictional-contact', required=True,
                        help='Must exactly match HUBSPOT_CONTACT_ID from the dedicated test account.')
    args = parser.parse_args()
    settings = get_settings()
    if not settings.hubspot_token or args.confirm_fictional_contact != settings.hubspot_contact_id:
        raise SystemExit('Configure the approved test contact and explicitly confirm its id.')
    client = HubSpotClient(settings.hubspot_token, plan_property=settings.hubspot_plan_property)
    try:
        record = client.get_contact(settings.hubspot_contact_id)
    finally:
        client.close()
    if record.get('status') != 'ok' or record.get('plan') != 'premium':
        raise SystemExit('Fixture requires a readable fictional premium contact. No local records changed.')
    now = utcnow()
    target = create_engine('postgresql+psycopg://support_app:support_app_local@127.0.0.1:55438/support')
    target_id = 'hubspot-demo-' + settings.hubspot_contact_id
    with target.begin() as conn:
        conn.execute(text("INSERT INTO accounts (id,display_name,plan,revision,source_revision_applied,write_count,updated_at) "
                          "VALUES (:id,'Alex Recovery Demo','basic',1,NULL,0,:now) ON CONFLICT (id) DO NOTHING"),
                     {'id': target_id, 'now': now})
    target.dispose()
    with session_factory()() as db:
        workspace = db.scalar(select(Workspace).where(Workspace.slug == 'hubspot-live-demo'))
        if workspace is None:
            workspace = Workspace(name='HubSpot + Local AI', slug='hubspot-live-demo', synthetic=False, created_at=now)
            db.add(workspace)
            db.flush()
        for role in ('operator', 'approver', 'viewer'):
            user = db.scalar(select(User).where(User.username == 'northstar.' + role))
            if user is None:
                raise SystemExit('Existing local demo users required.')
            if not db.scalar(select(Membership).where(Membership.workspace_id == workspace.id, Membership.user_id == user.id)):
                db.add(Membership(workspace_id=workspace.id, user_id=user.id, roles=[role], created_at=now))
        connectors = {}
        for provider in ('hubspot', 'support_service'):
            row = db.scalar(select(ConnectorConfig).where(ConnectorConfig.workspace_id == workspace.id, ConnectorConfig.provider == provider))
            if row is None:
                row = ConnectorConfig(workspace_id=workspace.id, name=provider, provider=provider,
                                      secret_configured=True, settings={'mapping_version': 'v1', 'plan_property': settings.hubspot_plan_property},
                                      capabilities={'read': True, 'write': provider == 'support_service'}, created_at=now)
                db.add(row)
                db.flush()
            connectors[provider] = row
        link = db.scalar(select(CustomerLink).where(CustomerLink.workspace_id == workspace.id,
                                                    CustomerLink.source_customer_id == settings.hubspot_contact_id))
        if link is None:
            link = CustomerLink(workspace_id=workspace.id, source_connector_id=connectors['hubspot'].id,
                                source_customer_id=settings.hubspot_contact_id, destination_connector_id=connectors['support_service'].id,
                                destination_customer_id=target_id, display_name='Alex Recovery Demo', mapping_version='v1', created_at=now)
            db.add(link)
            db.flush()
        incident = db.scalar(select(Incident).where(Incident.workspace_id == workspace.id, Incident.customer_link_id == link.id))
        if incident is None:
            incident = Incident(workspace_id=workspace.id, customer_link_id=link.id, title='Alex Recovery Demo',
                                description='Fictional contact: HubSpot premium plan differs from local support basic plan.',
                                severity='medium', state='OPEN', created_at=now, updated_at=now)
            db.add(incident)
            db.flush()
        db.commit()
        report = {'workspace': workspace.name, 'workspace_id': str(workspace.id), 'incident_id': str(incident.id),
                  'hubspot_contact_id': settings.hubspot_contact_id, 'support_id': target_id,
                  'hubspot_writes': 0, 'preserved_existing_records': True}
        (ROOT / 'var/hubspot-verification.json').write_text(json.dumps(report, indent=2), encoding='utf8')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
