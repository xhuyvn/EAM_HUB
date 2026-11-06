#!/usr/bin/env python3
"""Reference structural validator; no project commands or network requests."""
import argparse,json,sys
from pathlib import Path
import yaml
from jsonschema import Draft202012Validator,FormatChecker
class UniqueLoader(yaml.SafeLoader): pass
def mapping(loader,node,deep=False):
 out={}
 for kn,vn in node.value:
  k=loader.construct_object(kn,deep=deep)
  if k in out: raise ValueError('duplicate YAML key: '+str(k))
  out[k]=loader.construct_object(vn,deep=deep)
 return out
UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,mapping)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('target');ap.add_argument('--schema',required=True);a=ap.parse_args()
 schema=json.loads(Path(a.schema).read_text(encoding='utf-8'));Draft202012Validator.check_schema(schema)
 validator=Draft202012Validator(schema,format_checker=FormatChecker())
 target=Path(a.target);files=sorted(target.rglob('*.yaml')) if target.is_dir() else [target]
 if not files: print('FAIL no YAML files');return 1
 errors=[];docs=[]
 for p in files:
  try:
   if p.stat().st_size>2097152: raise ValueError('file exceeds 2 MiB limit')
   d=yaml.load(p.read_text(encoding='utf-8'),Loader=UniqueLoader)
   es=list(validator.iter_errors(d))
   if es: raise ValueError('; '.join(e.message for e in es))
   if d['kind']=='update' and p.stem!=d['update_id']: raise ValueError('filename/update_id mismatch')
   docs.append((p,d))
  except Exception as e: errors.append(f'{p}: {e}')
 ids={d['project_id'] for _,d in docs if 'project_id' in d}
 if target.is_dir() and len(ids)>1: errors.append('project_id mismatch within metadata root')
 seen=set()
 for p,d in docs:
  if d['kind']=='update':
   key=(d['project_id'],d['update_id'])
   if key in seen: errors.append('duplicate update ID '+str(key))
   seen.add(key)
   if d['supersedes_update_id']==d['update_id']: errors.append('update cannot supersede itself')
  if d['kind']=='registry':
   pids=[x['project_id'] for x in d['projects']]
   if len(pids)!=len(set(pids)): errors.append('duplicate registry project_id')
   for proj in d['projects']:
    lids=[x['location_id'] for x in proj['locations']]
    if len(lids)!=len(set(lids)) or proj['metadata_location_id'] not in lids: errors.append('invalid location identities')
 if errors:
  print('\n'.join('FAIL '+x for x in errors));return 1
 print(f'PASS {len(files)} records; structural checks only');return 0
if __name__=='__main__':sys.exit(main())
