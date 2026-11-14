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
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

_REDUCER_DIR = Path(__file__).resolve().parents[1] / "reducer"
if str(_REDUCER_DIR) not in sys.path:
    sys.path.insert(0, str(_REDUCER_DIR))

from ppm_reducer.contract_validation import (  # noqa: E402
    FUTURE_SKEW,
    parse_ts as _parse_ts,
    semantic_doc_errors,
    semantic_set_errors,
    uses_21_fields as _uses_21_fields,
)

MAX_BYTES = 2097152


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
