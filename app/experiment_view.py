"""Bounded read-only condition views over real experiment evidence.

Historical trials are projections of the original shared run. New experiments
declare exact child runs; neither path invents isolated historical execution.
"""
import json
import math
from pathlib import Path
import re

from experiment_usage import normalize_usage, summarize_usage
from six_bot_roster import CONDITIONS as SIX_CONDITIONS, ROSTER as SIX_ROSTER
from experiment_runs import JEV_CONDITIONS, JEV_ROSTER, validate_jev_condition

IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,159}')
MEMORY_CONDITIONS = ('solo-cold', 'team-cold', 'solo-warm', 'team-warm')
MEMORY_JEV_CONDITIONS = MEMORY_CONDITIONS + ('solo-jev-warm', 'team-jev-warm')
PILOT_CONDITIONS = tuple(f'{task}-{kind}-r{rep}' for task in ('small','medium')
                         for kind in ('solo','team') for rep in (1,2))
MAX_CALLS = 200
MAX_CASES = 1500


def identifier(value):
    if not isinstance(value,str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('Choose a saved experiment condition.')
    return value


def inside(base, path):
    base, path = Path(base).resolve(), Path(path)
    resolved = path.resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError('Experiment evidence must stay in its own run.')
    # Reject even in-root links: a condition cannot alias another condition.
    current = path
    while current != base and current != current.parent:
        if current.is_symlink() or getattr(current,'is_junction',lambda:False)():
            raise ValueError('Linked experiment evidence is unavailable.')
        current = current.parent
    return resolved


class Reader:
    def __init__(self):
        self.remaining = 16 * 1024**2
        self.cache = {}

    def text(self, base, path, maximum=2*1024**2):
        path = inside(base,path)
        if path in self.cache: return self.cache[path]
        with path.open('rb') as stream: raw = stream.read(min(maximum,self.remaining)+1)
        if len(raw) > maximum or len(raw) > self.remaining:
            raise ValueError('Experiment evidence exceeds the bounded reader. Inspect its saved files.')
        self.remaining -= len(raw)
        result = raw.decode('utf-8-sig')
        self.cache[path] = result
        return result

    def obj(self, base, path, optional=False, maximum=2*1024**2):
        try: value = json.loads(self.text(base,path,maximum))
        except FileNotFoundError:
            if optional: return {}
            raise ValueError('The selected experiment evidence is unavailable.') from None
        if not isinstance(value,dict): raise ValueError('Experiment evidence must be an object.')
        return value


def finite(value):
    return value if type(value) in (int,float) and math.isfinite(value) and value >= 0 else None


def grades(raw):
    if not raw: return {'passed':None,'total':None,'groups':[],'cases':[]}
    rows = []
    roles = raw.get('roles') or [raw]
    if isinstance(roles,dict): roles = list(roles.values())
    if not isinstance(roles,list): raise ValueError('Malformed grade roles.')
    for role in roles:
        for case in role.get('cases',[]):
            if len(rows) >= MAX_CASES: raise ValueError('Too many saved check results.')
            # Public/hidden is disclosure policy, NOT a normal/edge classification.
            group = case.get('group',case.get('case_group','unclassified'))
            if group not in ('normal','edge'): group = 'unclassified'
            rows.append({'name':str(role.get('role','')) + (': ' if role.get('role') else '') + str(case.get('case','Unnamed check')),
                         'group':group,'passed':case.get('passed') if type(case.get('passed')) is bool else None,
                         'detail':case.get('detail'),'public':case.get('public')})
    grouped = [{'name':key,'passed':sum(row['passed'] is True for row in rows if row['group']==key),
                'total':sum(row['group']==key for row in rows)} for key in ('normal','edge','unclassified')]
    return {'passed':finite(raw.get('passed')),'total':finite(raw.get('total')),
            'groups':[row for row in grouped if row['total']], 'cases':rows}


class ExperimentStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = inside(self.root,self.root/'.orchestration')

    def run(self, name): return inside(self.base,self.base/identifier(name))

    def _experiment(self, name, reader):
        run = self.run(name)
        manifest = reader.obj(run,run/'run.json')
        current = reader.obj(run,run/'experiment.json',True)
        historical = not bool(current)
        if current:
            if current.get('schema_version') != 1 or current.get('family') not in ('memory_control','solo_vs_team','six_bot_memory','jev_memory'):
                raise ValueError('Unsupported experiment schema.')
            conditions = current.get('conditions')
            if not isinstance(conditions,list) or len(conditions)>64:
                raise ValueError('Invalid experiment condition list.')
            seen, child_runs, projects = set(), set(), set()
            allowed = (JEV_CONDITIONS if current['family']=='jev_memory' else SIX_CONDITIONS
                       if current['family']=='six_bot_memory' else MEMORY_JEV_CONDITIONS
                       if current['family']=='memory_control' else PILOT_CONDITIONS)
            matched_jev = current['family']=='memory_control' and any(row.get('id') in MEMORY_JEV_CONDITIONS[4:] for row in conditions)
            if matched_jev and tuple(row.get('id') for row in conditions)!=MEMORY_JEV_CONDITIONS:
                raise ValueError('Matched Brain/JEV comparison must declare all six conditions in order.')
            if current['family']=='jev_memory' and tuple(row.get('id') for row in conditions)!=JEV_CONDITIONS:
                raise ValueError('Jev comparison must declare exactly the four cold-then-Jev conditions.')
            for condition in conditions:
                key = identifier(condition.get('id'))
                if key not in allowed: raise ValueError('Unsupported experiment condition.')
                if key in seen: raise ValueError('Duplicate experiment condition.')
                seen.add(key)
                child_id = identifier(condition.get('run_id'))
                if child_id in child_runs: raise ValueError('Conditions must have separate child runs.')
                child_runs.add(child_id)
                child = self.run(child_id)
                child_meta = reader.obj(child,child/'condition.json')
                if matched_jev:
                    expected = 'jev' if key in MEMORY_JEV_CONDITIONS[4:] else 'ordinary' if key.endswith('warm') else 'disabled'
                    if child_meta.get('suite_mode')!='jev_comparison' or child_meta.get('memory_ranking_mode')!=expected:
                        raise ValueError('Matched Brain/JEV child has inconsistent ranking metadata.')
                project = child_meta.get('project_id')
                if not isinstance(project,str) or not project or project in projects:
                    raise ValueError('Conditions must have separate memory scopes.')
                projects.add(project)
            family = current['family']
        elif (run/'review/experiment-lock.json').is_file():
            lock = reader.obj(run,run/'review/experiment-lock.json')
            if lock.get('order') != list(MEMORY_CONDITIONS): raise ValueError('Unrecognized historical experiment.')
            family = 'memory_control'
            conditions = [{'id':key,'run_id':name,'label':key} for key in MEMORY_CONDITIONS]
        elif (run/'review/benchmark-lock.json').is_file():
            lock = reader.obj(run,run/'review/benchmark-lock.json')
            if lock.get('memory') != 'disabled for every contestant': raise ValueError('Unrecognized historical experiment.')
            family = 'solo_vs_team'
            conditions = [{'id':key,'run_id':name,'label':key} for key in PILOT_CONDITIONS]
        else: raise ValueError('This run has no recorded experiment conditions.')
        return {'id':name,'title':manifest.get('display_name') or manifest.get('objective') or name,
                'family':family,'historical':historical,'status':current.get('status',manifest.get('status','unknown')),
                'conditions':conditions}

    def listing(self):
        rows, errors, reader = [], [], Reader()
        if not self.base.exists(): return {'experiments':[],'errors':[],'model_calls':0}
        paths = sorted(self.base.iterdir(),key=lambda p:p.name,reverse=True)
        if len(paths)>500: raise ValueError('Too many orchestration entries for the experiment reader.')
        for path in paths:
            if not path.is_dir(): continue
            if not any((path/relative).is_file() for relative in ('experiment.json','review/experiment-lock.json','review/benchmark-lock.json')): continue
            # A child may have its own freeze; it belongs under the declared parent.
            if (path/'condition.json').is_file(): continue
            try:
                row = self._experiment(path.name,reader)
                conditions = []
                for item in row['conditions']:
                    detail = self._detail(row,item['id'],reader)
                    metrics = detail['metrics']
                    conditions.append({'id':detail['id'],'label':detail['label'],'status':detail['status'],
                        'run_id':detail['run_id'],'memory_mode':detail['memory']['mode'],
                        **{key:metrics[key] for key in ('contestant_count','helper_count','provider_count','wall_seconds','input_tokens','output_tokens')},
                        'worker_calls':metrics['calls']})
                rows.append(dict(row,conditions=conditions))
            except (OSError,ValueError,KeyError,TypeError,AttributeError) as error:
                errors.append({'id':path.name,'error':str(error) if isinstance(error,ValueError) else 'Saved experiment needs inspection.'})
        return {'experiments':rows,'errors':errors,'model_calls':0}

    def detail(self, run, condition):
        reader = Reader()
        return self._detail(self._experiment(run,reader),identifier(condition),reader)

    def _context(self, experiment, condition, reader):
        declarations = [row for row in experiment['conditions'] if row['id']==condition]
        if len(declarations)!=1: raise ValueError('The selected condition is not in this experiment.')
        declaration = declarations[0]
        run = self.run(declaration['run_id'])
        metadata = {} if experiment['historical'] else reader.obj(run,run/'condition.json')
        if metadata and (metadata.get('parent_run_id')!=experiment['id'] or metadata.get('id')!=condition):
            raise ValueError('Child condition identity does not match its parent.')
        if metadata and (metadata.get('run_id')!=run.name or metadata.get('helper_count')!=0):
            raise ValueError('Unsupported condition identity or helper policy; inspect original evidence.')
        if metadata:
            manifest = reader.obj(run,run/'run.json')
            roster = metadata.get('roster')
            if (metadata.get('schema_version')!=1 or metadata.get('family')!=experiment['family']
                    or metadata.get('memory_mode') not in ('disabled','seeded')
                    or type(metadata.get('helper_count')) is not int
                    or type(metadata.get('contestant_count')) is not int
                    or metadata.get('project_id')!=manifest.get('project_id')
                    or not isinstance(roster,list) or len(roster)!=metadata.get('contestant_count')
                    or not 1<=len(roster)<=(6 if experiment['family']=='six_bot_memory' else 3)
                    or len({r.get('id') for r in roster})!=len(roster)):
                raise ValueError('Condition roster or memory scope is inconsistent.')
            if experiment['family']=='jev_memory':
                validate_jev_condition(metadata)
                if declaration.get('label')!=metadata.get('label'):
                    raise ValueError('Jev condition label differs from its declaration.')
        folder = run/('attempts' if experiment['family']=='solo_vs_team' else 'trials')/condition
        summary = reader.obj(run,folder/'summary.json',True)
        if summary and summary.get('id',condition)!=condition: raise ValueError('Trial summary identity mismatch.')
        if experiment['historical'] and summary and isinstance(summary.get('calls'),list):
            call_ids = summary['calls']
        else:
            call_ids = [p.parent.name for p in (run/'calls').glob('*/record.json') if not experiment['historical'] or p.parent.name.startswith(condition+'-')]
            if summary and (not isinstance(summary.get('calls'),list) or set(summary['calls'])-set(call_ids)):
                raise ValueError('A summarized call is missing from its condition.')
        if len(call_ids)>MAX_CALLS or len(set(call_ids))!=len(call_ids): raise ValueError('Invalid call membership.')
        records = []
        for name in call_ids:
            identifier(name)
            if not name.startswith(condition+'-'): raise ValueError('A call belongs to a different condition.')
            record = reader.obj(run,run/'calls'/name/'record.json')
            if record.get('call_id')!=name: raise ValueError('Saved call identifier mismatch.')
            actor = self._actor(experiment['family'],condition,name,record)
            expected = dict((row[0],row[1]) for row in self._candidates(experiment['family'],condition))
            if expected.get(actor)!=record.get('provider'): raise ValueError('Call provider is outside this condition roster.')
            if metadata:
                member = next((r for r in roster if r['id']==actor),None)
                if (not member or member.get('provider')!=record.get('provider')
                        or record.get('run_id')!=run.name or record.get('parent_run_id')!=experiment['id']
                        or record.get('condition_id')!=condition or record.get('actor_id')!=actor
                        or record.get('scope')!='contestant' or record.get('helper_count')!=0
                        or record.get('role')!=member.get('role')):
                    raise ValueError('Call execution binding does not match the isolated condition.')
                if experiment['family']=='jev_memory' and (
                        record.get('requested_model')!='opus' or record.get('requested_effort')!='medium'
                        or type(record.get('memory_enabled')) is not bool
                        or record['memory_enabled']!=(metadata['memory_mode']=='seeded')
                        or (metadata['memory_mode']=='disabled' and
                            (record.get('memory_ids',[]) or record.get('memory_context','')))):
                    raise ValueError('Jev call model, effort or memory policy differs from the frozen condition.')
            records.append(dict(record,worker_id=actor))
        return run,folder,metadata,summary,records,declaration

    @staticmethod
    def _actor(family, condition, name, record):
        if family=='jev_memory':
            if name!=condition+'-implementer-1' or record.get('actor_id')!='implementer':
                raise ValueError('Jev conditions permit one declared implementer call.')
            return 'implementer'
        if family=='six_bot_memory':
            actor=record.get('actor_id')
            if actor not in {r[0] for r in SIX_ROSTER} or not name.startswith(condition+'-'+actor+'-'):
                raise ValueError('Unknown six-bot contestant identity.')
            return actor
        if family=='memory_control':
            suffix = name[len(condition)+1:]
            actor = suffix.rsplit('-',1)[0]
            if actor not in ('solo','summary','aggregation','allocation'): raise ValueError('Unknown contestant role.')
            return actor
        return {'OpenAI':'implementer','Anthropic':'claude-auditor','xAI':'grok-auditor'}.get(record.get('provider'),'unknown-worker')

    @staticmethod
    def _candidates(family, condition):
        if family=='jev_memory': return list(JEV_ROSTER)
        if family=='six_bot_memory': return list(SIX_ROSTER)
        team = condition.startswith('team-') if family=='memory_control' else '-team-' in condition
        return ([('summary','OpenAI','Reporting implementation'),('aggregation','Anthropic','Usage aggregation implementation'),('allocation','xAI','Slot allocation implementation')]
                      if family=='memory_control' and team else [('solo','OpenAI','All three implementation modules')]
                      if family=='memory_control' else [('implementer','OpenAI','Implementation and final revision')] +
                      ([('claude-auditor','Anthropic','Independent audit')] if team else []) +
                      ([('grok-auditor','xAI','Independent edge-case audit')] if team and condition.startswith('medium-') else []))
    def _detail(self, experiment, condition, reader):
        run,folder,metadata,summary,records,declaration = self._context(experiment,condition,reader)
        family = experiment['family']
        memory_mode = metadata.get('memory_mode') or ('seeded' if family=='memory_control' and condition.endswith('-warm') else 'disabled')
        candidates = self._candidates(family,condition)
        declared = {row['id']:row for row in metadata.get('roster',[])}
        if metadata and {key:row.get('provider') for key,row in declared.items()}!={row[0]:row[1] for row in candidates}:
            raise ValueError('Declared roster does not match this experiment condition.')
        roster = []
        for actor,provider,role in candidates:
            own = [record for record in records if record['worker_id']==actor]
            roster.append({'worker_id':actor,'name':actor if family=='six_bot_memory' else 'OpenAI contestant' if provider=='OpenAI' else 'Claude' if provider=='Anthropic' else 'Grok',
                           'provider':provider,'role':role,'requested_model':next((r.get('requested_model') for r in own if r.get('requested_model')),declared.get(actor,{}).get('model')),
                           'requested_effort':next((r.get('requested_effort') for r in own if r.get('requested_effort')),declared.get(actor,{}).get('effort')),
                           'actual_models':sorted({m for r in own for m in r.get('actual_models',[]) if isinstance(m,str)}),
                           'calls':len(own),'helper_count':0})
        usage = summarize_usage(records)
        calls = [{'id':record['call_id'],'worker_id':record['worker_id'],'provider':record.get('provider','unknown'),
                  'preflight_only':record.get('provider_calls')==0,
                  'stage':record['call_id'][len(condition)+1:],'status':record.get('status','unknown'),
                  'started_at':record.get('started_at'),'ended_at':record.get('ended_at'),
                  'elapsed_seconds':finite(record.get('elapsed_seconds')),'requested_model':record.get('requested_model'),
                  'requested_effort':record.get('requested_effort'),
                  'actual_models':record.get('actual_models',[]),'usage':normalize_usage(record),
                  'memory_count':len(record.get('memory_ids',[])) if isinstance(record.get('memory_ids'),list) else None} for record in records]
        raw_initial = reader.obj(run,folder/'initial-grade.json',True)
        raw_final = reader.obj(run,folder/'final-grade.json',True)
        correction = reader.obj(run,run/'review/corrected-grades.json',True)
        corrected = correction.get('trials',{}).get(condition,{})
        initial,final = corrected.get('initial',raw_initial),corrected.get('final',raw_final)
        note = (correction.get('reason','')+' Incorrect solo public feedback affected revisions; corrected grades cannot repair timing.') if corrected else ''
        notes = ['Worker calls are recorded invocation attempts, not separate bots or verified underlying model requests. Preflight failures are marked when no provider was contacted.',
                 ('Creation and cross-audit have separate measured conditions. Root controller usage is unknown and excluded. Account allowances and billing cannot be inferred from tokens.' if family=='six_bot_memory' else
                  'Setup/controller and fixture-author usage is excluded and unmeasured. Account allowances and billing cannot be inferred from tokens.'),
                 'Normal/edge groups require explicit saved classification. Public/hidden checks are not the same grouping; historical unclassified cases are preserved.',
                 'Checks share an implementation request. No per-check token or timing attribution is claimed.']
        if experiment['historical']: notes.append('Historical condition view; shared original run. This display does not retroactively establish separate run or memory namespaces.')
        if family=='jev_memory':
            notes += ['This compares no memory with the combined memory and Jev-assisted recall workflow; it does not isolate the incremental effect of Jev.',
                      'Two fresh sessions per arm run in fixed cold-then-Jev order. Results are descriptive; order and model variability limit causal conclusions.',
                      'Each condition permits one response, with no revisions or repairs during scoring. Jev recall overhead is separate from contestant model usage.']
        if summary and not summary.get('wall_time_comparable',True): notes.append('This attempt was resumed; raw wall time is not comparable.')
        if note: notes.append(note)
        ids = sorted({key for record in records for key in record.get('memory_ids',[]) if isinstance(key,str)})
        lookup = [finite(record.get('memory_lookup_ms')) for record in records]
        lookup_total = sum(lookup) if lookup and all(x is not None for x in lookup) else None
        status = metadata.get('status') or summary.get('status') or ('completed' if summary.get('final_grade') else 'queued')
        if not metadata and not summary and records: status = 'running' if any(r.get('status')=='running' for r in records) else 'incomplete'
        manifest = reader.obj(run,run/'run.json')
        artifacts = []
        for key,label in [('initial','Initial implementation'),('final','Final implementation'),('initial-grade','Original initial checks'),
                          ('final-grade','Original final checks'),('summary','Condition summary and operations')]:
            suffix = '.py' if family=='solo_vs_team' and key in ('initial','final') else '.json'
            if (folder/(key+suffix)).is_file() or (folder/key).is_dir(): artifacts.append({'id':key,'label':label})
        if corrected: artifacts.append({'id':'correction','label':'Documented corrected checks'})
        return {'experiment_id':experiment['id'],'id':condition,'label':declaration.get('label',condition),'run_id':run.name,
                'family':family,'historical':experiment['historical'],'status':status,'stage':metadata.get('stage'),
                'memory':{'mode':memory_mode,'project_id':manifest.get('project_id'),'ids':ids,'lookup_ms':lookup_total,
                          'note':('Recall disabled for contestant calls.' if memory_mode=='disabled' else
                                  'Jev-assisted recall supplied these memory IDs; supplied context does not prove model reading or usefulness.'
                                  if family=='jev_memory' else 'These are observed supplied memory IDs; delivery alone does not prove usefulness.')},
                'roster':roster,'setup':{'usage_known':False,'note':(
                    'One Claude Opus contestant at medium effort, zero helpers, and a fresh supplied-text session per condition. Setup/controller and Jev recall are separate from contestant usage.'
                    if family=='jev_memory' else 'Six fixed contestant identities across authoring, cross-audit, and fresh cold/warm sessions. Authoring and audit usage have separate executions; ASTRA controller usage is unmeasured.' if family=='six_bot_memory' else 'ASTRA coordinated the experiment. Fixture author and setup/review were separate from scored contestants; their usage is not measured here. Solo means one OpenAI contestant, zero helper bots, with sequential calls carrying that contestant\'s own conversation.')},
                'metrics':{'wall_seconds':finite(summary.get('wall_seconds',summary.get('elapsed_seconds'))),
                           'call_path_seconds':finite(summary.get('call_path_seconds')),'calls':len(calls),
                           'input_tokens':usage['totals']['input_tokens'],'output_tokens':usage['totals']['output_tokens'],
                           'contestant_count':len(roster),'observed_contestant_count':sum(r['calls']>0 for r in roster),
                           'helper_count':0,'provider_count':len({r['provider'] for r in roster})},
                'usage':usage,'calls':calls,'grades':{'initial':grades(initial),'final':grades(final),'note':note},
                'artifacts':artifacts,'notes':notes}

    def output(self, run_id, condition, item):
        reader = Reader();experiment = self._experiment(run_id,reader)
        run,folder,metadata,summary,records,declaration = self._context(experiment,identifier(condition),reader)
        title,content = item,None
        if not isinstance(item,str) or len(item)>200: raise ValueError('Choose a saved output.')
        if item.startswith('call:'):
            parts = item.split(':')
            if len(parts)!=3 or parts[2] not in ('response','prompt','memory'): raise ValueError('Unknown call output.')
            record = next((row for row in records if row['call_id']==parts[1]),None)
            if not record: raise ValueError('That call does not belong to this condition.')
            if parts[2]=='prompt': content = reader.text(run,run/'calls'/parts[1]/'prompt.md')
            else: content = record.get('response' if parts[2]=='response' else 'memory_context')
            if content is None: content = 'This output is not available yet. Refresh while the condition runs.'
            title = parts[1]+' / '+parts[2]
        elif item in ('initial','final'):
            if experiment['family']=='solo_vs_team': content = reader.text(run,folder/(item+'.py'))
            else:
                directory = inside(run,folder/item)
                paths = sorted(directory.rglob('*.py'))
                if len(paths)>30: raise ValueError('Too many candidate files.')
                content = '\n\n'.join('### '+str(path.relative_to(directory))+'\n'+reader.text(run,path) for path in paths)
        elif item in ('initial-grade','final-grade','summary'):
            content = reader.text(run,folder/(item+'.json'))
        elif item=='correction':
            correction = reader.obj(run,run/'review/corrected-grades.json')
            if condition not in correction.get('trials',{}): raise ValueError('No correction for this condition.')
            content = json.dumps({'reason':correction.get('reason'),'performed_at':correction.get('performed_at'),
                                  'condition':correction['trials'][condition]},indent=2)
        else: raise ValueError('Unknown experiment output.')
        if not isinstance(content,str): content = json.dumps(content,indent=2)
        return {'title':title,'content':content[:512000],'truncated':len(content)>512000}
