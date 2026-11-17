"""Integration smoke test; runs a disposable SQLite backend on a random local port."""
import json, os, subprocess, sys, tempfile, time, urllib.request, urllib.error
from pathlib import Path
from socket import socket
root=Path(__file__).resolve().parents[1]
with socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
with tempfile.TemporaryDirectory() as tmp:
    env={**os.environ,'PORT':str(port),'FAIRSPLIT_DB':str(Path(tmp)/'test.db'),'FRONTEND_ORIGIN':'http://localhost:8001','FAIRSPLIT_QUIET':'1'}
    env.pop('DATABASE_URL',None)
    proc=subprocess.Popen([sys.executable,'server.py'],cwd=root/'backend',env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        base=f'http://localhost:{port}'
        for i in range(50):
            try:urllib.request.urlopen(base+'/health',timeout=1);break
            except Exception:time.sleep(.1)
        else:raise AssertionError('Backend did not start: '+str(proc.stderr.read()))
        def call(path,data=None,headers=None):
            payload=None if data is None else json.dumps(data).encode()
            req=urllib.request.Request(base+path,data=payload,headers={**(headers or {}),**({'Content-Type':'application/json'} if payload else {})})
            try:
                with urllib.request.urlopen(req,timeout=4) as resp:return resp.status,json.loads(resp.read())
            except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
        status,created=call('/api/sessions',{'title':'Aggregation I','questions':['a','b','c','d'],'collect_demographics':True})
        assert status==201,created
        sid=created['id'];key=created['admin_key']
        numbers=set()
        for i in range(4):
            status,assigned=call(f'/api/sessions/{sid}/join',{'alias':f'student{i}','consent':i%2==0,'gender':'Woman','year':'Postgraduate'})
            assert status==201,assigned
            assert assigned['number'] not in numbers
            numbers.add(assigned['number'])
            status,mine=call(f'/api/sessions/{sid}/mine?token='+assigned['token'])
            assert status==200 and mine['number']==assigned['number']
        assert numbers=={1,2,3,4}
        assert call(f'/api/sessions/{sid}/join',{'alias':'extra'})[0]==409
        assert call(f'/api/sessions/{sid}/admin')[0]==403
        status,dashboard=call(f'/api/sessions/{sid}/admin',headers={'X-Organizer-Key':key})
        assert status==200 and len(dashboard['assignments'])==4
        assert sum(bool(x['gender']) for x in dashboard['assignments'])==2
        status,one=call(f'/api/sessions/{sid}/join',{'alias':'extra'})
        # Verify consent withdrawal on an existing participant without changing the draw.
        status,created2=call('/api/sessions',{'title':'Withdrawal','questions':['one']})
        assert status==201
        other=created2['id']
        status,entry=call(f'/api/sessions/{other}/join',{'alias':'test','consent':True,'gender':'Woman'})
        assert status==201
        assert call(f'/api/sessions/{other}/withdraw',{'token':entry['token']})[0]==200
        status,d2=call(f'/api/sessions/{other}/admin',headers={'X-Organizer-Key':created2['admin_key']})
        assert status==200 and d2['assignments'][0]['gender']==''
        assert call(f'/api/sessions/{sid}/update',{'closed':True},headers={'X-Organizer-Key':key})[0]==200
        assert call(f'/api/sessions/{sid}/join',{'alias':'new'})[0]==409
        status,direct=call('/api/sessions',{'title':'Direct split','questions':['q1','q1','q2','q2'],'aliases':['Tariro','Alex','Munya','Rudo'],'public_roster':True})
        assert status==201,direct
        status,public=call(f"/api/sessions/{direct['id']}")
        assert status==200 and len(public['assignments'])==4
        assert {x['alias'] for x in public['assignments']}=={'Tariro','Alex','Munya','Rudo'}
        assert sorted(x['question'] for x in public['assignments'])==['q1','q1','q2','q2']
        assert call('/api/sessions',{'title':'Bad direct split','questions':['q1'],'aliases':['A','B']})[0]==400
        print('PASS: health, self-join, direct split, public roster, admin protection, consent, close')
    finally:
        proc.terminate();proc.wait(timeout=5)
