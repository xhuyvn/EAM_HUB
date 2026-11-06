"""Validate supplied contracts and examples. Run from any working directory."""
import ast
import copy
import json
from pathlib import Path
from importlib.metadata import version
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[1]

def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))

def semantic_checks(kind, value):
    if kind == 'event' and value['event_type'] == 'run.progress':
        payload = value['payload']
        completed = payload['completed_units']
        total = payload['total_units']
        if total is not None and completed is not None and completed > total:
            raise ValueError('progress exceeds denominator')
    if kind == 'result':
        usage = value['usage']
        if usage['cost_status'] == 'unknown' and usage['cost_usd'] is not None:
            raise ValueError('unknown cost must be null')
        if usage['cost_status'] != 'unknown' and usage['cost_usd'] is None:
            raise ValueError('known cost needs numeric value')
    if kind == 'binding':
        capabilities = value['certified_capabilities']
        if value['mode'] == 'observe' and any(capabilities[k] for k in ['submit', 'cancel', 'resume', 'approve_tool']):
            raise ValueError('observe binding advertises control')
        if value['mode'] == 'governed' and value['execution_boundary'] == 'external_trusted':
            raise ValueError('governed binding requires enforcement boundary')
    if kind == 'experiment' and value['base_release_digest'] == value['candidate_release_digest']:
        raise ValueError('candidate must differ from baseline')

def main():
    validators = {}
    checks = []
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
    }
    def validate(kind, data):
        validators[kind].validate(data)
        semantic_checks(kind, data)
    for file, kind in fixtures.items():
        validate(kind, read_json(ROOT / file))
        checks.append(file + ' passed')
    events = [json.loads(line) for line in (ROOT / 'examples/events.ndjson').read_text().splitlines()]
    seen = set()
    for event in events:
        validate('event', event)
        key = (event['tenant_id'], event['producer_id'], event['event_id'])
        if key in seen:
            raise ValueError('duplicate fixture event')
        seen.add(key)
    checks.append('typed event fixtures passed')
    negatives = []
    data = read_json(ROOT / 'examples/task-request.json')
    data['tenant_id'] = 'spoofed'
    negatives.append(('untrusted tenant claim', 'task-request', data))
    data = copy.deepcopy(events[1])
    data['payload']['completed_units'] = 13
    negatives.append(('progress overflow', 'event', data))
    data = copy.deepcopy(events[1])
    data['payload']['total_units'] = 0
    negatives.append(('zero denominator', 'event', data))
    data = copy.deepcopy(events[0])
    data['occurred_at'] = '2026-11-05T09:00:00'
    negatives.append(('timestamp without timezone', 'event', data))
    data = read_json(ROOT / 'examples/result.json')
    data['usage']['cost_usd'] = 0
    negatives.append(('unknown cost rendered as zero', 'result', data))
    data = read_json(ROOT / 'templates/admin/existing-observe-binding.example.json')
    data['certified_capabilities']['cancel'] = True
    negatives.append(('observe-only control escalation', 'binding', data))
    data = read_json(ROOT / 'templates/admin/existing-observe-binding.example.json')
    data['mode'] = 'governed'
    negatives.append(('external boundary claims governed mode', 'binding', data))
    data = read_json(ROOT / 'templates/actions/action-proposal.example.json')
    data['action_digest'] = 'invalid'
    negatives.append(('invalid action digest', 'action-proposal', data))
    data = read_json(ROOT / 'templates/phase2/experiment.example.json')
    data['candidate_release_digest'] = data['base_release_digest']
    negatives.append(('identical optimization candidate', 'experiment', data))
    for name, kind, data in negatives:
        rejected = False
        try:
            validate(kind, data)
        except (ValueError, ValidationError):
            rejected = True
        if not rejected:
            raise AssertionError('Negative case accepted: ' + name)
        checks.append(name + ' correctly rejected')
    for file in ['templates/new-agent/agent.json', 'templates/existing-agent/agent.json']:
        data = read_json(ROOT / file)
        for key in ['input_schema_ref', 'output_schema_ref']:
            if not (ROOT / data[key]).is_file():
                raise ValueError('missing schema reference ' + data[key])
    for path in ROOT.rglob('*.py'):
        ast.parse(path.read_text(encoding='utf-8'))
    for path in ROOT.rglob('*.md'):
        if path.read_text(encoding='utf-8').count('```') % 2:
            raise ValueError('unbalanced fenced block ' + str(path))
    report = '# Package validation report\n\nDate 05/11/2026.\n\n'
    report += '\n'.join('- ' + item for item in checks)
    report += '\n\nLocal schema references, Python syntax and Markdown fences passed.\n'
    report += '\nRuntime conformance, security, recovery, vendor compatibility, model quality, HUD load and live integration are pending implementation. The negative cases above validate package-level structural/semantic rules only.\n'
    report += '\nValidator dependency jsonschema ' + version('jsonschema') + '.\n'
    (ROOT / 'VALIDATION_REPORT.md').write_text(report, encoding='utf-8')
    print(f'PASS {len(validators)} schemas, {len(fixtures)} JSON fixtures, {len(events)} events, {len(negatives)} negative cases')

if __name__ == '__main__':
    main()
