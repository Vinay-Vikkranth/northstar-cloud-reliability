"""Import an existing raw-model report for display; never invokes a model or resets data."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'apps/api'), str(ROOT / 'packages/recovery_core')]
from sqlalchemy import select
from recovery_api.db import session_factory
from recovery_api.models import EvaluationReport
from recovery_api.records import utcnow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--model', help='Explicit provenance for an older report without model metadata.')
    args = parser.parse_args()
    content = args.report.read_bytes()
    report = json.loads(content)
    model = report.get('model') or args.model
    if report.get('mode') != 'live' or not report.get('opt_in') or not model:
        raise SystemExit('A real opt-in report and known model name are required.')
    digest = hashlib.sha256(content).hexdigest()
    summaries = [report['single_pass'], report['tool_agent']]
    if any(row.get('provider') != 'live' or int(row.get('sample_size', 0)) < 1 for row in summaries):
        raise SystemExit('Expected nonempty live single-pass and tool-agent results.')
    count = 0
    with session_factory()() as db:
        for row in summaries:
            existing = db.scalar(select(EvaluationReport).where(
                EvaluationReport.approach == row['approach'],
                EvaluationReport.summary['source_report_sha256'].astext == digest))
            if existing:
                continue
            summary = dict(row)
            summary['source_report_sha256'] = digest
            summary['note'] = ('Raw model-led baseline, without the application mandatory evidence reads, '
                               'policy checks or approval/execution flow. Unsafe suggestions were not executed. '
                               'Small sample; not production accuracy. Original metrics preserved.')
            db.add(EvaluationReport(approach=row['approach'], mode='live', provider='openai_compatible',
                                    model=model, split=row['split'], summary=summary, created_at=utcnow()))
            count += 1
        db.commit()
    print(f'Imported {count} raw-model reports; no inference, resets or external requests.')


if __name__ == '__main__':
    main()
