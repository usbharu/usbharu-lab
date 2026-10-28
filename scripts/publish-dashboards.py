#!/usr/bin/env python3
"""Publish only the two usbharu-lab dashboards; read token from a host file."""
import argparse,json,stat,sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,build_opener,HTTPRedirectHandler
class NoRedirect(HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): return None
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--token-file',default='/etc/komodo/secrets/observability/grafana-api-token')
parser.add_argument('--directory',type=Path,default=Path(__file__).resolve().parents[1]/'stacks/observability/dashboards')
args=parser.parse_args()
token_path=Path(args.token_file)
if token_path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
 sys.exit('Token file must be accessible only to its owner (chmod 600).')
token=token_path.read_text().strip()
if not token: sys.exit('Token file is empty.')
base='https://usbharulab.grafana.net'
opener=build_opener(NoRedirect())
def api(path,body=None):
 request=Request(base+path,None if body is None else json.dumps(body).encode(),{'Authorization':'Bearer '+token,'Content-Type':'application/json'})
 try:
  with opener.open(request,timeout=30) as r: return json.load(r)
 except HTTPError as exc:
  sys.exit('Grafana API failed: HTTP '+str(exc.code)+' at '+path)
datasources=api('/api/datasources')
selected={}
for key,kind in [('metrics','prometheus'),('logs','loki'),('traces','tempo')]:
 options=[d for d in datasources if d['type']==kind]
 if not options: sys.exit('Missing Grafana data source type: '+kind)
 options.sort(key=lambda d:(not d.get('isDefault',False),'usbharulab' not in d['name'].lower(),d['name']))
 selected[key]=options[0]
for uid in ['usbharulab-infra','usbharulab-ocms']:
 dashboard=json.loads((args.directory/(uid+'.json')).read_text())
 if dashboard['uid']!=uid: sys.exit('Unexpected dashboard UID.')
 for variable in dashboard['templating']['list']:
  if variable['name'] in selected:
   ds=selected[variable['name']]
   variable['current']={'text':ds['name'],'value':ds['uid'],'selected':True}
 result=api('/api/dashboards/db',{'dashboard':dashboard,'overwrite':True,'message':'Apply usbharu-lab observability dashboard from Git'})
 print(base+result['url'])
