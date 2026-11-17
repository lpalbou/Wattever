"""Version and correct grading of saved outputs only; never repeats an inference request."""
from pathlib import Path
import hashlib,json,shutil,datetime
import common
HERE=Path(__file__).resolve().parent
sha=lambda raw:hashlib.sha256(raw).hexdigest()
plan=json.loads((HERE/'plan.json').read_text(encoding='utf-8'));tasks={t['id']:t for t in plan['tasks']}
sources=HERE/'grading-sources';sources.mkdir(exist_ok=True)
new_code=(HERE/'code_grade.py').read_text()
old_code=new_code.replace("'is_integer','__name__',\n       'lstrip','rstrip','splitlines','casefold','removeprefix','removesuffix','isspace'","'is_integer'").replace("'str','sum','tuple','type','zip'","'str','sum','tuple','zip'")
old_code=old_code.replace("    format_ok=False\n",'').replace(
    "    except SyntaxError as exc:return {'content_passed':False,'format_passed':format_ok,'failure':'code_syntax_error','detail':str(exc)[:300]}\n    except ValueError as exc:return {'content_passed':None,'format_passed':format_ok,'failure':'execution_policy_unsupported','detail':str(exc)[:300]}",
    "    except (ValueError,SyntaxError) as exc:return {'content_passed':False,'format_passed':False,'failure':'code_policy_or_syntax','detail':str(exc)[:300]}")
new_common=(HERE/'common.py').read_text();old_common=new_common.replace('None if content is None else bool(content)','bool(content)')
changes=[]
for folder in sorted(HERE.glob('*2048-controlled')):
    path=folder/'run.json'
    if not path.exists():continue
    report=json.loads(path.read_text(encoding='utf-8'))
    for name,old,new in [('code_grade.py',old_code,new_code),('common.py',old_common,new_common)]:
        expected=report['helpers_sha256'][name]
        assert sha(old.encode())==expected or sha(new.encode())==expected,(name,'Reconstruction does not match original recorded source')
        for version in (old,new):
            target=sources/(Path(name).stem+'-'+sha(version.encode())[:16]+'.py')
            if target.exists():assert target.read_bytes()==version.encode()
            else:target.write_text(version,encoding='utf-8',newline='\n')
    original=folder/'run.original-grading.json'
    if original.exists():raise SystemExit('Original grading already preserved; inspect before any additional regrade.')
    shutil.copy2(path,original)
    for row in report['results']:
        rowpath=folder/(row['id']+'.json');shutil.copy2(rowpath,folder/(row['id']+'.original-grading.json'))
        prior=row['grade'];updated=common.score(tasks[row['id']],row)
        row['grade_history']=[{'source_sha256':report['helpers_sha256'],'grade':prior}]
        row['grade']=updated;row['grading_revision']='v2-safe-basic-type-introspection'
        common.save(rowpath,row)
        if prior!=updated:changes.append({'condition':folder.name,'id':row['id'],'before':prior,'after':updated})
    report['summary']=common.summary(report['results'])
    report['grading_revision']='v2-safe-basic-type-introspection'
    report['current_grading_source_sha256']={name:sha((HERE/name).read_bytes()) for name in ('common.py','code_grade.py')}
    common.save(path,report)
common.save(HERE/'grading-review.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'reason':'The prompt permits Python builtins; type(value).__name__ was valid but initially rejected by an overly narrow execution-policy whitelist. Allow basic type/name introspection. Unsupported execution policies are kept separate from syntax or content failures.',
    'inference_repeated':False,'applies_to_all_candidates':True,'changes':changes})
print(json.dumps({'changed_saved_grades':len(changes),'inference_repeated':False}))
