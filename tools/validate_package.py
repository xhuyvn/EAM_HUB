"""Validate contracts, fixtures and package-level semantic invariants.

Run from any working directory:  python tools/validate_package.py
Checks are structural/semantic only. They do not prove runtime security,
recovery, vendor compatibility, model quality or HUD performance.
"""
import ast
import copy
import datetime as dt
import hashlib
import json
import platform
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[1]

TERMINAL = {'succeeded', 'failed', 'cancelled', 'timed_out'}
# Mirrors docs/02_RUNTIME_AND_SECURITY.md "State transitions". Keep both in sync.
ALLOWED = {
    ('queued', 'dispatching'), ('queued', 'cancelled'),
    ('dispatching', 'running'), ('dispatching', 'unknown'), ('dispatching', 'failed'),
    ('dispatching', 'cancelled'),
    ('running', 'waiting_approval'), ('waiting_approval', 'running'),
    ('running', 'succeeded'), ('running', 'failed'),
    ('running', 'cancelling'), ('waiting_approval', 'cancelling'),
    ('running', 'timed_out'), ('waiting_approval', 'timed_out'),
    ('cancelling', 'cancelled'), ('cancelling', 'succeeded'), ('cancelling', 'failed'),
    ('cancelling', 'unknown'),
    ('unknown', 'running'), ('unknown', 'waiting_approval'), ('unknown', 'succeeded'),
    ('unknown', 'failed'), ('unknown', 'cancelled'),
}
# dispatching->failed/cancelled only when the dispatch journal proves no submit reached the provider
# or the provider definitively rejected it. unknown->* only on an authoritative provider observation.
DIGEST_FIELDS = ['binding_id', 'run_id', 'task_id', 'tool_id', 'arguments_sha256', 'resource_ref',
                 'expected_resource_revision', 'policy_version', 'operation_key', 'nonce', 'expires_at']


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def ts(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def jcs(obj):
    # Equivalent to RFC 8785 JCS for the ASCII strings/integers used in these fixtures.
    # Production must use a full RFC 8785 implementation (see docs/02).
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def action_digest(proposal):
    return hashlib.sha256(jcs({k: proposal[k] for k in DIGEST_FIELDS})).hexdigest()


def semantic_checks(kind, value):
    if kind == 'event':
        if value['event_type'] == 'run.progress':
            p = value['payload']
            if p['total_units'] is not None and p['completed_units'] is not None and p['completed_units'] > p['total_units']:
                raise ValueError('progress exceeds denominator')
        if value['event_type'] == 'run.state_changed':
            p = value['payload']
            if p['from_state'] in TERMINAL:
                raise ValueError('terminal run state cannot be left')
            if (p['from_state'], p['to_state']) not in ALLOWED:
                raise ValueError('illegal run transition %s->%s' % (p['from_state'], p['to_state']))
        if value['event_type'] == 'usage.reported':
            p = value['payload']
            if (p['cost_status'] == 'unknown') != (p['cost_usd'] is None):
                raise ValueError('usage cost_status/cost_usd mismatch')
    if kind == 'result':
        usage = value['usage']
        if usage['cost_status'] == 'unknown' and usage['cost_usd'] is not None:
            raise ValueError('unknown cost must be null')
        if usage['cost_status'] != 'unknown' and usage['cost_usd'] is None:
            raise ValueError('known or partial cost needs numeric value')
    if kind == 'binding':
        caps = value['certified_capabilities']
        if value['mode'] == 'observe' and any(caps[k] for k in ['submit', 'cancel', 'resume', 'approve_tool']):
            raise ValueError('observe binding advertises control')
        if value['mode'] == 'governed' and value['execution_boundary'] == 'external_trusted':
            raise ValueError('governed binding requires enforcement boundary')
        if value['model_profile_ref'] in value['fallback_model_profile_refs']:
            raise ValueError('fallback list repeats primary profile')
    if kind == 'experiment':
        if value['base_release_digest'] == value['candidate_release_digest']:
            raise ValueError('candidate must differ from baseline')
        if value['holdout_ref'] == value['dataset_ref']:
            raise ValueError('holdout must be isolated from optimizer dataset')
        if value['proposer_ref'] == value['reviewer_ref']:
            raise ValueError('proposer cannot review its own candidate')
        if value['rollout']['stage'] == 'canary' and value['state'] not in ('canary', 'promoted', 'reverted'):
            raise ValueError('canary rollout requires approved experiment state')
    if kind == 'ppm-output':
        crits = value['criteria']
        for c in crits:
            if c['owner_project_id'] != value['project_id']:
                raise ValueError('criterion owned by another project must be a dependency_ref')
            if c['status'] == 'verified' and (not c['supporting_evidence_refs'] or c['refuting_evidence_refs'] or c['missing_evidence']):
                raise ValueError('verified criterion needs supporting evidence and no refutation/missing evidence')
            if c['status'] == 'contradicted' and not c['refuting_evidence_refs']:
                raise ValueError('contradicted criterion needs refuting evidence')
            if c['status'] in ('verified', 'contradicted'):
                if value['assessed_revision'] is None or c['evidence_revision'] != value['assessed_revision']:
                    raise ValueError('verdict evidence must be produced at the assessed revision')
                if not c['covered_test_ids'] and c['reason_code'] in ('evidence_current_pass', 'evidence_refuted'):
                    raise ValueError('test-based verdict must name the covered baseline test ids')
            if c['status'] == 'verified' and c['reason_code'] != 'evidence_current_pass':
                raise ValueError('verified criterion must have reason evidence_current_pass')
            if set(c['supporting_evidence_refs']) & set(c['reported_claim_refs']):
                raise ValueError('reported claim used as supporting evidence')
            if c['status'] == 'verified' and c['evidence_trust'] not in ('verifier_observed', 'ci_attested'):
                raise ValueError('only verifier-observed or CI-attested evidence can verify a criterion')
            if (c['status'] == 'waived') != (c['waiver_ref'] is not None):
                raise ValueError('waived status requires an approved waiver_ref (and only waived criteria carry one)')
            if c['status'] == 'waived' and c['reason_code'] != 'waiver_approved':
                raise ValueError('waived criterion must have reason waiver_approved')
        ids = [c['criterion_id'] for c in crits]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate criterion in assessment')
        req = [c for c in crits if c['required']]
        if value['verification'] == 'verified':
            if not crits or any(c['status'] not in ('verified', 'waived') for c in req) or value['missing_evidence'] or value['validity'] != 'current':
                raise ValueError('verified project requires all required criteria verified, no missing evidence, current validity')
        if any(c['status'] == 'contradicted' for c in crits) and value['verification'] != 'contradicted':
            raise ValueError('contradicting evidence must surface as contradicted')
        if value['acceptance_readiness'] == 'ready_for_review' and value['verification'] != 'verified':
            raise ValueError('only verified assessments are ready for acceptance review')
        prog = value['progress']
        if prog['basis'] == 'none':
            if prog['verified_weight'] is not None or prog['total_weight'] is not None:
                raise ValueError('no baseline means no progress ratio')
        else:
            if value['baseline_revision'] is None:
                raise ValueError('baseline_weighted progress requires baseline_revision')
            total = sum(c['weight'] for c in crits if c['status'] != 'waived')
            ver = sum(c['weight'] for c in crits if c['status'] == 'verified')
            if prog['total_weight'] != total or prog['verified_weight'] != ver:
                raise ValueError('progress weights do not match criteria (denominator must be the baseline criteria set)')
        if prog['criteria_total'] != len(crits):
            raise ValueError('criteria_total mismatch')
    if kind == 'hud-projection':
        for row in value['rows']:
            p = row['progress']
            if p['kind'] == 'units':
                if p['total_units'] is None or p['completed_units'] is None or p['completed_units'] > p['total_units']:
                    raise ValueError('units progress needs a valid denominator')
            elif p['total_units'] is not None or p['completed_units'] is not None:
                raise ValueError('percent data present without units progress kind')
            c = row['cost']
            if (c['status'] == 'unknown') != (c['amount_usd'] is None):
                raise ValueError('cost status/amount mismatch (unknown must be null, partial is a lower bound)')
            if (row['run_id'] is None) != (row['run_state'] is None):
                raise ValueError('run_state requires run_id')
            if row['run_state'] == 'unknown' and row['remote_outcome'] != 'unknown':
                raise ValueError('unknown run must expose unknown remote outcome')
            if row['task_state'] == 'completed' and row['run_state'] not in ('succeeded', None):
                raise ValueError('completed task with non-succeeded current run')
            pr = row['project']
            if pr and pr['verified_weight'] is not None and pr['total_weight'] is not None and pr['verified_weight'] > pr['total_weight']:
                raise ValueError('project verified weight exceeds denominator')
    if kind == 'action-proposal':
        if action_digest(value) != value['action_digest']:
            raise ValueError('action digest does not match canonical proposal fields')
    if kind == 'location-registration':
        root = value['root_path']
        if not (root.startswith('/') or (len(root) > 2 and root[1] == ':' and root[2] in '/\\')):
            raise ValueError('root_path must be absolute')
        for key, aliases in value['artifact_aliases'].items():
            if len(aliases) != len(set(aliases)):
                raise ValueError('duplicate alias for ' + key)
        if value['state'] == 'active' and not value['approved_by_ref']:
            raise ValueError('active location requires approval')
    if kind == 'run-command':
        if (value['state'] == 'rejected') != (value['rejection_code'] is not None):
            raise ValueError('rejection_code only for rejected commands')


def check_approval_pair(proposal, decision):
    if decision['action_digest'] != proposal['action_digest'] or decision['nonce'] != proposal['nonce']:
        raise ValueError('approval does not bind exact proposal digest/nonce')
    if decision['proposal_id'] != proposal['proposal_id']:
        raise ValueError('approval proposal_id mismatch')
    if ts(decision['valid_until']) > ts(proposal['expires_at']):
        raise ValueError('approval outlives proposal expiry')
    cons = decision['consumption']
    if cons['state'] == 'consumed' and (cons['consumed_at'] is None or cons['consumed_by_operation_key'] != proposal['operation_key']):
        raise ValueError('consumed approval must record consuming operation')
    if decision['decision'] == 'denied' and cons['state'] == 'consumed':
        raise ValueError('denied approval cannot be consumed')


def check_operation_key_reuse(proposals):
    """Same operation_key across attempts must describe the same business action."""
    seen = {}
    for p in proposals:
        content = (p['tool_id'], p['arguments_sha256'], p['resource_ref'])
        prev = seen.setdefault(p['operation_key'], content)
        if prev != content:
            raise ValueError('OPERATION_KEY_CONFLICT: operation_key reused with different tool/arguments/target')


def check_location_set(locations):
    """Physical source identity vs project scope (ADR-019).

    - One physical folder/repository = one source_id (two source_ids with the same fingerprint or repo
      identity are a duplicate source).
    - Many project scopes may reference one source with distinct metadata_root.
    - (source_id, metadata_root) is unique and belongs to exactly one project.
    """
    source_of_identity, scopes = {}, {}
    for loc in locations:
        ident = loc['identity']
        for physical in (ident['canonical_root_fingerprint'], ident['repo_identity']):
            if physical is None:
                continue
            prev = source_of_identity.setdefault(physical, loc['source_id'])
            if prev != loc['source_id']:
                raise ValueError('duplicate source: same physical folder registered under two source_ids')
        key = (loc['source_id'], loc['metadata_root'])
        if key in scopes:
            if scopes[key] != loc['project_id']:
                raise ValueError('metadata_root registered by two projects')
            raise ValueError('duplicate location: same source and metadata_root')
        scopes[key] = loc['project_id']


def apply_assessment(current, incoming):
    """Current-assessment pointer rule (ADR-021 rev. 2): compare-and-set on basis_seq, which is assigned when
    the assessment task is created by any trigger. A result that finishes later with a lower basis_seq is history."""
    if current is None or incoming['basis_seq'] > current['basis_seq']:
        return incoming, None
    return current, incoming


def check_event_stream(events):
    seen, last_state, producer_seq = set(), {}, {}
    for event in events:
        key = (event['tenant_id'], event['producer_id'], event['event_id'])
        if key in seen:
            raise ValueError('duplicate fixture event')
        seen.add(key)
        pkey = (event['producer_id'], event['run_id'])
        if event['producer_seq'] <= producer_seq.get(pkey, 0):
            raise ValueError('producer_seq not increasing within producer/run')
        producer_seq[pkey] = event['producer_seq']
        if event['event_type'] == 'run.state_changed':
            run = event['run_id']
            if run in last_state and last_state[run] != event['payload']['from_state']:
                raise ValueError('state_changed from_state does not match previous state for ' + run)
            last_state[run] = event['payload']['to_state']


def git_revision():
    try:
        rev = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        return rev + (' (working tree has uncommitted changes)' if dirty else '')
    except Exception:
        return 'unavailable'


def build_negatives(events):
    out = []

    def neg(name, kind, data):
        out.append((name, kind, data))

    data = read_json(ROOT / 'examples/task-request.json'); data['tenant_id'] = 'spoofed'
    neg('untrusted tenant claim', 'task-request', data)
    data = copy.deepcopy(events[1]); data['payload']['completed_units'] = 13
    neg('progress overflow', 'event', data)
    data = copy.deepcopy(events[1]); data['payload']['total_units'] = 0
    neg('zero denominator', 'event', data)
    data = copy.deepcopy(events[0]); data['occurred_at'] = '2026-11-05T09:00:00'
    neg('timestamp without timezone', 'event', data)
    data = copy.deepcopy(events[0]); data['run_id'] = None
    neg('run event without run identity', 'event', data)
    data = copy.deepcopy(events[0]); data['event_type'] = 'run.state_changed'
    data['payload'] = {'from_state': 'cancelled', 'to_state': 'running', 'reason_code': 'late_event', 'evidence_ref': None}
    neg('terminal state revived by late event', 'event', copy.deepcopy(data))
    data['payload'] = {'from_state': 'unknown', 'to_state': 'queued', 'reason_code': 'blind_retry', 'evidence_ref': None}
    neg('unknown run re-queued for blind retry', 'event', copy.deepcopy(data))
    data = copy.deepcopy(events[1]); data['payload']['stage'] = 'x' * 500
    neg('unbounded stage text (log/secret leakage surface)', 'event', data)
    data = read_json(ROOT / 'examples/result.json'); data['usage']['cost_usd'] = 0
    neg('unknown cost rendered as zero', 'result', data)
    data = read_json(ROOT / 'examples/result.json'); data['usage']['cost_status'] = 'partial'
    neg('partial cost without lower bound', 'result', data)
    data = read_json(ROOT / 'templates/admin/existing-observe-binding.example.json'); data['certified_capabilities']['cancel'] = True
    neg('observe-only control escalation', 'binding', data)
    data = read_json(ROOT / 'templates/admin/existing-observe-binding.example.json'); data['mode'] = 'governed'
    neg('external boundary claims governed mode', 'binding', data)
    data = read_json(ROOT / 'templates/actions/action-proposal.example.json'); data['action_digest'] = 'invalid'
    neg('invalid action digest', 'action-proposal', data)
    data = read_json(ROOT / 'templates/actions/action-proposal.example.json'); data['expected_resource_revision'] = 'sha-other'
    neg('proposal changed after digest (approval replay onto new target)', 'action-proposal', data)
    data = read_json(ROOT / 'templates/phase2/experiment.example.json'); data['candidate_release_digest'] = data['base_release_digest']
    neg('identical optimization candidate', 'experiment', data)
    data = read_json(ROOT / 'templates/phase2/experiment.example.json'); data['holdout_ref'] = data['dataset_ref']
    neg('optimizer dataset includes holdout', 'experiment', data)
    data = read_json(ROOT / 'templates/phase2/experiment.example.json'); data['reviewer_ref'] = data['proposer_ref']
    neg('optimizer reviews its own candidate', 'experiment', data)
    data = read_json(ROOT / 'templates/phase2/experiment.example.json'); data['guardrail_metrics'] = []
    neg('cheaper candidate without quality guardrails', 'experiment', data)
    base = read_json(ROOT / 'examples/ppm-output.json')
    data = copy.deepcopy(base); data['verification'] = 'verified'
    neg('verified project with unverified required criterion', 'ppm-output', data)
    data = copy.deepcopy(base); data['acceptance_readiness'] = 'accepted'
    neg('assessment proposal claims acceptance', 'ppm-output', data)
    # Fixture roles: [0] AC-001 contradicted (note "done" + PASS rev-r1 + FAIL rev-r2), [1] AC-002 verified, [2] AC-003 claim-only
    data = copy.deepcopy(base); data['criteria'][2]['status'] = 'verified'
    neg('claim-only criterion promoted to verified', 'ppm-output', data)
    data = copy.deepcopy(base); data['progress']['total_weight'] = 2
    neg('progress denominator drops unverified criteria', 'ppm-output', data)
    data = copy.deepcopy(base); data['criteria'][0]['owner_project_id'] = 'proj-shared-gateway'
    neg('shared component counted in consumer project', 'ppm-output', data)
    data = copy.deepcopy(base); data['verification'] = 'partially_verified'
    neg('contradicting evidence hidden behind partially_verified', 'ppm-output', data)
    data = copy.deepcopy(base); data['criteria'][0].update({'status': 'verified', 'reason_code': 'evidence_current_pass',
        'supporting_evidence_refs': ['ev-r1-collector-report'], 'refuting_evidence_refs': [], 'evidence_revision': 'rev-r1'})
    data['verification'] = 'partially_verified'; data['progress']['verified_weight'] = 3
    neg('AC-001: old-commit PASS used despite current-revision FAIL', 'ppm-output', data)
    data = copy.deepcopy(base); data['criteria'][0].update({'status': 'verified', 'reason_code': 'evidence_current_pass',
        'supporting_evidence_refs': ['note:sha256-ac001-done'], 'refuting_evidence_refs': []})
    data['verification'] = 'partially_verified'; data['progress']['verified_weight'] = 3
    neg('AC-001: "done" note used as evidence', 'ppm-output', data)
    data = copy.deepcopy(base); data['criteria'][2].update({'status': 'verified', 'reason_code': 'evidence_current_pass',
        'supporting_evidence_refs': ['upd-0002'], 'missing_evidence': [], 'evidence_revision': 'rev-r2', 'covered_test_ids': ['test_x']})
    data['progress']['verified_weight'] = 2
    neg('reported claim (session update) used as evidence', 'ppm-output', data)
    data = copy.deepcopy(base); data['criteria'][1]['covered_test_ids'] = []
    neg('test PASS that does not cover the criterion mapping', 'ppm-output', data)
    data = copy.deepcopy(base); data['acceptance_readiness'] = 'ready_for_review'
    neg('acceptance review offered while required AC-001 contradicted', 'ppm-output', data)
    data = copy.deepcopy(base); data['criteria'][2].update({'status': 'verified', 'reason_code': 'evidence_current_pass',
        'supporting_evidence_refs': ['artifact:repo/reports/ac003-junit.xml'], 'reported_claim_refs': ['upd-0002'],
        'missing_evidence': [], 'evidence_revision': 'rev-r2', 'covered_test_ids': ['test_ac003'], 'evidence_trust': 'reported_artifact'})
    data['progress']['verified_weight'] = 2
    neg('repo-written test report used to verify (forged-report path)', 'ppm-output', data)
    data = copy.deepcopy(base); data['criteria'][2].update({'status': 'waived', 'reason_code': 'waiver_approved', 'waiver_ref': None})
    data['progress']['total_weight'] = 3
    neg('criterion waived without approved waiver', 'ppm-output', data)
    data = copy.deepcopy(base); data['criteria'][2].update({'status': 'waived', 'reason_code': 'waiver_approved', 'waiver_ref': 'waiver:WV-1'})
    neg('waived criterion kept in progress denominator', 'ppm-output', data)
    data = copy.deepcopy(base)
    data['progress'] = {'basis': 'none', 'verified_weight': 0, 'total_weight': None, 'criteria_total': 2, 'criteria_with_current_evidence': 1}
    neg('missing baseline rendered as zero progress', 'ppm-output', data)
    hud = read_json(ROOT / 'examples/hud-projection.json')
    data = copy.deepcopy(hud); data['rows'][1]['progress']['completed_units'] = 3
    neg('percent without denominator', 'hud-projection', data)
    data = copy.deepcopy(hud); data['rows'][1]['remote_outcome'] = 'known'
    neg('unknown run shown with known remote outcome', 'hud-projection', data)
    data = copy.deepcopy(hud); data['rows'][0]['cost']['amount_usd'] = 0
    neg('unknown HUD cost rendered as zero', 'hud-projection', data)
    data = copy.deepcopy(hud); data['rows'][1]['last_observed_at'] = '2026-11-05 09:10'
    neg('HUD observation time without timezone', 'hud-projection', data)
    loc = read_json(ROOT / 'examples/location-registration.json')
    data = copy.deepcopy(loc); data['follow_symlinks'] = True
    neg('location follows symlinks', 'location-registration', data)
    data = copy.deepcopy(loc); data['artifact_aliases']['blueprint'] = ['../other/MASTER_BLUEPRINT.md']
    neg('alias escapes root', 'location-registration', data)
    data = copy.deepcopy(loc); data['artifact_aliases']['architecture'] = ['C:/Users/x/arch.md']
    neg('absolute alias', 'location-registration', data)
    data = copy.deepcopy(loc); data['state'] = 'active'
    neg('location activated without approval', 'location-registration', data)
    cmd = read_json(ROOT / 'examples/run-command.json')
    data = copy.deepcopy(cmd); data['requested_by_ref'] = None
    neg('command without server-derived actor', 'run-command', data)
    env = read_json(ROOT / 'examples/task-envelope.json'); env.pop('lease_fencing_token')
    neg('envelope without lease fencing token', 'task-envelope', env)
    return out


def main():
    validators, checks = {}, []
    for path in sorted((ROOT / 'contracts').glob('*.schema.json')):
        schema = read_json(path)
        Draft202012Validator.check_schema(schema)
        validators[path.name.removesuffix('.schema.json')] = Draft202012Validator(schema, format_checker=FormatChecker())
    fixtures = {
        'templates/new-agent/agent.json': 'agent',
        'templates/existing-agent/agent.json': 'agent',
        'templates/admin/deployment-binding.example.json': 'binding',
        'templates/admin/existing-observe-binding.example.json': 'binding',
        'templates/actions/action-proposal.example.json': 'action-proposal',
        'templates/phase2/experiment.example.json': 'experiment',
        'examples/task-request.json': 'task-request',
        'examples/task-envelope.json': 'task-envelope',
        'examples/result.json': 'result',
        'examples/ppm-input.json': 'ppm-input',
        'examples/ppm-output.json': 'ppm-output',
        'examples/hud-projection.json': 'hud-projection',
        'examples/approval-decision.json': 'approval-decision',
        'examples/location-registration.json': 'location-registration',
        'examples/run-command.json': 'run-command',
    }

    def validate(kind, data):
        validators[kind].validate(data)
        semantic_checks(kind, data)

    for file, kind in fixtures.items():
        validate(kind, read_json(ROOT / file))
        checks.append(file + ' passed')
    events = [json.loads(line) for line in (ROOT / 'examples/events.ndjson').read_text(encoding='utf-8').splitlines() if line.strip()]
    for event in events:
        validate('event', event)
    check_event_stream(events)
    checks.append('typed event fixtures passed (%d events, transitions legal, producer_seq ordered)' % len(events))
    proposal = read_json(ROOT / 'templates/actions/action-proposal.example.json')
    decision = read_json(ROOT / 'examples/approval-decision.json')
    check_approval_pair(proposal, decision)
    checks.append('approval decision binds exact proposal digest/nonce/expiry')

    negatives = build_negatives(events)
    for name, kind, data in negatives:
        rejected = False
        try:
            validate(kind, data)
        except (ValueError, ValidationError):
            rejected = True
        if not rejected:
            raise AssertionError('Negative case accepted: ' + name)
        checks.append(name + ' correctly rejected')

    waived = read_json(ROOT / 'examples/ppm-output.json')
    waived['criteria'][2].update({'status': 'waived', 'reason_code': 'waiver_approved', 'waiver_ref': 'waiver:WV-1',
                                  'missing_evidence': []})
    waived['progress']['total_weight'] = 3
    validate('ppm-output', waived)
    checks.append('approved waiver excluded from denominator (verified 1 / total 3) accepted')

    pair_negatives = []
    d = copy.deepcopy(decision); d['nonce'] = 'nonce-replayed-000000000'
    pair_negatives.append(('approval replay with different nonce', d))
    d = copy.deepcopy(decision); d['valid_until'] = '2026-11-05T11:00:00Z'
    pair_negatives.append(('approval outlives proposal', d))
    d = copy.deepcopy(decision)
    d['consumption'] = {'state': 'consumed', 'consumed_at': '2026-11-05T09:21:00Z', 'consumed_by_operation_key': 'op-other'}
    pair_negatives.append(('approval consumed by different operation', d))
    for name, d in pair_negatives:
        try:
            check_approval_pair(proposal, d)
        except ValueError:
            checks.append(name + ' correctly rejected')
            continue
        raise AssertionError('Negative case accepted: ' + name)

    retry = read_json(ROOT / 'examples/action-proposal-retry.json')
    validate('action-proposal', retry)
    check_operation_key_reuse([proposal, retry])
    checks.append('retry attempt reuses operation_key for the identical action (run-002 -> run-003)')
    bad = copy.deepcopy(retry); bad['resource_ref'] = 'repo:other-target'
    try:
        check_operation_key_reuse([proposal, bad])
        raise AssertionError('Negative case accepted: same operation_key, different target repository')
    except ValueError:
        checks.append('same operation_key with different target repository correctly rejected')

    locs = read_json(ROOT / 'examples/locations-monorepo.json')
    for loc in locs:
        validate('location-registration', loc)
    check_location_set(locs)
    checks.append('monorepo: %d projects share one repository with distinct metadata_root' % len(locs))
    for name, mutate in [('duplicate registration of same repository and metadata_root',
                          lambda ls: ls.append(dict(copy.deepcopy(ls[0]), location_id='loc-dup'))),
                         ('metadata_root claimed by two projects',
                          lambda ls: ls.append(dict(copy.deepcopy(ls[0]), location_id='loc-x', project_id='proj-x'))),
                         ('same physical folder registered as a second source',
                          lambda ls: ls.append(dict(copy.deepcopy(ls[0]), location_id='loc-y', source_id='src-other',
                                                    metadata_root='apps/c', project_id='proj-app-c')))]:
        ls = copy.deepcopy(locs); mutate(ls)
        try:
            check_location_set(ls)
            raise AssertionError('Negative case accepted: ' + name)
        except ValueError:
            checks.append(name + ' correctly rejected')

    # Out-of-order scan completion: scan B (newer snapshot) finishes before scan A.
    a_old = dict(read_json(ROOT / 'examples/ppm-output.json'), assessment_id='asmt-A', snapshot_seq=2, basis_seq=2)
    b_new = dict(a_old, assessment_id='asmt-B', snapshot_seq=3, basis_seq=3)
    current, history = apply_assessment(None, b_new)
    current, history = apply_assessment(current, a_old)
    if current['assessment_id'] != 'asmt-B' or history['assessment_id'] != 'asmt-A':
        raise AssertionError('late assessment of older snapshot replaced current assessment')
    checks.append('late assessment of older snapshot kept as history; newer snapshot stays current')
    # Same snapshot, baseline changed while the old assessment is still running (C39).
    old_basis = dict(b_new, assessment_id='asmt-C-baseline1', basis_seq=4, baseline_revision=1, trigger='snapshot')
    new_basis = dict(b_new, assessment_id='asmt-D-baseline2', basis_seq=5, baseline_revision=2, trigger='baseline_changed')
    current, _ = apply_assessment(current, new_basis)
    current, history = apply_assessment(current, old_basis)
    if current['assessment_id'] != 'asmt-D-baseline2' or history['assessment_id'] != 'asmt-C-baseline1':
        raise AssertionError('assessment on old baseline replaced reassessment on new baseline')
    checks.append('same snapshot: baseline-1 result finishing last stays history; baseline-2 reassessment stays current')

    for file in ['templates/new-agent/agent.json', 'templates/existing-agent/agent.json']:
        data = read_json(ROOT / file)
        for key in ['input_schema_ref', 'output_schema_ref']:
            if not (ROOT / data[key]).is_file():
                raise ValueError('missing schema reference ' + data[key])
    for path in ROOT.rglob('*.py'):
        if '.git' not in path.parts:
            ast.parse(path.read_text(encoding='utf-8'))
    for path in ROOT.rglob('*.md'):
        if '.git' not in path.parts and path.read_text(encoding='utf-8').count('```') % 2:
            raise ValueError('unbalanced fenced block ' + str(path))
    checks.append('local schema refs, Python syntax and Markdown fences passed')

    ppm = subprocess.run([sys.executable, str(ROOT / 'ppm-v2/tools/run_negative_cases.py')],
                         cwd=ROOT / 'ppm-v2', capture_output=True, text=True)
    if ppm.returncode != 0:
        raise AssertionError('PPM contract checks failed:\n' + ppm.stdout + ppm.stderr)
    checks.extend('ppm-v2: ' + line for line in ppm.stdout.strip().splitlines())

    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    report = '# Package validation report\n\n'
    report += 'Generated by `python tools/validate_package.py` at %s.\n' % now
    report += 'Source revision: %s. Python %s. jsonschema %s.\n\n' % (git_revision(), platform.python_version(), version('jsonschema'))
    report += '## Executed\n' + '\n'.join('- ' + item for item in checks)
    report += '\n\n## Not run (no runtime exists yet)\n'
    report += '- Runtime conformance cases C01-C39 (evals/CONFORMANCE_CASES.md)\n'
    report += '- Security tests against running services (path/symlink/hardlink escape, prompt injection, cross-tenant, egress, approval replay)\n'
    report += '- Recovery/fault injection, vendor/SDK compatibility, model quality, HUD load and live integration\n'
    report += '\nNegative cases above validate package-level structural/semantic rules only.\n'
    (ROOT / 'VALIDATION_REPORT.md').write_text(report, encoding='utf-8')
    print(f'PASS {len(validators)} schemas, {len(fixtures)} JSON fixtures, {len(events)} events, '
          f'{len(negatives) + len(pair_negatives)} negative cases, PPM checks passed')


if __name__ == '__main__':
    main()
