"""Local SCC catalog UI using the SC Helper bytecode core.
Run on Windows with CPython 3.11 or 3.13 from the project directory.
"""
import ctypes
import ctypes.wintypes
import base64
import http.client
import html
import json
import marshal
import os
import re
import secrets
import shutil
import sys
import subprocess
import threading
import time
import types
import urllib.parse
import webbrowser
import xml.etree.ElementTree as ET
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
from feed_cache import get_feed, feed_id_for, get_image

ROOT = Path(__file__).resolve().parent
SECRETS = ROOT / 'secrets'
HELPER = ROOT / 'sc-helper' / 'service.sc.helper'
if not HELPER.exists():
    sys.exit('Missing sc-helper/service.sc.helper directory')

def load_sc_core():
    version = ET.parse(HELPER / 'addon.xml').getroot().get('version')
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}-win_amd64_py.dat"
    core_data = HELPER / 'resources' / 'lib' / f'_core-{version}-{tag}'
    if not core_data.is_file():
        sys.exit(f'Missing SC Helper bytecode dependency: {core_data.name}')
    try:
        encoded = base64.b64decode(core_data.read_bytes(), validate=True)
        decoded = zlib.decompress(bytes(value ^ 0xC5 for value in encoded))
        module = types.ModuleType('_core')
        exec(marshal.loads(decoded), module.__dict__)
    except Exception as exc:
        sys.exit(f'Could not load SC Helper bytecode dependency: {exc}')
    sys.modules['_core'] = module
    return module


_core = load_sc_core()
LOCAL_PORT = 8765

BASE = 'https://stream-cinema.online/kodi'
KRA = 'https://api.kra.sk'
AGENT = 'Kodi/21.2 (Linux; Android) (sk; ver2.6.10.3+k19)'
UUID = (ROOT / 'sc-uuid.txt').read_text(encoding='utf-8').strip() if (ROOT / 'sc-uuid.txt').exists() else secrets.token_hex(16)
TOKEN = (SECRETS / 'sc.json').read_text(encoding='utf-8').strip()
if not TOKEN or not re.fullmatch(r'[a-zA-Z0-9]+', TOKEN):
    sys.exit('Missing/invalid SCC token in secrets/sc.json')
N, E = _core._load_key()
if not (isinstance(N, int) and isinstance(E, int)):
    sys.exit('Could not load SC Helper RSA public key')
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': AGENT, 'X-Uuid': UUID, 'X-AUTH-TOKEN': TOKEN})
KRA_SESSION = requests.Session()
KRA_ID = None
KRA_USER = None
KRA_PASSWORD = None
CRED_FILE = SECRETS / "kra-session.dpapi"
LOGIN_FILE = SECRETS / "creds.txt"
CSRF = secrets.token_urlsafe(24)
LOCK = threading.Lock()


def catalog(path, extra=None):
    if not path.startswith('/') or path.startswith('//') or '\\' in path or '://' in path:
        raise ValueError('Invalid catalog path')
    parsed = urllib.parse.urlsplit(path)
    params = urllib.parse.parse_qsl(parsed.query)
    extras = list((extra.items() if isinstance(extra, dict) else extra) if extra else [])
    defaults = [('DV', '1'), ('HDR', '1'), ('lang', 'sk'), ('pro', 'kraska'), ('skin', 'estuary'), ('uid', UUID), ('ver', '2.0')]
    overridden = {key for key, _ in extras}
    params = [(key, value) for key, value in params if key not in overridden and key not in {name for name, _ in defaults}]
    params.extend((key, value) for key, value in defaults if key not in overridden)
    params.extend(extras)
    r = SESSION.get(BASE + parsed.path, params=sorted(params), timeout=25)
    r.raise_for_status()
    return r.json()


FILTER_PARAMS = frozenset('s typ q la HDR DV atmos hevc ste dub tit r y mu co ge ca of od m kwd b f page limit ms'.split())
FILTER_INTEGER_PARAMS = frozenset('HDR DV atmos hevc ste dub tit m page limit ms'.split())


def filter_params(query):
    """Validate and preserve repeated, allowlisted SCC filter parameters."""
    result = []
    for key, values in query.items():
        if key.endswith('[]') and key[:-2] in ('mu', 'co', 'ge', 'ca'):
            key = key[:-2]
        if key not in FILTER_PARAMS or len(values) > 20:
            raise ValueError('Invalid filter parameter')
        for value in values:
            if len(value) > 100 or any(ord(char) < 32 for char in value):
                raise ValueError('Invalid filter value')
            if key in FILTER_INTEGER_PARAMS and (not value.isdecimal() or len(value) > 6):
                raise ValueError('Invalid numeric filter value')
            if key in ('mu', 'co', 'ge', 'ca') and not re.fullmatch(r'!?\d{1,12}', value):
                raise ValueError('Invalid facet identifier')
            if key in ('r', 'y', 'b', 'f') and not re.fullmatch(r'[<>]?\d{1,14}(?::\d{1,14})?', value):
                raise ValueError('Invalid range filter')
            if key in ('of',) and not re.fullmatch(r'[a-zA-Z0-9._-]{1,40}', value):
                raise ValueError('Invalid sort field')
            if key == 'od' and value not in ('asc', 'desc'):
                raise ValueError('Invalid sort direction')
            wire_key = key + '[]' if key in ('mu', 'co', 'ge', 'ca') else key
            result.append((wire_key, value))
    if len(result) > 60:
        raise ValueError('Too many filter values')
    return result


# Windows DPAPI: encrypted for the current Windows user, never store password.
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data):
    buf = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p

def dpapi_encrypt(data):
    inp, keep = _blob(data)
    out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), None, None, None, None, 0x01, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def dpapi_decrypt(data):
    inp, keep = _blob(data)
    out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0x01, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def load_session():
    global KRA_ID
    if CRED_FILE.exists():
        try:
            KRA_ID = dpapi_decrypt(CRED_FILE.read_bytes()).decode('utf-8')
        except Exception:
            KRA_ID = None


def save_session(value):
    global KRA_ID
    KRA_ID = value
    CRED_FILE.write_bytes(dpapi_encrypt(value.encode('utf-8')))


def login(username, password):
    r = KRA_SESSION.post(KRA + '/api/user/login', json={'data': {'username': username, 'password': password}}, timeout=25)
    r.raise_for_status()
    value = r.json().get('session_id')
    if not value:
        raise ValueError('kra.sk nevratilo session_id')
    save_session(value)


def login_from_file():
    if not LOGIN_FILE.is_file():
        return False
    line = LOGIN_FILE.read_text(encoding='utf-8-sig').strip()
    if not line or '\n' in line or '\r' in line or ':' not in line:
        raise ValueError('Invalid secrets/creds.txt format; expected username:password on one line')
    username, password = line.split(':', 1)
    if not username.strip() or not password.strip():
        raise ValueError('Invalid secrets/creds.txt format; username and password are required')
    login(username.strip(), password.strip())
    return True


def resolve(path):
    response = catalog(path)
    version = response.get('version')
    raw = response.get('v' + str(version))
    if version is None or not isinstance(raw, str):
        raise ValueError('SCC resolver did not return an ident')
    decrypted = _core.decrypt_ident('v' + str(version) + ':' + raw, n=N, e=E)
    if not isinstance(decrypted, str) or not re.fullmatch(r'[a-zA-Z0-9]{12}', decrypted):
        raise ValueError('SC Helper could not decode ident')
    with LOCK:
        if not KRA_ID:
            if not login_from_file():
                raise PermissionError('LOGIN_REQUIRED')
        for attempt in range(2):
            r = KRA_SESSION.post(KRA + '/api/file/download', json={'session_id': KRA_ID, 'data': {'ident': decrypted}}, timeout=30)
            try:
                data = r.json()
                msg = data.get('msg', '')
            except Exception:
                data, msg = {}, ''
            if r.ok:
                link = (data.get('data') or {}).get('link')
                if link and urllib.parse.urlsplit(link).scheme == 'https':
                    return link
            expired = r.status_code in (401, 403) or 'session' in str(msg).lower() or 'login' in str(msg).lower()
            if expired and attempt == 0 and login_from_file():
                continue
            if expired:
                raise PermissionError('LOGIN_REQUIRED')
            raise ValueError('kra.sk download: HTTP %s %s' % (r.status_code, str(msg)[:100]))


def find_vlc():
    candidates = [shutil.which('vlc.exe'), shutil.which('vlc')]
    for env_name in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        base = os.environ.get(env_name)
        if base:
            candidates.append(str(Path(base) / 'VideoLAN' / 'VLC' / 'vlc.exe'))
            candidates.append(str(Path(base) / 'Programs' / 'VideoLAN' / 'VLC' / 'vlc.exe'))
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for key_name in (r'SOFTWARE\VideoLAN\VLC', r'SOFTWARE\WOW6432Node\VideoLAN\VLC'):
                try:
                    with winreg.OpenKey(hive, key_name) as key:
                        install_dir, _ = winreg.QueryValueEx(key, 'InstallDir')
                    candidates.append(str(Path(install_dir) / 'vlc.exe'))
                except OSError:
                    pass
    except ImportError:
        pass
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError('VLC sa nenašlo. Nainštaluj VLC alebo pridaj vlc.exe do PATH.')


def open_in_vlc(url):
    parsed = urllib.parse.urlsplit(url)
    if len(url) > 8192 or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Neplatná HTTPS adresa streamu')
    subprocess.Popen([str(find_vlc()), url], close_fds=True)


def open_vlc_playlist(tracks):
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= 100:
        raise ValueError('Playlist musí obsahovať 1 až 100 položiek')
    lines = ['#EXTM3U']
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError('Invalid playlist track')
        url = track.get('url', '')
        if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 32 for c in url):
            raise ValueError('Invalid stream URL')
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Neplatná HTTPS adresa streamu')
        title = str(track.get('title', 'Stream')).replace('\r', ' ').replace('\n', ' ')[:500]
        lines.extend(['#EXTINF:-1,' + title, url])
    vlc = find_vlc()
    directory = ROOT / '.cache' / 'playlists'
    directory.mkdir(parents=True, exist_ok=True)
    # Old launch files contain expiring playback links, not the saved queue.
    for old in directory.glob('queue-*.m3u8'):
        try:
            if time.time() - old.stat().st_mtime > 86400:
                old.unlink()
        except OSError:
            pass
    path = directory / ('queue-' + secrets.token_hex(12) + '.m3u8')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    try:
        subprocess.Popen([str(vlc), str(path)], close_fds=True)
    except OSError:
        path.unlink(missing_ok=True)
        raise


PAGE = r'''<!doctype html><html lang="sk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SCC · lokálne</title><style>
:root{font-family:system-ui,-apple-system,Segoe UI,sans-serif;color-scheme:dark;background:#101318;color:#e6eaf1}body{max-width:1440px;margin:0 auto;padding:28px 18px}h1{font-size:1.6rem;margin:0 0 8px}p{color:#9aa6b7}button,input,select{font:inherit;border-radius:9px;padding:10px 13px}input,select{background:#1d2430;color:white;border:1px solid #3a4556}input{flex:1;min-width:180px}button{background:#3478ef;color:white;border:0;cursor:pointer}button:hover{filter:brightness(1.15)}button.secondary{background:#293449}.toolbar{display:flex;gap:9px;flex-wrap:wrap;margin:18px 0}.tabs{display:flex;gap:8px;margin:12px 0}.tabs button.selected{background:#3478ef}.tabs button{background:#293449}.item{padding:13px 0;border-bottom:1px solid #303a48;display:flex;justify-content:space-between;align-items:center;gap:12px}.item .name{font-weight:600}.meta{font-size:.83rem;color:#a0adbf;margin-top:4px}.actions{display:flex;gap:8px;flex-wrap:wrap}.actions button{font-size:.85rem;padding:8px 10px}#notice{min-height:20px;color:#a9b7c8;margin:10px 0}#crumb{margin:14px 0;color:#a9b7c8}#crumb button{background:transparent;color:#91b8ff;padding:3px}.small{font-size:.83rem}a{color:#8cb7ff}#linkbox{word-break:break-all;padding:14px;background:#1d2430;border-radius:9px;margin:14px 0;display:none}.layout{display:grid;grid-template-columns:minmax(0,3fr) minmax(250px,1fr);gap:30px;align-items:start}.history{position:sticky;top:18px;background:#171c24;border:1px solid #303a48;border-radius:12px;padding:16px}.history h2{font-size:1.15rem;margin:0}.history .tabs{margin:12px 0}.history-list .item{padding:10px 0}.history-list button{background:transparent;color:#91b8ff;text-align:left;padding:2px 0}.history-empty{font-size:.9rem;color:#9aa6b7}@media(max-width:850px){.layout{grid-template-columns:1fr}.history{position:static}}
</style></head><body><div class="layout"><main>
<h1>SCC · lokálne</h1><p>Vyhľadaj film alebo seriál, vyber súbor a získaj priamy kra.sk link.</p>
<div id="auth" class="toolbar"><span id="authstate">Overujem uloženú session...</span><button id="loginBtn" type="button" class="secondary">Prihlásiť kra.sk</button></div><div id="loginPanel" style="display:none"><form id="loginForm" class="toolbar"><input id="username" placeholder="kra.sk používateľ" autocomplete="username" required><input id="password" type="password" placeholder="Heslo" autocomplete="current-password" required><button>Prihlásiť</button></form><p class="small">Heslo sa neukladá. Session je šifrovaná cez Windows DPAPI.</p></div>
<div class="tabs"><button id="movies" class="selected" onclick="mode='movies';setMode()">Filmy</button><button id="series" onclick="mode='series';setMode()">Seriály</button></div>
<form class="toolbar" id="searchForm"><input id="query" placeholder="Napr. Futurama, Simpsons" autocomplete="off" required><button>Hľadať</button></form>
<div id="notice"></div><div id="crumb"></div><div id="results"></div><div id="linkbox"></div>
</main><aside class="history"><h2>História</h2><div class="tabs"><button id="historyMovies" class="selected" type="button">Filmy</button><button id="historySeries" type="button">Seriály</button></div><div id="historyList" class="history-list"></div></aside></div>
<script>
let mode='movies', stack=[], lastQuery='', historyMode='movies';const csrf=__CSRF__;
const results=document.getElementById('results'), notice=document.getElementById('notice'), crumb=document.getElementById('crumb'), linkbox=document.getElementById('linkbox');
try{const saved=JSON.parse(localStorage.getItem('scc-ui-state')||'{}');if(saved.searchMode==='series')mode='series';if(saved.historyMode==='series')historyMode='series'}catch{}
function readHistory(){try{const value=JSON.parse(localStorage.getItem('scc-history')||'{}');return {movies:Array.isArray(value.movies)?value.movies:[],series:Array.isArray(value.series)?value.series:[]}}catch{return {movies:[],series:[]}}}
function saveUiState(){try{localStorage.setItem('scc-ui-state',JSON.stringify({searchMode:mode,historyMode}))}catch{}}
function addHistory(type,title,path,navigation=[]){if(!path||(!path.startsWith('/FGet/')&&!path.startsWith('/Play/')))return;try{const data=readHistory(),items=data[type];data[type]=[{title,path,navigation},...items.filter(item=>item.path!==path&&!(type==='series'&&path.startsWith(item.path.replace(/\/$/,'')+'/')))].slice(0,10);historyMode=type;localStorage.setItem('scc-history',JSON.stringify(data));saveUiState();renderHistory()}catch{}}
function renderHistory(){const box=document.getElementById('historyList'),items=readHistory()[historyMode];box.replaceChildren();document.getElementById('historyMovies').classList.toggle('selected',historyMode==='movies');document.getElementById('historySeries').classList.toggle('selected',historyMode==='series');if(!items.length){box.append(el('div','history-empty','Zatiaľ bez položiek.'));return}items.forEach(item=>{const row=el('div','item'),a=el('button',null,item.title||'Bez názvu');a.type='button';a.onclick=()=>openHistory(item);row.append(a);box.append(row)})}
async function findHistoryPath(items,target,trail=[],depth=0){const wanted=target.split('?')[0].replace(/\/+$/,'');for(let i=0;i<items.length;i++){const item=items[i],path=String(item.url||''),candidate=path.split('?')[0].replace(/\/+$/,'');if(!candidate)continue;const next=[...trail,{path,label:locationLabel(item,menuContext(items),i)}];if(candidate===wanted)return next;if(depth<8&&wanted.startsWith(candidate+'/')){try{const data=await api('/api/browse?'+new URLSearchParams({path}));const found=await findHistoryPath(data.menu||[],target,next,depth+1);if(found)return found}catch{}}}return null}
async function openHistory(item){mode=historyMode;document.getElementById(mode).click();const titleParts=String(item.title||'').split(' — '),show=titleParts[0]||item.title||'Položky';lastQuery=show;document.getElementById('query').value=show;let navigation=Array.isArray(item.navigation)?item.navigation.filter(entry=>entry&&typeof entry.path==='string'&&typeof entry.label==='string'):[];if(!navigation.length){try{const id=mode==='movies'?'search-movies':'search-series',data=await api('/api/search?'+new URLSearchParams({q:show,id}));navigation=await findHistoryPath(data.menu||[],item.path)||[]}catch{}}stack=navigation.slice(0,-1);const leafLabel=navigation.at(-1)?.label||item.title||'Položky';openPath(item.path,undefined,leafLabel)}
document.getElementById('historyMovies').onclick=()=>{historyMode='movies';saveUiState();renderHistory()};document.getElementById('historySeries').onclick=()=>{historyMode='series';saveUiState();renderHistory()};renderHistory();
function setMode(){document.getElementById('movies').classList.toggle('selected',mode==='movies');document.getElementById('series').classList.toggle('selected',mode==='series');saveUiState();results.replaceChildren();stack=[];crumb.replaceChildren();linkbox.style.display='none'}
function showNotice(s){notice.textContent=s}
async function refreshAuth(){try{const j=await api('/api/status');document.getElementById('authstate').textContent=j.session?'Kra.sk session uložená':'Kra.sk prihlásenie potrebné';document.getElementById('loginPanel').style.display=j.session?'none':'block'}catch(e){showNotice(e.message)}}
document.getElementById('loginBtn').onclick=()=>{const p=document.getElementById('loginPanel');p.style.display=p.style.display==='none'?'block':'none'};
document.getElementById('loginForm').onsubmit=async e=>{e.preventDefault();const u=document.getElementById('username').value,p=document.getElementById('password').value;try{await api('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});document.getElementById('password').value='';showNotice('Prihlásenie úspešné');refreshAuth()}catch(e){showNotice('Prihlásenie: '+e.message)}};
async function api(path,options={}){const r=await fetch(path,{...options,headers:{'X-Local-Token':csrf,...(options.headers||{})}});let j;try{j=await r.json()}catch{throw Error('Neplatná odpoveď servera')}if(!r.ok)throw Error(j.error||('HTTP '+r.status));return j}
function clean(s){return String(s||'').replace(/\[\/?(?:B|I|COLOR[^\]]*)\]/gi,'').trim()}
function name(item){
 const info=item.info||{};
 const candidates=[info.title,info.originaltitle,info.name,item.label,item.name,item.title]
  .map(value=>clean(value)).filter(value=>value&&!/^\/(?:Play|FGet)\//i.test(value)&&!/^https?:\/\//i.test(value));
 if(candidates.length)return candidates[0];
 const query=clean(lastQuery),year=info.year||item.year;
 if(query)return query+(year?' ('+year+')':'');
 return clean(item.url||'Bez názvu');
}
function locationLabel(item, context, index){
 const path=String(item.url||'').split('?')[0], info=item.info||{};
 let match=path.match(/^\/FGet\/[^/]+\/(\d+)(?:\/(\d+))?\/?$/);
 if(!match&&context==='episodes')match=path.match(/(?:^|\/)(\d+)\/(\d+)\/?$/);
 let season=match?Number(match[1]):Number(info.season??info.season_number??info.seasonnumber);
 let episode=match&&match[2]?Number(match[2]):Number(info.episode??info.episode_number??info.episodenumber);
 if(context==='episodes'&&(!Number.isFinite(episode)||episode<1)){
  const source=[item.filename,item.file,item.label,item.title,item.name,item.url].filter(Boolean).join(' ');
  const code=source.match(/S(\d{1,2})[ ._-]*E(\d{1,3})/i)||source.match(/(\d{1,2})x(\d{1,3})/i);
  if(code){season=Number(code[1]);episode=Number(code[2])}
 }
 if(context==='seasons'&&Number.isFinite(season)&&season>0)return 'Sezóna '+season;
 if(context==='episodes'){
  const parent=clean(info.tvshowtitle||info.showtitle||info.series||item.title||item.label||item.name||'');
  const titles=[info.episode_title,info.episodeTitle,info.episode_name,info.title,item.label,item.name,item.title]
   .map(value=>clean(value)).filter(Boolean);
  const distinctTitle=titles.find(title=>title.toLowerCase()!==parent.toLowerCase())||'';
  const label=Number.isFinite(episode)&&episode>0
   ? (Number.isFinite(season)&&season>0?'S'+String(season).padStart(2,'0')+'E'+String(episode).padStart(2,'0'):'Epizóda '+episode)
   : 'Epizóda '+(index+1);
  return label+(distinctTitle?' — '+distinctTitle:'');
 }
 return name(item);
}
function menuContext(items){
 const paths=items.map(x=>String(x.url||'').split('?')[0]);
 if(paths.length&&paths.every(p=>/^\/FGet\/[^/]+\/\d+\/?$/.test(p)))return 'seasons';
 if(paths.length&&paths.every(p=>/(?:^|\/)\d+\/\d+\/?$/.test(p)))return 'episodes';
 if(paths.length&&items.every(x=>x.type==='video')&&(mode==='series'||items.some(x=>x.info?.episode!=null||x.info?.episode_number!=null)))return 'episodes';
 return 'general';
}
function sizeMB(s){const m=String(s||'').replace(',','.').match(/([\d.]+)\s*(KB|MB|GB|TB)/i);if(!m)return Infinity;return Number(m[1])*({KB:1/1024,MB:1,GB:1024,TB:1048576}[m[2].toUpperCase()])}
function el(tag,cls,txt){const x=document.createElement(tag);if(cls)x.className=cls;if(txt!==undefined)x.textContent=txt;return x}
function btn(text,fn,cls='secondary'){const b=el('button',cls,text);b.type='button';b.onclick=fn;return b}
function renderCrumb(){crumb.replaceChildren();crumb.append(btn('Vyhľadávanie',()=>search(lastQuery)));stack.forEach((s,i)=>{crumb.append(' / ');crumb.append(btn(s.label,()=>openPath(s.path,i)))})}
async function search(q){lastQuery=q;stack=[];renderCrumb();results.replaceChildren();linkbox.style.display='none';showNotice('Vyhľadávam...');try{const id=mode==='movies'?'search-movies':'search-series';const data=await api('/api/search?'+new URLSearchParams({q,id}));renderMenu(data.menu||[]);showNotice((data.menu||[]).length+' výsledkov')}catch(e){showNotice('Chyba: '+e.message)}}
async function openPath(path,depth, label){showNotice('Načítavam...');results.replaceChildren();linkbox.style.display='none';if(depth!==undefined)stack=stack.slice(0,depth+1);else stack.push({path,label:label||'Položky'});renderCrumb();try{const data=await api('/api/browse?'+new URLSearchParams({path}));if(data.strms){renderStreams(data.strms);showNotice(data.strms.length+' streamov')}else{renderMenu(data.menu||[]);showNotice((data.menu||[]).length+' položiek')}}catch(e){showNotice('Chyba: '+e.message)}}
function renderMenu(items){
 results.replaceChildren();
 const context=menuContext(items);
 items.forEach((it,i)=>{
  if(!it.url)return;
  const row=el('div','item'),left=el('div'),label=locationLabel(it,context,i);
  left.append(el('div','name',label));
  const meta=[];
  if(context==='seasons')meta.push('Sezóna');
  else if(context==='episodes')meta.push('Epizóda');
  else meta.push(it.type==='dir'?'Seriál / priečinok':it.type==='video'?'Video':(it.type||''));
  if(context==='general'&&it.info?.year)meta.push(String(it.info.year));
  left.append(el('div','meta',meta.join(' · ')));
  row.append(left);
  row.append(btn(it.type==='video'?'Streamy':'Otvoriť',()=>openPath(it.url,undefined,label)));
  results.append(row);
 });
}
function currentMediaTitle(){const labels=stack.map(item=>clean(item.label)).filter(label=>label&&!/^položky$/i.test(label));return labels.join(' — ')||clean(lastQuery)||'Stream'}
function rememberCurrentMedia(){const folder=stack.at(-1);if(folder)addHistory(mode,currentMediaTitle(),folder.path,stack.map(item=>({path:item.path,label:item.label})))}
function streamDisplayName(stream,index){const raw=clean(stream.filename||stream.title||''),extension=(raw.match(/\.[a-z0-9]{2,6}$/i)||[])[0]||'',details=[stream.quality,stream.lang,stream.size].filter(Boolean).map(clean),suffix=details.length?details.join(' — '):'Stream '+(index+1);return currentMediaTitle()+' — '+suffix+extension}
function renderStreams(streams){results.replaceChildren();[...streams].sort((a,b)=>sizeMB(a.size)-sizeMB(b.size)).forEach((s,i)=>{if(!s.url)return;const row=el('div','item'),left=el('div'),act=el('div','actions');left.append(el('div','name',streamDisplayName(s,i)));left.append(el('div','meta',[s.size,s.quality,s.lang].filter(Boolean).join(' · ')));act.append(btn('Získať link',()=>getLink(s.url),'secondary'));row.append(left,act);results.append(row)})}
async function getLink(path){linkbox.style.display='none';showNotice('Získavam priamy odkaz z kra.sk...');try{const j=await api('/api/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});showNotice('Odkaz získaný.');linkbox.replaceChildren();linkbox.style.display='block';const actions=el('div','actions');const a=el('a',null,'Otvoriť / stiahnuť');a.href=j.url;a.target='_blank';a.rel='noopener noreferrer';a.referrerPolicy='no-referrer';a.onclick=rememberCurrentMedia;actions.append(a);actions.append(btn('Otvoriť vo VLC',async()=>{rememberCurrentMedia();try{await api('/api/open-vlc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:j.url})});showNotice('Stream sa otvára vo VLC.')}catch(e){showNotice('VLC: '+e.message)}}));actions.append(btn('Kopírovať URL',async()=>{try{await navigator.clipboard.writeText(j.url);showNotice('URL skopírovaná')}catch{showNotice('Kopírovanie zlyhalo; použi odkaz')}}));linkbox.append(actions);linkbox.append(el('p','small','Odkaz môže expirovať. Tlačidlo VLC otvorí stream priamo v nainštalovanom VLC.'))}catch(e){if(e.message.includes('LOGIN_REQUIRED'))document.getElementById('loginPanel').style.display='block';showNotice('Chyba: '+e.message)}}
document.getElementById('searchForm').onsubmit=e=>{e.preventDefault();search(document.getElementById('query').value.trim())};setMode();refreshAuth();
</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Do not log paths containing user input, or any secrets
        pass

    def respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def check(self):
        host = self.headers.get('Host', '')
        if host not in ('127.0.0.1:%s' % self.server.server_port, 'localhost:%s' % self.server.server_port):
            self.respond(403, {'error':'Invalid host'})
            return False
        if self.headers.get('X-Local-Token') != CSRF:
            self.respond(403, {'error':'Invalid local token'})
            return False
        return True

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == '/':
            index = ROOT / 'static' / 'index.html'
            css = ROOT / 'static' / 'app.css'
            script = ROOT / 'static' / 'app.js'
            if not all(path.is_file() for path in (index, css, script)):
                self.send_error(500, 'Static UI files are missing')
                return
            source = index.read_text(encoding='utf-8')
            source = source.replace('<link rel="stylesheet" href="/static/app.css">', '<style>' + css.read_text(encoding='utf-8') + '</style>')
            source = source.replace('<script defer src="/static/app.js"></script>', '<meta name="local-token" content="' + html.escape(CSRF, quote=True) + '">')
            source = source.replace('</body>', '<script>' + script.read_text(encoding='utf-8') + '</script></body>')
            page = source.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('X-Local-App', 'SCCLocal')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob: data:; script-src 'unsafe-inline' 'self'; style-src 'unsafe-inline' 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'")
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        if not self.check():return
        qs = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        try:
            if url.path == '/api/status':
                self.respond(200, {'session': bool(KRA_ID)});return
            if url.path == '/api/image':
                body, content_type = get_image(qs.get('url', [''])[0])
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'private, max-age=86400')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(body)
                return
            if url.path == '/api/feed':
                data = get_feed(qs.get('id', [''])[0], catalog, full=qs.get('full', ['0'])[0] == '1')
            elif url.path == '/api/search':
                q = qs.get('q',[''])[0].strip()
                id = qs.get('id',[''])[0]
                if id not in ('search-movies','search-series','search-people') or not (1 <= len(q) <= 100):
                    raise ValueError('Invalid search')
                extra = [('id', id), ('search', q)]
                if id == 'search-people':
                    extra.append(('ms', '1'))
                data = catalog('/Search/'+id, extra)
            elif url.path == '/api/filter':
                params = filter_params(qs)
                feed_id = feed_id_for(params)
                if feed_id and dict(params).get('page', '1') == '1':
                    data = get_feed(feed_id, catalog, full=True)
                else:
                    data = catalog('/Filter', params)
            elif url.path == '/api/facets':
                data = catalog('/Filter/facet', filter_params(qs))
            elif url.path == '/api/catalog':
                path = qs.get('path',['/'])[0] or '/'
                if path != '/' and not path.startswith(('/FGet/','/Play/','/Search/')):
                    raise ValueError('Invalid catalog path')
                data = catalog(path)
            elif url.path == '/api/browse':
                path = qs.get('path',[''])[0]
                if not path.startswith(('/FGet/','/Play/')):
                    raise ValueError('Invalid browse path')
                data = catalog(path)
            else:
                self.respond(404, {'error':'Not found'});return
            self.respond(200,data)
        except Exception as exc:
            self.respond(502, {'error':str(exc)[:200]})

    def do_POST(self):
        if not self.check():return
        endpoint = urllib.parse.urlsplit(self.path).path
        if endpoint not in ('/api/resolve', '/api/login', '/api/open-vlc', '/api/open-vlc-playlist'):
            self.respond(404, {'error':'Not found'});return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length < 1 or length > (1048576 if endpoint == '/api/open-vlc-playlist' else 16384):
                raise ValueError('Invalid request length')
            body=json.loads(self.rfile.read(length))
            if endpoint == '/api/open-vlc-playlist':
                open_vlc_playlist(body.get('tracks'))
                self.respond(200, {'ok': True});return
            if endpoint == '/api/open-vlc':
                url=body.get('url','')
                if not isinstance(url,str):
                    raise ValueError('Invalid stream URL')
                open_in_vlc(url)
                self.respond(200,{'ok':True});return
            if endpoint == '/api/login':
                username = body.get('username','')
                password = body.get('password','')
                if not isinstance(username,str) or not isinstance(password,str) or not (1 <= len(username) <= 200) or not (1 <= len(password) <= 500):
                    raise ValueError('Invalid credentials')
                with LOCK:
                    login(username,password)
                self.respond(200,{'ok':True});return
            path=body.get('path','')
            if not isinstance(path,str) or not path.startswith('/ws2/'):
                raise ValueError('Invalid stream path')
            link=resolve(path)
            self.respond(200,{'url':link})
        except Exception as exc:
            self.respond(502,{'error':str(exc)[:200]})


def open_existing_instance():
    url = 'http://127.0.0.1:%d/' % LOCAL_PORT
    for attempt in range(8):
        connection = http.client.HTTPConnection('127.0.0.1', LOCAL_PORT, timeout=1)
        try:
            connection.request('GET', '/')
            response = connection.getresponse()
            body = response.read(2048).decode('utf-8', 'replace')
            is_app = response.getheader('X-Local-App') == 'SCCLocal' or '<title>SCC · lokálne</title>' in body
            if response.status == 200 and is_app:
                webbrowser.open(url)
                print('SCC už beží; otváram existujúcu inštanciu:', url)
                return True
            return False
        except OSError:
            if attempt == 7:
                return False
            time.sleep(0.25)
        finally:
            connection.close()
    return False


def main():
    load_session()
    if not KRA_ID:
        try:
            if login_from_file():
                print('Automatic kra.sk login successful.')
        except Exception as exc:
            print('Automatic kra.sk login failed:', str(exc)[:200])
    print('SCC - session encrypted with Windows DPAPI.')
    try:
        server=ThreadingHTTPServer(('127.0.0.1',LOCAL_PORT),Handler)
    except OSError:
        if open_existing_instance():
            return
        print('Port %d už používa iná aplikácia; zatvor ju alebo zmeň LOCAL_PORT.' % LOCAL_PORT)
        return
    url='http://127.0.0.1:%d/' % LOCAL_PORT
    print('Open:',url)
    webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
