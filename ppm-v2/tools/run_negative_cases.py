#!/usr/bin/env python3
"""Run PPM contract template checks, document negative cases and metadata-root (dir) scenarios.

Run from the ppm-v2 directory:  python tools/run_negative_cases.py
Exit code 0 only if every template validates and every case behaves as expected.
Structural/semantic checks only: no project commands, no network.
"""
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from validate_contract import load_yaml, semantic_doc_errors, validate_paths  # noqa: E402

BASE = {
    'project': 'templates/project/.ppm/project.yaml',
    'status': 'templates/project/.ppm/status.yaml',
    'update': 'templates/project/.ppm/updates/upd-example-001.yaml',
    'acceptance': 'templates/project/.ppm/acceptance.yaml',
    'registry': 'templates/registry.example.yaml',
}


def set_path(doc, dotted, value):
    keys = dotted.split('.')
    cur = doc
    for k in keys[:-1]:
        cur = cur[int(k)] if k.isdigit() else cur[k]
    last = keys[-1]
    cur[int(last) if last.isdigit() else last] = value


def _dump(path, doc):
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding='utf-8')


def dir_scenario(name, tmp):
    """Build a metadata root from the templates and apply one scenario mutation."""
    root = tmp / '.ppm'
    shutil.copytree(ROOT / 'templates/project/.ppm', root)
    upd_path = root / 'updates/upd-example-001.yaml'
    upd = load_yaml(upd_path.read_text(encoding='utf-8'))

    def add_update(uid, **changes):
        d = dict(copy.deepcopy(upd), update_id=uid, **changes)
        _dump(root / 'updates' / (uid + '.yaml'), d)

    if name == 'duplicate_criterion_id':
        acc = load_yaml((root / 'acceptance.yaml').read_text(encoding='utf-8'))
        acc['criteria'].append(copy.deepcopy(acc['criteria'][0]))
        _dump(root / 'acceptance.yaml', acc)
    elif name == 'supersession_cycle':
        add_update('upd-b', supersedes_update_id='upd-c')
        add_update('upd-c', supersedes_update_id='upd-b')
    elif name == 'supersedes_unknown_update':
        add_update('upd-d', supersedes_update_id='upd-missing')
    elif name == 'update_references_unknown_criterion':
        d = copy.deepcopy(upd)
        d['checks'][0]['criterion_ids'] = ['AC-999']
        add_update('upd-e', checks=d['checks'])
    elif name == 'two_project_ids_in_one_root':
        add_update('upd-f', project_id='proj-other')
    elif name == 'valid_correction_chain':
        add_update('upd-g', supersedes_update_id='upd-example-001', summary='Correction of upd-example-001')
    else:
        raise KeyError(name)
    return sorted(root.rglob('*.yaml'))


def main():
    schema = json.loads((ROOT / 'schemas/contract.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    failures = []
    for kind, rel in BASE.items():
        doc = load_yaml((ROOT / rel).read_text(encoding='utf-8'))
        errors = [e.message for e in validator.iter_errors(doc)] + semantic_doc_errors(doc)
        if errors:
            failures.append('template %s invalid: %s' % (rel, errors[0]))
        else:
            print('template %s: PASS' % rel)
    tpl_errors, _ = validate_paths(sorted((ROOT / 'templates/project/.ppm').rglob('*.yaml')), validator)
    if tpl_errors:
        failures.append('template metadata root invalid: %s' % tpl_errors[0])
    cases = json.loads((ROOT / 'evals/negative_cases.json').read_text(encoding='utf-8'))
    for case in cases:
        try:
            if case.get('kind') == 'dir':
                with tempfile.TemporaryDirectory() as t:
                    errors, _ = validate_paths(dir_scenario(case['scenario'], Path(t)), validator)
                valid = not errors
            else:
                if 'raw_yaml' in case:
                    doc = load_yaml(case['raw_yaml'])
                else:
                    doc = copy.deepcopy(load_yaml((ROOT / BASE[case['kind']]).read_text(encoding='utf-8')))
                    for key, value in case['mutation'].items():
                        set_path(doc, key, value)
                valid = validator.is_valid(doc) and not semantic_doc_errors(doc)
        except ValueError:
            valid = False
        if valid != case['expected_valid']:
            failures.append('case "%s": valid=%s expected=%s' % (case['name'], valid, case['expected_valid']))
        else:
            print('case %s: correctly %s' % (case['name'], 'accepted' if valid else 'rejected'))
    if failures:
        print('\n'.join('FAIL ' + f for f in failures))
        return 1
    print('PASS %d templates, %d cases' % (len(BASE), len(cases)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
