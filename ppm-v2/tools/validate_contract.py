#!/usr/bin/env python3
"""Reference validator for PPM contract 2.1 (reads 2.0). No project commands or network requests.

Structural checks (JSON Schema) plus semantic checks a schema cannot express:
- 2.1-only fields are rejected in documents that declare contract_version 2.0
- one project_id per metadata root; update filename == update_id; unique update/criterion IDs
- supersession must reference an existing update in the same root and must not form a cycle
- criterion_ids referenced by updates must exist in acceptance.yaml
- recorded_at/updated_at may not lie more than 24 h in the future (clock skew guard)
Passing proves structure and these rules only; it does not verify progress or acceptance.
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

MAX_BYTES = 2097152
FUTURE_SKEW = dt.timedelta(hours=24)


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    out = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in out:
            raise ValueError('duplicate YAML key: ' + str(key))
        out[key] = loader.construct_object(value_node, deep=deep)
    return out


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_yaml(text):
    return yaml.load(text, Loader=UniqueLoader)


def _uses_21_fields(doc):
    kind = doc.get('kind')
    if kind == 'acceptance':
        return any('verification_refs' in c for c in doc.get('criteria', []))
    if kind == 'update':
        items = doc.get('completed', []) + doc.get('in_progress', [])
        return any('criterion_ids' in i for i in items) or any(
            'criterion_ids' in c or 'test_ids' in c for c in doc.get('checks', []))
    if kind == 'project':
        return any(r.get('relation') == 'uses_component' for r in doc.get('relationship_proposals', []))
    return False


def _parse_ts(value):
    return dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def semantic_doc_errors(doc, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    errors = []
    if doc.get('contract_version') == '2.0' and _uses_21_fields(doc):
        errors.append('document declares 2.0 but uses 2.1 fields')
    for field in ('recorded_at', 'updated_at'):
        if field in doc:
            try:
                if _parse_ts(doc[field]) > now + FUTURE_SKEW:
                    errors.append(field + ' is more than 24h in the future')
            except ValueError:
                pass  # schema pattern reports malformed timestamps
    if doc.get('kind') == 'acceptance':
        ids = [c['criterion_id'] for c in doc.get('criteria', [])]
        if len(ids) != len(set(ids)):
            errors.append('duplicate criterion_id in acceptance.yaml')
    if doc.get('kind') == 'update' and doc.get('supersedes_update_id') == doc.get('update_id'):
        errors.append('update cannot supersede itself')
    return errors


def semantic_set_errors(docs):
    """docs: list of (path, doc) from one metadata root."""
    errors = []
    ids = {d['project_id'] for _, d in docs if 'project_id' in d and d.get('kind') != 'registry'}
    if len(ids) > 1:
        errors.append('project_id mismatch within metadata root')
    updates = {}
    for p, d in docs:
        if d.get('kind') == 'update':
            if d['update_id'] in updates:
                errors.append('duplicate update ID ' + d['update_id'])
            updates[d['update_id']] = d
    for uid, d in updates.items():
        target = d.get('supersedes_update_id')
        if target and target not in updates:
            errors.append('update %s supersedes unknown update %s' % (uid, target))
    for start in updates:
        seen, cur = set(), start
        while cur in updates and updates[cur].get('supersedes_update_id'):
            if cur in seen:
                errors.append('supersession cycle involving ' + start)
                break
            seen.add(cur)
            cur = updates[cur]['supersedes_update_id']
    criteria = set()
    for _, d in docs:
        if d.get('kind') == 'acceptance':
            criteria |= {c['criterion_id'] for c in d.get('criteria', [])}
    for uid, d in updates.items():
        refs = set()
        for item in d.get('completed', []) + d.get('in_progress', []) + d.get('checks', []):
            refs |= set(item.get('criterion_ids', []))
        unknown = refs - criteria
        if unknown:
            errors.append('update %s references unknown criteria %s' % (uid, sorted(unknown)))
    for _, d in docs:
        if d.get('kind') == 'registry':
            pids = [x['project_id'] for x in d['projects']]
            if len(pids) != len(set(pids)):
                errors.append('duplicate registry project_id')
            for proj in d['projects']:
                lids = [x['location_id'] for x in proj['locations']]
                if len(lids) != len(set(lids)) or proj['metadata_location_id'] not in lids:
                    errors.append('invalid location identities')
    return sorted(set(errors))


def validate_paths(files, validator):
    errors, docs = [], []
    for p in files:
        try:
            if p.stat().st_size > MAX_BYTES:
                raise ValueError('file exceeds 2 MiB limit')
            d = load_yaml(p.read_text(encoding='utf-8'))
            es = list(validator.iter_errors(d))
            if es:
                raise ValueError('; '.join(e.message for e in es))
            if d['kind'] == 'update' and p.stem != d['update_id']:
                raise ValueError('filename/update_id mismatch')
            for e in semantic_doc_errors(d):
                raise ValueError(e)
            docs.append((p, d))
        except Exception as e:  # report every file, keep going
            errors.append(f'{p}: {e}')
    errors += semantic_set_errors(docs)
    return errors, docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target')
    ap.add_argument('--schema', required=True)
    a = ap.parse_args()
    schema = json.loads(Path(a.schema).read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    target = Path(a.target)
    files = sorted(target.rglob('*.yaml')) if target.is_dir() else [target]
    if not files:
        print('FAIL no YAML files')
        return 1
    errors, _ = validate_paths(files, validator)
    if errors:
        print('\n'.join('FAIL ' + x for x in errors))
        return 1
    print(f'PASS {len(files)} records; structural and contract-semantic checks only')
    return 0


if __name__ == '__main__':
    sys.exit(main())
