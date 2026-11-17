"""FairSplit — zero-dependency, SQLite-backed student collaboration site."""
import csv, hashlib, io, json, os, secrets, threading, time
from db import connect, initialize, PG
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('FAIRSPLIT_DB',str(ROOT/'fairsplit.sqlite3')))
LOCK=threading.RLock()
MAX_BODY=240000
RATE={}
def limited(ip, action, max_hits, window=60):
    now=time.time(); key=(ip,action)
    with LOCK:
        hits=[t for t in RATE.get(key,[]) if now-t<window]
        if len(hits)>=max_hits:return True
        hits.append(now); RATE[key]=hits
        if len(RATE)>10000:
            for k,v in list(RATE.items()):
                if not v or now-v[-1]>window:RATE.pop(k,None)
        return False

def digest(s):return hashlib.sha256(s.encode()).hexdigest()

def clean(value,maxlen=100):return str(value or '').strip()[:maxlen]

def safe_csv(s):
    if isinstance(s,str) and s.lstrip().startswith(('=','+','-','@','\t','\r')):return "'"+s
    return s

initialize()

class Handler(BaseHTTPRequestHandler):
    server_version='FairSplit/2'
    def send(self,status,obj,kind='application/json',filename=None):
        data=json.dumps(obj,ensure_ascii=False).encode('utf8') if kind=='application/json' else obj
        self.send_response(status)
        self.send_header('Content-Type',kind+'; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        origin=self.headers.get('Origin','')
        allowed=os.environ.get('FRONTEND_ORIGIN','http://localhost:8000').rstrip('/')
        if origin==allowed:
            self.send_header('Access-Control-Allow-Origin',allowed)
            self.send_header('Vary','Origin')
            self.send_header('Access-Control-Allow-Headers','Content-Type, X-Organizer-Key')
            self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()')
        if filename:self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass
    def fail(self,status,message):self.send(status,{'error':message})
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n<1 or n>MAX_BODY:raise ValueError('Invalid request size')
        obj=json.loads(self.rfile.read(n))
        if not isinstance(obj,dict):raise ValueError('Expected a JSON object')
        return obj
    def find_session(self,c,sid):
        return c.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
    def authorized(self,s,key):return bool(key) and secrets.compare_digest(digest(key),s['admin_hash'])
    def public_info(self,c,s):
        total=c.execute('SELECT COUNT(*) AS n FROM questions WHERE session=?',(s['id'],)).fetchone()['n']
        used=c.execute('SELECT COUNT(*) AS n FROM assignments WHERE session=?',(s['id'],)).fetchone()['n']
        try:settings=json.loads(s['settings'] or '{}')
        except ValueError:settings={}
        info={'id':s['id'],'title':s['title'],'institution':s['institution'],'description':s['description'] or '', 'created':s['created'],'total':total,'assigned':used,'remaining':total-used,'closed':bool(s['closed']),'settings':settings}
        if settings.get('public_roster'):
            order='LOWER(a.alias)' if PG else 'a.alias COLLATE NOCASE'
            info['assignments']=[dict(r) for r in c.execute(f'SELECT a.alias,a.number,q.question FROM assignments a JOIN questions q ON q.session=a.session AND q.number=a.number WHERE a.session=? ORDER BY {order}',(s['id'],))]
        return info
    def do_OPTIONS(self):
        origin=self.headers.get('Origin','')
        if origin != os.environ.get('FRONTEND_ORIGIN','http://localhost:8000').rstrip('/'):
            return self.fail(403,'Origin not allowed')
        return self.send(204,b'','text/plain')
    def do_GET(self):
        u=urlparse(self.path);parts=u.path.strip('/').split('/');qs=parse_qs(u.query)
        if u.path in ('/','/index.html'):
            return self.send(200,b'FairSplit API. Open the Cloudflare Pages site.','text/plain')
        if u.path=='/health':
            try:
                with connect() as c:c.execute('SELECT 1')
                return self.send(200,{'status':'ok'})
            except Exception:return self.fail(503,'Database unavailable')
        if len(parts)>=3 and parts[:2]==['api','sessions']:
            sid=parts[2]
            with connect() as c:
                s=self.find_session(c,sid)
                if not s:return self.fail(404,'This session could not be found.')
                if len(parts)==3:return self.send(200,self.public_info(c,s))
                action=parts[3] if len(parts)==4 else ''
                if action=='mine':
                    token=qs.get('token',[''])[0]
                    if not token:return self.fail(403,'A participant token is required.')
                    r=c.execute('SELECT alias,number,participant_note FROM assignments WHERE session=? AND token=?',(sid,token)).fetchone()
                    if not r:return self.fail(404,'No assignment found for this device.')
                    q=c.execute('SELECT question FROM questions WHERE session=? AND number=?',(sid,r['number'])).fetchone()
                    return self.send(200,{'alias':r['alias'],'number':r['number'],'question':q['question'],'note':r['participant_note'] or '', 'token':token})
                if action in ('admin','export','export_questions'):
                    key=self.headers.get('X-Organizer-Key') or qs.get('key',[''])[0]
                    if not self.authorized(s,key):return self.fail(403,'Organizer link required.')
                    questions=[dict(r) for r in c.execute('SELECT number,question FROM questions WHERE session=? ORDER BY number',(sid,))]
                    rows=[dict(r) for r in c.execute('SELECT alias,number,gender,year,faculty,consent,age_band,programme,created FROM assignments WHERE session=? ORDER BY number',(sid,))]
                    lookup={x['number']:x['question'] for x in questions}
                    for r in rows:
                        r['question']=lookup.get(r['number'],'')
                        if not r['consent']:
                            for field in ('gender','year','faculty','age_band','programme'):r[field]=''
                    stats={'respondents':len(rows),'consented':sum(int(bool(x['consent'])) for x in rows),'questions':len(questions),'remaining':len(questions)-len(rows)}
                    if action=='admin':
                        feedback=[dict(x) for x in c.execute('SELECT rating,comment,created FROM feedback WHERE session=?',(sid,))]
                        stats['feedback_count']=len(feedback)
                        stats['average_rating']=round(sum(f['rating'] for f in feedback)/len(feedback),1) if feedback else None
                        return self.send(200,{'session':self.public_info(c,s),'questions':questions,'assignments':rows,'stats':stats,'feedback':feedback})
                    out=io.StringIO();writer=csv.writer(out)
                    if action=='export_questions':
                        writer.writerow(['question_number','question'])
                        for x in questions:writer.writerow([x['number'],safe_csv(x['question'])])
                        return self.send(200,out.getvalue().encode('utf-8-sig'),'text/csv',f'fairsplit-questions-{sid}.csv')
                    writer.writerow(['session_id','session_title','institution','alias','question_number','question','consented_to_research','gender','study_level','faculty','age_band','programme','assigned_timestamp'])
                    for r in rows:writer.writerow([sid,safe_csv(s['title']),safe_csv(s['institution']),safe_csv(r['alias']),r['number'],safe_csv(r['question']),r['consent'],safe_csv(r['gender']),safe_csv(r['year']),safe_csv(r['faculty']),safe_csv(r['age_band']),safe_csv(r['programme']),r['created']])
                    return self.send(200,out.getvalue().encode('utf-8-sig'),'text/csv',f'fairsplit-assignments-{sid}.csv')
        return self.fail(404,'Not found.')
    def do_POST(self):
        u=urlparse(self.path);parts=u.path.strip('/').split('/')
        try:data=self.body()
        except (ValueError,UnicodeError,json.JSONDecodeError):return self.fail(400,'Please send valid JSON.')
        if parts==['api','sessions']:
            if limited(self.client_address[0],'create',8,3600):return self.fail(429,'Too many sessions created. Try again later.')
            title=clean(data.get('title'),120);questions=data.get('questions',[]);aliases=data.get('aliases',[])
            if not title or not isinstance(questions,list) or not 1<=len(questions)<=150 or any(not isinstance(v,str) or not v.strip() or len(v)>1800 for v in questions):return self.fail(400,'Enter a title and 1–150 questions (one item per line).')
            if not isinstance(aliases,list) or len(aliases)>150 or any(not isinstance(v,str) or not 1<=len(v.strip())<=40 for v in aliases):return self.fail(400,'Enter up to 150 student aliases, one per line.')
            aliases=[v.strip() for v in aliases]
            if len({v.casefold() for v in aliases})!=len(aliases):return self.fail(400,'Each student alias must be unique.')
            if aliases and len(aliases)!=len(questions):return self.fail(400,'The number of student aliases must match the number of assignment places.')
            settings={'collect_demographics':bool(data.get('collect_demographics',True)),'allow_feedback':bool(data.get('allow_feedback',True)),'show_progress':bool(data.get('show_progress',True)),'public_roster':bool(data.get('public_roster',bool(aliases)))}
            sid=secrets.token_urlsafe(10);key=secrets.token_urlsafe(32)
            with connect() as c:
                c.execute('INSERT INTO sessions (id,admin_hash,title,institution,created,description,settings,closed) VALUES(?,?,?,?,?,?,?,0)',(sid,digest(key),title,clean(data.get('institution'),120),int(time.time()),clean(data.get('description'),500),json.dumps(settings)))
                c.executemany('INSERT INTO questions(session,number,question) VALUES(?,?,?)',[(sid,i,v.strip()) for i,v in enumerate(questions,1)])
                if aliases:
                    numbers=list(range(1,len(questions)+1));secrets.SystemRandom().shuffle(numbers)
                    now=int(time.time())
                    c.executemany('INSERT INTO assignments(session,token,alias,number,gender,year,faculty,consent,created,age_band,programme) VALUES(?,?,?,?,?,?,?,?,?,?,?)',[(sid,secrets.token_urlsafe(32),alias,number,'','','',0,now,'','') for alias,number in zip(aliases,numbers)])
            return self.send(201,{'id':sid,'admin_key':key})
        if len(parts)==4 and parts[:2]==['api','sessions']:
            sid=parts[2];action=parts[3]
            with LOCK,connect() as c:
                if PG:c.execute('SELECT id FROM sessions WHERE id=? FOR UPDATE',(sid,))
                s=self.find_session(c,sid)
                if not s:return self.fail(404,'Session not found.')
                if action=='join':
                    if limited(self.client_address[0],'join',25,3600):return self.fail(429,'Too many requests. Try again later.')
                    alias=clean(data.get('alias'),41)
                    if not 1<=len(alias)<=40:return self.fail(400,'Enter an alias with 1–40 characters.')
                    if s['closed']:return self.fail(409,'This session is closed to new participants.')
                    if c.execute('SELECT 1 FROM assignments WHERE session=? AND alias=? COLLATE NOCASE',(sid,alias)).fetchone():return self.fail(409,'This alias is already taken. Use your original device to retrieve your draw, or choose another alias.')
                    available=[dict(x) for x in c.execute('SELECT q.number,q.question FROM questions q LEFT JOIN assignments a ON a.session=q.session AND a.number=q.number WHERE q.session=? AND a.number IS NULL',(sid,))]
                    if not available:return self.fail(409,'All available questions have been assigned.')
                    chosen=secrets.choice(available);token=secrets.token_urlsafe(32);consent=int(data.get('consent') is True and data.get('age_band') != 'Under 18')
                    settings=json.loads(s['settings'] or '{}');demographics=consent and settings.get('collect_demographics',True)
                    fields={k:clean(data.get(k),maxlen) if demographics else '' for k,maxlen in [('gender',40),('year',60),('faculty',100),('age_band',40),('programme',120)]}
                    c.execute('INSERT INTO assignments(session,token,alias,number,gender,year,faculty,consent,created,age_band,programme) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(sid,token,alias,chosen['number'],fields['gender'],fields['year'],fields['faculty'],consent,int(time.time()),fields['age_band'],fields['programme']))
                    return self.send(201,{'alias':alias,**chosen,'token':token})
                if action=='withdraw':
                    token=clean(data.get('token'),120)
                    result=c.execute('UPDATE assignments SET consent=0,gender=NULL,year=NULL,faculty=NULL,age_band=NULL,programme=NULL WHERE session=? AND token=?',(sid,token))
                    if result.rowcount!=1:return self.fail(403,'A valid participant token is required.')
                    return self.send(200,{'withdrawn':True})
                if action=='feedback':
                    token=clean(data.get('token'),120)
                    r=c.execute('SELECT token FROM assignments WHERE session=? AND token=?',(sid,token)).fetchone()
                    if not r:return self.fail(403,'An assignment is required before leaving feedback.')
                    rating=data.get('rating')
                    if type(rating) is not int or not 1<=rating<=5:return self.fail(400,'Rating must be from 1 to 5.')
                    c.execute('INSERT INTO feedback(session,assignment_token,rating,comment,created) VALUES(?,?,?,?,?) ON CONFLICT(session,assignment_token) DO UPDATE SET rating=excluded.rating,comment=excluded.comment,created=excluded.created',(sid,token,rating,clean(data.get('comment'),400),int(time.time())))
                    return self.send(200,{'saved':True})
                if action=='update':
                    key=self.headers.get('X-Organizer-Key') or clean(data.get('key'),150)
                    if not self.authorized(s,key):return self.fail(403,'Organizer link required.')
                    closed=data.get('closed')
                    if not isinstance(closed,bool):return self.fail(400,'Expected closed=true or closed=false.')
                    c.execute('UPDATE sessions SET closed=? WHERE id=?',(int(closed),sid))
                    return self.send(200,{'closed':closed})
        return self.fail(404,'Not found.')
    def log_message(self,fmt,*args):
        if os.environ.get('FAIRSPLIT_QUIET')!='1':super().log_message(fmt,*args)

if __name__=='__main__':
    port=int(os.environ.get('PORT','8000'))
    print(f'FairSplit → http://localhost:{port}',flush=True)
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
