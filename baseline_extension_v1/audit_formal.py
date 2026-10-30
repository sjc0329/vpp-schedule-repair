"""Read-only replay of completed formal comparisons. No optimization calls."""
from pathlib import Path
import csv,json,hashlib,sys
import extension_prevalidate as a
B=Path(__file__).resolve().parent;O=B/'formal_results';ROOT=a.ROOT
assert (O/'summary.json').exists(),'Formal run is incomplete'
protocol=a.read(O/'protocol.json');rows=list(csv.DictReader((O/'metrics.csv').open()))
lookup={(r['case_id'],float(r['alpha']),r['method']):r for r in rows}
assert len(lookup)==540
checked=0;maximum_error=0.;backup_status={};backup_n={'lower':0,'equal':0,'higher':0}
for path in sorted((O/'cases').glob('*.json')):
 rec=a.read(path);s=a.read(ROOT/rec['source']['input_path']);old=a.read(ROOT/rec['source']['own_path'])
 u=a.verify(s,old['configurations']['economic']['detail']['plan'])['cost']
 for label,methods in rec['runs'].items():
  alpha={'alpha_0':0.,'alpha_0p1':.1,'alpha_0p3':.3}[label];cap=u+alpha*.8*s['event']['task']['energy']
  own=a.checked(s,old['configurations'][label]['detail']['selected_plan'],cap)
  for method,run in methods.items():
   row=lookup[(s['id'],alpha,method)];v=a.checked(s,run['selected_plan'],cap)
   if method=='segan':
    first=run['first_pass'];second=run['backup_pass'];initial=a.checked(s,first['plan'],cap)
    backup_status[second['status']]=backup_status.get(second['status'],0)+1
    difference=v['n_adjust']-initial['n_adjust'];backup_n['lower' if difference<0 else 'higher' if difference>0 else 'equal']+=1
    assert v['d_vpp']<=initial['d_vpp']+2e-7
    if run['backup_accepted']:a.checked(s,run['selected_plan'],cap,initial['d_vpp'],run['candidate'])
    for r in [first,second]:
     if r['plan'] is not None:
      rv=a.checked(s,r['plan'],cap);err=abs(rv['cost']+8*rv['d_vpp']-r['objective']);assert err<2e-5;maximum_error=max(maximum_error,err)
   else:
    err=abs(a.settlement_value(s,run['selected_plan'],run['beta_minus'],run['beta_plus'])-run['solve']['objective']);assert err<2e-5;maximum_error=max(maximum_error,err)
   for k,f in [('d','d_vpp'),('cost','cost'),('n','n_adjust'),('n_engineering','n_engineering'),('d_edge','d_edge')]:assert abs(v[f]-float(row[k]))<1e-9
   for k,f in [('own_d','d_vpp'),('own_cost','cost'),('own_n','n_adjust'),('own_n_engineering','n_engineering')]:assert abs(own[f]-float(row[k]))<1e-9
   checked+=1
assert checked==540
assert all(a.sha(ROOT/k)==v for k,v in protocol['protected_sha256'].items())
assert a.sha(B/'extension_formal.py')==protocol['runner_sha256']
assert a.sha(B/'extension_prevalidate.py')==protocol['adapter_sha256']
a.write(O/'audit.json',dict(verified_outputs=checked,extra_solver_calls=0,max_objective_error=maximum_error,backup_status=backup_status,backup_actual_n_changes=backup_n,original_files_unchanged=len(protocol['protected_sha256'])))
print(json.dumps(a.read(O/'audit.json'),indent=2))
