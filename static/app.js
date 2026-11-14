(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const csrf = document.querySelector('meta[name="local-token"]')?.content || window.KODY_LOCAL_TOKEN || '';
  const state = {mode: 'movies', section: 'home', view: 'home', stack: [], query: '', facets: null, list: 'watchlist', filterParams: null, filterPage: 1};
  const results = $('results'), notice = $('notice'), crumb = $('crumb'), linkbox = $('linkbox');
  const esc = (s) => String(s ?? '');
  function el(tag, cls, value) { const n = document.createElement(tag); if (cls) n.className = cls; if (value != null) n.textContent = value; return n; }
  function api(path, options={}) { return fetch(path, {...options, headers: {'X-Local-Token': csrf, ...(options.headers||{})}}).then(async r => { let j; try { j=await r.json(); } catch { throw Error('Neplatná odpoveď servera'); } if(!r.ok) throw Error(j.error||`HTTP ${r.status}`); return j; }); }
  function say(s) { notice.textContent = s; }
  const sortOptions=new Set(['datum_desc','rating_desc','minfo.play_desc','yyear_desc','yyear_asc']);
  let selectedSort='datum_desc';
  try{const saved=localStorage.getItem('kody-sort');if(sortOptions.has(saved))selectedSort=saved;}catch{}
  function syncSort(){document.querySelectorAll('[data-sort]').forEach(b=>{const active=b.dataset.sort===selectedSort;b.classList.toggle('selected',active);b.setAttribute('aria-pressed',String(active));});}
  function sortParams(){const split=selectedSort.lastIndexOf('_');return {of:selectedSort.slice(0,split),od:selectedSort.slice(split+1)};}
  function completeFilters(){
    const panel=$('filterPanel'),existing=new Set(['yearFilter','typeFilter','genreFilter','ratingFilter','languageFilter','audioFilter','qualityFilter','hdrFilter','dvFilter','sortFilter','applyFilters','saveFilter','savedFilters']);
    const add=(id,label,kind='select',values=[])=>{if($(id))return;const wrap=el('div','filter-field'),lab=el('label','',label),input=document.createElement(kind==='input'?'input':'select');input.id=id;lab.htmlFor=id;if(kind==='input'){input.type='number';input.min='0';input.placeholder=label;}else values.forEach(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;input.append(o);});wrap.append(lab,input);const before=[...panel.children].find(x=>!existing.has(x.querySelector?.('select,input')?.id||x.id));panel.insertBefore(wrap,before||$('applyFilters'));};
    add('countryFilter','Krajina','select',[['','Všetky krajiny']]);add('atmosFilter','Dolby Atmos','select',[['','Všetko'],['1','Len Atmos']]);add('hevcFilter','HEVC','select',[['','Všetko'],['1','Bez HEVC']]);add('stereoFilter','3D','select',[['','Všetko'],['1','Bez 3D']]);add('bitrateFilter','Bitrate od (kb/s)','input');add('sizeFilter','Veľkosť od (MB)','input');add('keywordFilter','Kľúčové slovo','input');$('keywordFilter').type='search';add('ageFilter','Vekové obmedzenie','select',[['','Všetko'],['6','6+'],['12','12+'],['15','15+'],['18','18+']]);
  }
  function itemsFrom(data) { return (data.menu || data.items || data.results || data.catalog || []).map(normalize); }
  function localized(d={}){return d.sk||d.cs||d.en||Object.values(d)[0]||{};}
  function normalize(item){const info={...item.info,...localized(item.i18n_info)},art=localized(item.i18n_art);return {...item,info:{...info,poster:art.poster||info.poster||item.art?.poster||art.thumb}};}
  function title(item) { return String(item.info?.title||localized(item.i18n_info).title||item.label||item.title||item.name||item.filename||'Bez názvu').replace(/\[\/?(?:B|I|COLOR[^\]]*)\]/gi,'').trim(); }
  function pathOf(item) { return item.url || item.path || item.id || ''; }
  function icon(item) { return item.type === 'video' ? '▶' : (state.mode === 'series' ? '▤' : '▣'); }
  const posterObserver=new IntersectionObserver(entries=>{for(const entry of entries){if(!entry.isIntersecting)continue;const img=entry.target;posterObserver.unobserve(img);loadPoster(img);}}, {rootMargin:'150px'});
  function attachPoster(img,url){img.dataset.poster=url;posterObserver.observe(img);}
  async function loadPoster(img){try{const response=await fetch('/api/image?'+new URLSearchParams({url:img.dataset.poster}),{headers:{'X-Local-Token':csrf}});if(!response.ok)throw Error('Image unavailable');const blob=await response.blob();if(!img.isConnected)return;const url=URL.createObjectURL(blob);img.onload=()=>URL.revokeObjectURL(url);img.onerror=()=>{URL.revokeObjectURL(url);img.remove();};img.src=url;}catch{img.remove();}}
  function feedId(filters){const suffix=filters.typ==='3'?'series':'movies',kind=filters.dub?'dub':filters.y?'trending':filters.of==='minfo.play'?'popular':filters.of==='rating'?'rating':'new';return kind+'-'+suffix;}
  function cacheMessage(data){const status=data._cache?.status;return status==='stale'?'Zobrazuje sa posledná uložená verzia.':status==='waiting'?'Cache sa napĺňa postupne. Otvorte kategóriu pre aktuálny výpis.':status==='unavailable'?'Obnova zlyhala; skúste neskôr.':'';}
  function makeCard(item, index, context='general') {
    const info=item.info||{}, card=el('article','catalog-card'), poster=info.poster||info.poster_url||info.thumbnail||info.cover||info.image;
    if(typeof poster==='string'&&(/^(https:\/\/|\/)/i).test(poster)){const img=el('img','card-poster');attachPoster(img,poster);img.alt='';img.loading='lazy';img.onerror=()=>img.remove();card.append(img);}
    const name=el('div','card-name',displayName(item,index,context));
    const meta=[info.year||item.year,info.rating!=null?`★ ${info.rating}`:'',info.quality,info.language||info.lang,info.runtime].filter(Boolean).join(' · ');
    card.append(name); if(meta) card.append(el('div','card-meta',meta));
    const p=pathOf(item); if(info.plot||info.overview)card.append(el('div','card-plot',info.plot||info.overview));
    const actions=el('div','card-actions');
    if(p){
      const activate=()=>{if(item.type==='stream'){getLink(p);return;}state.currentItem=item;const isTitle=context==='general'&&(info.mediatype==='movie'||info.mediatype==='tvshow'||/^\/Play\//.test(p)||/^\/FGet\/[^/?]+\/?(?:\?|$)/.test(p));if(isTitle)showDetail(item);else openPath(p,undefined,title(item));};
      card.classList.add('clickable-card');card.tabIndex=0;card.setAttribute('role','link');card.setAttribute('aria-label',title(item));
      card.onclick=e=>{if(!e.target.closest('button,a,input,select'))activate();};
      card.onkeydown=e=>{if(e.target===card&&(e.key==='Enter'||e.key===' ')){e.preventDefault();activate();}};
    }
    const favorite=el('button','list-action quick-favorite'),queue=el('button','list-action quick-playlist');
    favorite.type=queue.type='button';favorite.dataset.favoriteKey=itemKey(item);queue.dataset.playlistKey=itemKey(item);
    favorite.onclick=e=>{e.stopPropagation();toggleSaved('favorites',item);};
    queue.onclick=e=>{e.stopPropagation();addToPlaylist(item,index,context);};
    updateQuickButton(favorite,'favorite',readLists().favorites.some(x=>itemKey(x)===itemKey(item)));
    updateQuickButton(queue,'playlist',readPlaylist().some(x=>itemKey(x)===itemKey(item)));actions.append(favorite,queue);
    if(actions.childNodes.length)card.append(actions);
    return card;
  }
  function itemKey(item){return String(pathOf(item)||title(item));}
  function readLists(){try{const x=JSON.parse(localStorage.getItem('kody-lists')||'{}');return {watchlist:Array.isArray(x.watchlist)?x.watchlist:[],favorites:Array.isArray(x.favorites)?x.favorites:[],watched:Array.isArray(x.watched)?x.watched:[]};}catch{return {watchlist:[],favorites:[],watched:[]};}}
  function toggleSaved(list,item){const data=readLists(),key=itemKey(item),present=data[list].some(x=>itemKey(x)===key);data[list]=present?data[list].filter(x=>itemKey(x)!==key):[item,...data[list]];try{localStorage.setItem('kody-lists',JSON.stringify(data));}catch{}if(['lists','history'].includes(state.section))renderLists();syncQuickActions();}
  function updateQuickButton(button,kind,active){button.textContent=kind==='favorite'?(active?'♥':'♡'):(active?'V playliste':'＋ Playlist');button.setAttribute('aria-pressed',String(active));button.setAttribute('aria-label',kind==='favorite'?(active?'Odobrať z obľúbených':'Pridať do obľúbených'):(active?'Už je v playliste':'Pridať do playlistu'));button.title=button.getAttribute('aria-label');button.classList.toggle('selected',active);}
  function syncQuickActions(){const favorites=new Set(readLists().favorites.map(itemKey)),playlist=new Set(readPlaylist().map(itemKey));document.querySelectorAll('[data-favorite-key]').forEach(b=>updateQuickButton(b,'favorite',favorites.has(b.dataset.favoriteKey)));document.querySelectorAll('[data-playlist-key]').forEach(b=>updateQuickButton(b,'playlist',playlist.has(b.dataset.playlistKey)));}
  function readPlaylist(){try{const data=JSON.parse(localStorage.getItem('kody-playlist')||'[]');return Array.isArray(data)?data.filter(x=>x&&typeof pathOf(x)==='string').slice(0,100):[];}catch{return [];}}
  function savePlaylist(items){try{localStorage.setItem('kody-playlist',JSON.stringify(items));syncQuickActions();return true;}catch{say('Playlist sa nepodarilo uložiť. Lokálne úložisko je plné alebo nedostupné.');return false;}}
  function addToPlaylist(item,index=0,context='general'){
    const items=readPlaylist();if(items.some(x=>itemKey(x)===itemKey(item))){say('Titul už je v playliste.');return;}if(items.length>=100){say('Playlist môže mať najviac 100 položiek.');return;}
    let label=title(item);if(context==='episodes'||context==='seasons'){const parent=state.stack[0]?.label||'';label=[parent,displayName(item,index,context)].filter(Boolean).join(' — ');}
    const stored={...normalize(item),_playlistContext:context,_playlistMode:state.mode};stored.info={...stored.info,title:label};stored.i18n_info={};
    if(savePlaylist([...items,stored])){say('Pridané na koniec playlistu.');if(state.section==='playlist')renderPlaylist();}
  }
  let playlistBusy=false;
  function renderPlaylist(){
    results.replaceChildren();results.classList.add('playlist-layout');const items=readPlaylist(),toolbar=el('div','playlist-controls');
    const play=el('button','primary-button','Prehrať vo VLC'),exportButton=el('button','secondary-button','Exportovať M3U'),clear=el('button','secondary-button','Vyprázdniť');play.disabled=exportButton.disabled=!items.length||playlistBusy;clear.disabled=!items.length||playlistBusy;
    play.onclick=()=>playPlaylist(false);exportButton.onclick=()=>playPlaylist(true);clear.onclick=()=>{if(confirm('Vyprázdniť playlist?')&&savePlaylist([]))renderPlaylist();};toolbar.append(el('span','card-meta',`${items.length} položiek`),play,exportButton,clear);results.append(toolbar);
    results.append(el('p','card-meta','Poradie zodpovedá prehrávaniu. Prehrávanie a export použijú prvý dostupný stream každého filmu alebo konkrétnej epizódy.'));
    const visible=items.map((item,index)=>({item,index})).filter(x=>!state.query||genreKey(title(x.item)).includes(genreKey(state.query)));
    if(!visible.length)results.append(el('p','empty-state',state.query?'Žiadna položka nezodpovedá hľadaniu.':'Playlist je prázdny. Pridajte filmy alebo epizódy tlačidlom ＋ Playlist.'));
    visible.forEach(({item,index})=>{const row=makeCard(item,index,item._playlistContext||'general'),manage=el('div','playlist-manage');row.prepend(el('span','playlist-position',String(index+1)));[['↑',-1],['↓',1]].forEach(([label,direction])=>{const button=el('button','secondary-button',label);button.setAttribute('aria-label',direction<0?'Posunúť vyššie':'Posunúť nižšie');button.disabled=playlistBusy||index+direction<0||index+direction>=items.length;button.onclick=e=>{e.stopPropagation();const next=[...items];[next[index],next[index+direction]]=[next[index+direction],next[index]];if(savePlaylist(next))renderPlaylist();};manage.append(button);});const remove=el('button','secondary-button','Odstrániť');remove.disabled=playlistBusy;remove.onclick=e=>{e.stopPropagation();if(savePlaylist(items.filter((_,i)=>i!==index)))renderPlaylist();};manage.append(remove);row.append(manage);results.append(row);});
  }
  async function playPlaylist(exportOnly){
    if(playlistBusy)return;const items=readPlaylist();if(!items.length)return;
    if(items.some(item=>item.info?.mediatype==='tvshow'||item._playlistContext==='seasons'||/^\/FGet\/[^/?]+\/?$/.test(pathOf(item)))){say('V playliste je celý seriál alebo sezóna. Nahraďte ho konkrétnymi epizódami.');return;}
    playlistBusy=true;if(state.section==='playlist')renderPlaylist();const tracks=[];
    try{for(let index=0;index<items.length;index++){const item=items[index];say(`Pripravujem ${index+1}/${items.length}: ${title(item)}`);let path=pathOf(item);if(!path.startsWith('/ws2/')){const data=await api('/api/browse?'+new URLSearchParams({path}));if(!Array.isArray(data.strms)||!data.strms.length)throw Error('Nie je dostupný konkrétny stream pre: '+title(item));path=data.strms[0].url;}const resolved=await api('/api/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});tracks.push({title:title(item),url:resolved.url});}
      if(exportOnly){const clean=x=>String(x).replace(/[\r\n]/g,' '),body='#EXTM3U\n'+tracks.map(x=>'#EXTINF:-1,'+clean(x.title)+'\n'+clean(x.url)).join('\n')+'\n',url=URL.createObjectURL(new Blob([body],{type:'audio/x-mpegurl'})),a=el('a');a.href=url;a.download='kody-playlist.m3u8';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);say('Playlist je exportovaný. Odkazy môžu expirovať.');}
      else{await api('/api/open-vlc-playlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tracks})});say('Playlist sa otvára vo VLC v uloženom poradí.');}
    }catch(e){say('Playlist: '+e.message);if(e.message.includes('LOGIN_REQUIRED'))$('loginPanel').hidden=false;}
    finally{playlistBusy=false;if(state.section==='playlist')renderPlaylist();}
  }
  function displayName(item,index,context) {
    const raw=title(item); if(item._playlistContext)return raw; if(context==='seasons') return /season|sezóna/i.test(raw)?raw:`Sezóna ${raw}`;
    if(context==='episodes') { const m=raw.match(/S(\d{1,2})[ ._-]*E(\d{1,3})/i)||raw.match(/(\d{1,2})x(\d{1,3})/i); return m?`S${String(m[1]).padStart(2,'0')}E${String(m[2]).padStart(2,'0')} · ${raw}`:`Epizóda ${index+1} · ${raw}`; }
    return raw;
  }
  function contextFor(items) { const paths=items.map(x=>String(pathOf(x)).split('?')[0]); if(paths.length&&paths.every(p=>/^\/FGet\/[^/]+\/\d+\/?$/.test(p)))return 'seasons'; if(paths.length&&paths.every(p=>/(?:^|\/)\d+\/\d+\/?$/.test(p)))return 'episodes'; return 'general'; }
  function render(items, context) { results.replaceChildren(); if(!items.length){results.append(el('div','empty-state','Nenašli sa žiadne položky. Skúste upraviť vyhľadávanie alebo filtre.'));return;} (context||contextFor(items)); items.forEach((it,i)=>results.append(makeCard(it,i,context||contextFor(items)))); }
  const detailCache=new Map();
  function cacheDetail(item){const key=String(pathOf(item));detailCache.set(key,item);try{sessionStorage.setItem('kody-detail:'+key,JSON.stringify(item));}catch{}return key;}
  function readDetail(key){if(detailCache.has(key))return detailCache.get(key);try{return JSON.parse(sessionStorage.getItem('kody-detail:'+key)||'null');}catch{return null;}}
  function snapshotPage(){return {view:state.view,section:state.section,query:state.query,heading:$('sectionTitle').textContent,filters:!results.classList.contains('feed-layout')&&state.filterParams?Array.from(state.filterParams):null};}
  function showDetail(raw,push=true){
    const item=normalize(raw),key=cacheDetail(item),previous=snapshotPage();
    if(push){if(state.view!=='detail')history.replaceState({kodyPage:previous},'');history.pushState({kodyDetail:key},'', '#detail='+encodeURIComponent(key));}
    const ticket=++navigation;prepareResults();state.view='detail';state.currentItem=item;state.mode=item.info?.mediatype==='tvshow'||item.type!=='video'?'series':'movies';state.stack=[];state.filterParams=null;
    $('filterPanel').hidden=true;linkbox.hidden=true;crumb.replaceChildren();results.replaceChildren();results.classList.add('feed-layout');$('sectionTitle').textContent='Detail titulu';$('sectionEyebrow').textContent=state.mode==='series'?'SERIÁL':'FILM';document.querySelectorAll('.nav-item').forEach(b=>b.classList.remove('active'));say('');
    const panel=el('section','detail-panel detail-page');panel.id='detailPanel';results.before(panel);
    const back=el('button','secondary-button','← Späť');back.onclick=()=>{if(history.state?.kodyDetail&&history.length>1)history.back();else setView(state.mode);};
    const info=item.info||{},copy=el('div','detail-copy'),heading=el('h1','',title(item)),meta=el('p','card-meta',[info.year,info.rating!=null?'★ '+info.rating:'',Array.isArray(info.genre)?info.genre.join(', '):info.genre,Array.isArray(info.country)?info.country.join(', '):info.country].filter(Boolean).join(' · '));
    copy.append(heading,meta,el('p','detail-plot',info.plot||info.overview||'Popis zatiaľ nie je dostupný.'));
    const stream=el('button','primary-button',state.mode==='series'?'Sezóny a epizódy':'Vybrať stream');stream.onclick=()=>{state.currentItem=item;openPath(pathOf(item),undefined,title(item));};copy.append(stream);panel.append(back);
    const body=el('div','detail-body');if(info.poster&&/^(https:\/\/|\/)/i.test(info.poster)){const img=el('img','detail-poster');attachPoster(img,info.poster);img.alt=title(item);body.append(img);}body.append(copy);panel.append(body);
    window.scrollTo({top:0,behavior:'instant'});loadSimilar(item,ticket);
  }
  const genreKey=value=>String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
  async function similarFilters(item){
    if(!state.facets){try{const d=await api('/api/facets');state.facets=Array.isArray(d)?d:d.facets||[];}catch{}}
    const info=item.info||{},filters={typ:info.mediatype==='tvshow'||item.type!=='video'?'3':'1',of:'rating',od:'desc'},names=(Array.isArray(info.genre)?info.genre:[info.genre]).filter(Boolean).map(genreKey);
    const facet=(state.facets||[]).find(x=>x.t==='genre'&&[x.n,x.u,genreNames[x.u]].some(name=>name&&names.includes(genreKey(name))));
    if(facet)filters['mu[]']=String(facet.i);const year=Number(info.year);if(Number.isInteger(year)&&year>=1900)filters.y=`${year-10}:${year+10}`;return filters;
  }
  async function loadSimilar(item,ticket){
    const section=el('section','home-shelf'),heading=el('h2','shelf-title','Podobné tituly'),grid=el('div','catalog-grid');section.append(heading,grid);results.append(section);grid.append(el('p','card-meta','Načítavam podobné tituly…'));
    try{const filters=await similarFilters(item);if(ticket!==navigation)return;
      let items=itemsFrom(await api('/api/filter?'+new URLSearchParams({...filters,limit:'16',page:'1'}))).filter(x=>pathOf(x)!==pathOf(item)&&(!item.id||x.id!==item.id)&&/^\/(Play|FGet)\//.test(pathOf(x)));
      if(!items.length&&filters.y){delete filters.y;items=itemsFrom(await api('/api/filter?'+new URLSearchParams({...filters,limit:'16',page:'1'}))).filter(x=>pathOf(x)!==pathOf(item)&&(!item.id||x.id!==item.id)&&/^\/(Play|FGet)\//.test(pathOf(x)));}
      if(ticket!==navigation)return;grid.replaceChildren();items.slice(0,4).forEach((x,i)=>grid.append(makeCard(x,i)));if(!items.length)grid.append(el('p','card-meta','Podobné tituly sa nenašli.'));
    }catch(e){if(ticket===navigation){grid.replaceChildren(el('p','card-meta','Podobné tituly sa nepodarilo načítať.'));}}
  }
  function restorePage(){const key=history.state?.kodyDetail||(location.hash.startsWith('#detail=')?decodeURIComponent(location.hash.slice(8)):null);if(key){const item=readDetail(key);if(item){showDetail(item,false);return;}}const page=history.state?.kodyPage;if(page?.filters){state.section=page.section||'home';state.query=page.query||'';state.view=page.view;openFeed(page.heading,Object.fromEntries(page.filters));}else{const hash=location.hash.slice(1);setView(page?.section||(['home','movies','series','lists','history','playlist'].includes(page?.view)?page.view:['home','movies','series','lists','history','playlist'].includes(hash)?hash:'home'),false);}}
  window.addEventListener('popstate',restorePage);
  function renderCrumb() { crumb.replaceChildren(); if(!state.stack.length)return; const home=el('button','',state.query?'Výsledky':'Knižnica'); home.onclick=()=>state.query?search(state.query):loadCatalog(''); crumb.append(home); state.stack.forEach((s,i)=>{crumb.append(' / ');const b=el('button','',s.label);b.onclick=()=>openPath(s.path,i);crumb.append(b);}); }
  async function loadCatalog(path) { say('Načítavam knižnicu…'); state.query=''; try { const data=await api('/api/catalog?'+new URLSearchParams({path:path||''})); state.stack=path?[{path,label:'Knižnica'}]:[]; renderCrumb(); render(itemsFrom(data)); say(`${itemsFrom(data).length} položiek`); } catch(e) { say('Knižnica: '+e.message); results.replaceChildren(); } }
  let navigation=0;
  function historyItems(){try{return JSON.parse(localStorage.getItem('kody-history')||'[]').map(x=>normalize(x.item||{title:x.label,url:x.path,type:x.mode==='series'?'folder':'video'}));}catch{return [];}}
  function prepareResults(sortable=false){document.querySelector('.sort-toolbar').hidden=!sortable;results.classList.remove('feed-layout','playlist-layout');$('listsPanel').hidden=true;$('detailPanel')?.remove();}
  function shelf(heading,items,open){const section=el('section','home-shelf'),h=el('h2','shelf-title'),button=el('button','shelf-link',heading+' →'),grid=el('div','catalog-grid');button.onclick=open;h.append(button);items.slice(0,4).forEach((item,i)=>grid.append(makeCard(item,i)));if(!items.length)grid.append(el('p','card-meta','Zatiaľ bez titulov.'));section.append(h,grid);results.append(section);}
  async function openFeed(heading,filters){const searched=state.view==='search'&&Boolean(filters.s);state.view=searched?'search':'listing';const ticket=++navigation;prepareResults(searched);state.query=searched?filters.s:'';state.stack=[];renderCrumb();$('sectionTitle').textContent=heading;const params=new URLSearchParams({...filters,limit:'40',page:'1'});if(searched){const ordering=sortParams();params.set('of',ordering.of);params.set('od',ordering.od);}state.filterParams=params;state.filterPage=1;say('Načítavam…');try{const data=await api('/api/filter?'+params);if(ticket!==navigation)return;render(itemsFrom(data));say(cacheMessage(data)||`${itemsFrom(data).length} titulov`);if(itemsFrom(data).length>=40){const more=el('button','load-more','Načítať ďalšie');more.onclick=loadMore;results.append(more);}}catch(e){say(e.message);}}
  function feedsFor(view){
    const recentYear=String(new Date().getFullYear()-1);
    const newest=typ=>({typ,of:'datum',od:'desc'});
    const popular=typ=>({typ,of:'minfo.play',od:'desc'});
    const trending=typ=>({...popular(typ),y:'>'+recentYear});
    if(view==='home')return [
      ['Novo pridané filmy',newest('1')],['Novo pridané seriály',newest('3')],
      ['Trending filmy',trending('1')],['Trending seriály',trending('3')]
    ];
    const typ=view==='series'?'3':'1';
    const feeds=[['Novo pridané',newest(typ)],['Trending',trending(typ)],['Populárne',popular(typ)]];
    if(view==='movies')feeds.push(['Najlepšie hodnotené',{typ,of:'rating',od:'desc'}]);
    feeds.push(['Dabing',{...newest(typ),dub:'1'}]);return feeds;
  }
  async function loadFeedPreview(filters){const data=await api('/api/feed?'+new URLSearchParams({id:feedId(filters)}));return {items:itemsFrom(data),cacheNote:cacheMessage(data)};}
  async function loadHome(view='home'){
    state.filterParams=null;const ticket=++navigation;prepareResults();results.replaceChildren();results.classList.add('feed-layout');say('Načítavam…');
    shelf('Naposledy pozerané',historyItems().filter(x=>view==='home'||(view==='series'?x.type!=='video':x.type==='video')),()=>setView('history'));
    const loaded=await Promise.all(feedsFor(view).map(async ([heading,filters])=>{
      try{return {heading,filters,...await loadFeedPreview(filters)};}
      catch(e){return {heading,filters,items:[],error:e.message};}
    }));
    if(ticket!==navigation)return;
    loaded.forEach(f=>{shelf(f.heading,f.items,()=>openFeed(f.heading,f.filters));const section=results.lastElementChild;if(f.heading.startsWith('Trending')){const note=el('p','feed-description','Približný trend: popularita podľa prehratí SCC, tituly z aktuálneho a predchádzajúceho roka.');section.querySelector('.shelf-title').after(note);}if(f.cacheNote)section.append(el('p','card-meta',f.cacheNote));if(f.error)section.append(el('p','card-meta','Feed sa nepodarilo načítať.'));});
    say(loaded.some(f=>f.error)?'Niektoré kategórie sa nepodarilo načítať.':'');
  }
  async function search(query){if(!query){setView(state.section);return;}prepareResults();state.view='search';document.querySelector('.sort-toolbar').hidden=false;state.query=query;state.stack=[];$('sectionTitle').textContent=`Výsledky pre „${query}“`;$('sectionEyebrow').textContent='VYHĽADÁVANIE';renderCrumb();linkbox.hidden=true;await applyFilters();}
  async function applyFilters() {
    if(state.section==='playlist'){navigation++;prepareResults();results.replaceChildren();renderPlaylist();return;}if(['lists','history'].includes(state.section)){navigation++;prepareResults();results.replaceChildren();$('listsPanel').hidden=false;state.filterParams=null;renderLists();return;}
    state.view='search';const ticket=++navigation;prepareResults(true);const params=new URLSearchParams();
    const year=$('yearFilter').value, type=$('typeFilter').value, genre=$('genreFilter').value;
    const sort=selectedSort||'datum_desc';
    const scopedType=state.section==='series'?'3':state.section==='movies'?'1':(type||'all');params.set('typ',scopedType);
    if(state.query) params.set('s',state.query);
    if(year) params.set('y',year);
    if(genre) params.append('mu[]',genre);
    const language=$('languageFilter').value, audio=$('audioFilter').value, quality=$('qualityFilter').value;
    const hdr=$('hdrFilter').value, dv=$('dvFilter').value, rating=$('ratingFilter').value;
    const country=$('countryFilter').value, atmos=$('atmosFilter').value, hevc=$('hevcFilter').value, stereo=$('stereoFilter').value, bitrate=$('bitrateFilter').value, size=$('sizeFilter').value;
    const keyword=$('keywordFilter').value, age=$('ageFilter').value;
    if(language) params.set('la',language.toUpperCase());
    if(audio) params.set(audio,'1');
    if(quality) params.set('q',quality);
    if(hdr) params.set('HDR',hdr==='1'?'2':hdr);
    if(dv) params.set('DV',dv==='1'?'2':dv);
    if(rating) params.set('r','>'+rating);
    if(country) params.append('co[]',country);
    if(atmos) params.set('atmos',atmos);
    if(hevc) params.set('hevc',hevc);
    if(stereo) params.set('ste',stereo);
    if(bitrate) params.set('b','>'+bitrate);
    if(size) params.set('f','>'+Math.round(Number(size)*1000000));
    if(keyword) params.set('kwd',keyword);
    if(age) params.set('m',age);
    if(sort) {
      const split=sort.lastIndexOf('_'),field=sort.slice(0,split),direction=sort.slice(split+1);
      params.set('of',field);
      params.set('od',direction==='desc'?'desc':'asc');
    }
    params.set('limit','40');params.set('page','1');state.filterParams=new URLSearchParams(params);state.filterPage=1;
    say('Filtrujem…');
    try { const d=await api('/api/filter?'+params);if(ticket!==navigation)return;render(itemsFrom(d));say(`${itemsFrom(d).length} výsledkov`);if(itemsFrom(d).length>=40){const more=el('button','load-more','Načítať ďalšie');more.onclick=loadMore;results.append(more);} }
    catch(e){say('Filtre: '+e.message);}
  }
  async function loadMore(){if(!state.filterParams)return;state.filterPage++;const params=new URLSearchParams(state.filterParams);params.set('page',String(state.filterPage));try{const d=await api('/api/filter?'+params),items=itemsFrom(d);const more=$('results').querySelector('.load-more');if(more)more.remove();items.forEach((item,i)=>results.append(makeCard(item,i)));say(`${items.length} ďalších výsledkov`);if(items.length>=40){const button=el('button','load-more','Načítať ďalšie');button.onclick=loadMore;results.append(button);}}catch(e){state.filterPage--;say('Ďalšia strana: '+e.message);}}
  async function openPath(path, depth, label) { navigation++;prepareResults();say('Načítavam…');results.replaceChildren();linkbox.hidden=true;if(depth!==undefined)state.stack=state.stack.slice(0,depth+1);else state.stack.push({path,label:label||'Položky'});renderCrumb();try{const data=await api('/api/browse?'+new URLSearchParams({path}));if(data.strms){renderStreams(data.strms);say(`${data.strms.length} streamov`);}else{render(itemsFrom(data));say(`${itemsFrom(data).length} položiek`);}}catch(e){say('Priečinok: '+e.message);}}
  function renderStreams(streams) {results.replaceChildren();streams.forEach((s,i)=>{const row=el('article','catalog-card'),left=el('div'),filename=s.filename||s.title||`Stream ${i+1}`,meta=[s.size,s.quality,s.lang].filter(Boolean).join(' · ');left.append(el('div','card-icon','▶'),el('div','card-name',filename));if(meta)left.append(el('div','card-meta',meta));row.append(left);const b=el('button','card-open','Získať odkaz →');b.onclick=()=>getLink(s.url);row.append(b);const queue=el('button','list-action','＋ Playlist');queue.onclick=()=>{const parent=state.currentItem||{};addToPlaylist({type:'stream',url:s.url,info:{...parent.info,mediatype:'episode',title:[title(parent),filename].join(' — ')}},i,'stream');};row.append(queue);results.append(row);});}
  async function getLink(path) {say('Získavam priamy odkaz…');try{const j=await api('/api/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});linkbox.replaceChildren();linkbox.hidden=false;const actions=el('div','link-actions'),a=el('a','','Otvoriť / stiahnuť');a.href=j.url;a.target='_blank';a.rel='noopener noreferrer';a.referrerPolicy='no-referrer';a.onclick=()=>remember(path);const vlc=el('button','','Otvoriť vo VLC');vlc.onclick=async()=>{remember(path);try{await api('/api/open-vlc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:j.url})});say('Stream sa otvára vo VLC.');}catch(e){say('VLC: '+e.message);}};const copy=el('button','','Kopírovať URL');copy.onclick=async()=>{try{await navigator.clipboard.writeText(j.url);say('URL skopírovaná.');}catch{say('Kopírovanie sa nepodarilo.');}};actions.append(a,vlc,copy);linkbox.append(actions,el('p','card-meta','Odkaz môže expirovať.'));say('Odkaz je pripravený.');}catch(e){if(e.message.includes('LOGIN_REQUIRED'))$('loginPanel').hidden=false;say('Odkaz: '+e.message);}}
  function remember(path){if(!path)return;try{const history=JSON.parse(localStorage.getItem('kody-history')||'[]'),item=state.currentItem,target=state.stack.at(-1)?.path||path,label=item?title(item):state.stack.map(x=>x.label).join(' — ')||'Médium';localStorage.setItem('kody-history',JSON.stringify([{label,path:target,mode:state.mode,item:item||null,playedAt:new Date().toISOString()},...history.filter(x=>x.path!==target)].slice(0,100)));}catch{}}
  function matchesLocal(item){
    item=normalize(item);const info=item.info||{},query=genreKey(state.query),year=$('yearFilter').value,type=$('typeFilter').value,genre=$('genreFilter').selectedOptions[0]?.textContent,rating=Number($('ratingFilter').value||0);
    if(query&&!genreKey(title(item)).includes(query))return false;
    if(year&&String(info.year||item.year)!==year)return false;
    const itemType=info.mediatype==='tvshow'||item.type!=='video'?'3':'1';if(type&&itemType!==type)return false;
    if($('genreFilter').value&&!(Array.isArray(info.genre)?info.genre:[info.genre]).some(x=>genreKey(x)===genreKey(genre)))return false;
    if(rating&&Number(info.rating||0)*10<rating)return false;
    return true;
  }
  function renderLists(){const box=$('savedList'),data=readLists();box.replaceChildren();let list=state.list==='history'?[]:data[state.list]||[];if(state.list==='history'){let history=[];try{history=JSON.parse(localStorage.getItem('kody-history')||'[]');}catch{}history.forEach(x=>list.push(normalize(x.item||{title:x.label,url:x.path,type:x.mode==='series'?'folder':'video'})));}list=list.filter(matchesLocal);if(!list.length){box.append(el('div','empty-state',state.query?'V tomto zozname sa nenašli zodpovedajúce tituly.':'Tento zoznam je zatiaľ prázdny.'));return;}list.forEach((item,i)=>{const card=makeCard(item,i);const remove=el('button','list-action','Odstrániť');remove.onclick=()=>{if(state.list==='history'){const h=JSON.parse(localStorage.getItem('kody-history')||'[]').filter(x=>x.path!==pathOf(item));localStorage.setItem('kody-history',JSON.stringify(h));}else{data[state.list]=data[state.list].filter(x=>itemKey(x)!==itemKey(item));localStorage.setItem('kody-lists',JSON.stringify(data));}renderLists();};card.append(remove);box.append(card);});}
  function saveCurrentFilter(){const name=prompt('Názov filtra');if(!name)return;const value={name,mode:state.mode,query:state.query,year:$('yearFilter').value,type:$('typeFilter').value,genre:$('genreFilter').value,rating:$('ratingFilter').value,language:$('languageFilter').value,audio:$('audioFilter').value,quality:$('qualityFilter').value,hdr:$('hdrFilter').value,dv:$('dvFilter').value,sort:selectedSort,country:$('countryFilter').value,atmos:$('atmosFilter').value,hevc:$('hevcFilter').value,stereo:$('stereoFilter').value,bitrate:$('bitrateFilter').value,size:$('sizeFilter').value,keyword:$('keywordFilter').value,age:$('ageFilter').value};let saved=[];try{saved=JSON.parse(localStorage.getItem('kody-filters')||'[]');}catch{}saved.unshift(value);localStorage.setItem('kody-filters',JSON.stringify(saved.slice(0,12)));renderSavedFilters();}
  function renderSavedFilters(){const box=$('savedFilters');box.replaceChildren();let saved=[];try{saved=JSON.parse(localStorage.getItem('kody-filters')||'[]');}catch{}saved.forEach((f,i)=>{const row=el('div','saved-filter'),load=el('button','',f.name),edit=el('button','list-action','Upraviť'),rename=el('button','list-action','Premenovať'),del=el('button','list-action','×');const fill=()=>{state.mode=f.mode||'movies';state.query=f.query||'';$('query').value=state.query;['year','type','genre','rating','language','audio','quality','hdr','dv','country','atmos','hevc','stereo','bitrate','size','keyword','age'].forEach(k=>{const node=$(`${k}Filter`);if(node)node.value=f[k]||'';});};load.onclick=()=>{fill();applyFilters();};edit.onclick=()=>{$('filterPanel').hidden=false;fill();say('Filter upravený. Ulož ho znova pod názvom, ktorý chceš zachovať.');};rename.onclick=()=>{const value=prompt('Nový názov filtra',f.name);if(value){saved[i]={...f,name:value.trim()};localStorage.setItem('kody-filters',JSON.stringify(saved));renderSavedFilters();}};del.onclick=()=>{saved.splice(i,1);localStorage.setItem('kody-filters',JSON.stringify(saved));renderSavedFilters();};row.append(load,edit,rename,del);box.append(row);});}
  async function refreshAuth(){try{const j=await api('/api/status');$('authstate').textContent=j.session?'Kra.sk pripojené':'Prihlásenie potrebné';if(!j.session)$('loginPanel').hidden=false;}catch(e){$('authstate').textContent='Server nedostupný';say(e.message);}}
  const genreNames={akcni:'Akčný',animovany:'Animovaný',dobrodruzny:'Dobrodružný',dokumentarni:'Dokumentárny',drama:'Dráma',eroticky:'Erotický',fantasy:'Fantasy',historicky:'Historický',horor:'Horor',hudebni:'Hudobný',komedie:'Komédia',krimi:'Krimi',muzikal:'Muzikál',mysteriozni:'Mysteriózny',pohadka:'Rozprávka',rodinny:'Rodinný',romanticky:'Romantický','sci-fi':'Sci-fi',sportovni:'Športový','stand-up':'Stand-up',thriller:'Thriller',valecny:'Vojnový',western:'Western',zivotopisny:'Životopisný'};
  async function loadFacets(){try{const d=await api('/api/facets');state.facets=Array.isArray(d)?d:(d.facets||[]);const genres=state.facets.filter(x=>/genre|žán|zan/i.test(String(x.t||'')));const countries=state.facets.filter(x=>/country|kraj/i.test(String(x.t||'')));genres.forEach(x=>{const value=x.i??x.u??x.n;if(value==null)return;const o=document.createElement('option');o.value=String(value);o.textContent=x.n||genreNames[x.u]||String(x.u||'Neznámy žáner').replaceAll('-',' ');$('genreFilter').append(o);});countries.forEach(x=>{const o=document.createElement('option');o.value=String(x.i??x.u);o.textContent=x.n||String(x.u);$('countryFilter').append(o);});}catch{}const years=$('yearFilter');for(let year=new Date().getFullYear();year>=1900;year--){const o=document.createElement('option');o.value=String(year);o.textContent=String(year);years.append(o);}}
  function setView(view,push=true){state.section=view;$('typeFilter').value=view==='series'?'3':view==='movies'?'1':'';$('query').placeholder=({series:'Hľadať v seriáloch',movies:'Hľadať vo filmoch',lists:'Hľadať v mojich zoznamoch',history:'Hľadať v histórii pozerania',playlist:'Hľadať v playliste'})[view]||'Hľadať filmy a seriály';if(push)history.pushState({kodyPage:{view}},'', '#'+view);navigation++;state.view=view;syncSort();document.querySelector('.sort-toolbar').hidden=true;state.query='';state.stack=[];$('query').value='';renderCrumb();prepareResults();linkbox.hidden=true;document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===view));$('listsPanel').hidden=!['lists','history'].includes(view);$('sectionTitle').textContent=({home:'Domov',movies:'Filmy',series:'Seriály',lists:'Moje zoznamy',history:'História pozerania',playlist:'Playlist'})[view]||'';$('sectionEyebrow').textContent=['lists','history'].includes(view)?'VAŠE TITULY':'KATALÓG SCC';if(view==='playlist'){renderPlaylist();say('');}else if(['home','movies','series'].includes(view)){state.mode=view==='series'?'series':'movies';loadHome(view);}else{results.replaceChildren();state.list=view==='history'?'history':'watchlist';renderLists();say('');}}
  async function changeSort(){
    if(state.view!=='search')return;
    if(results.classList.contains('feed-layout')){await applyFilters();return;}if(!state.filterParams){await applyFilters();return;}
    const ticket=++navigation,params=new URLSearchParams(state.filterParams),sort=selectedSort,split=sort.lastIndexOf('_');params.set('of',sort.slice(0,split));params.set('od',sort.slice(split+1));params.set('page','1');state.filterPage=1;state.filterParams=params;prepareResults(true);say('Zoraďujem…');
    try{const d=await api('/api/filter?'+params);if(ticket!==navigation)return;const items=itemsFrom(d);render(items);say(`${items.length} titulov`);if(items.length>=40){const more=el('button','load-more','Načítať ďalšie');more.onclick=loadMore;results.append(more);}}catch(e){if(ticket===navigation)say(e.message);}
  }
  document.querySelectorAll('[data-sort]').forEach(button=>button.onclick=()=>{selectedSort=button.dataset.sort;try{localStorage.setItem('kody-sort',selectedSort);}catch{}syncSort();changeSort();});syncSort();
  $('searchForm').addEventListener('submit',e=>{e.preventDefault();search($('query').value.trim());});document.querySelectorAll('.nav-item').forEach(b=>b.onclick=()=>setView(b.dataset.view));document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{state.mode=b.dataset.mode;document.querySelectorAll('[data-mode]').forEach(x=>x.classList.toggle('selected',x===b));if(state.query)search(state.query);else applyFilters();});$('advancedToggle').onclick=()=>{$('filterPanel').hidden=!$('filterPanel').hidden;};$('applyFilters').onclick=applyFilters;$('loginBtn').onclick=()=>$('loginPanel').hidden=!$('loginPanel').hidden;$('closeLogin').onclick=()=>$('loginPanel').hidden=true;
  $('loginForm').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('username').value,password:$('password').value})});$('password').value='';$('loginPanel').hidden=true;say('Prihlásenie úspešné.');refreshAuth();}catch(err){say('Prihlásenie: '+err.message);}});
  completeFilters();$('saveFilter').onclick=saveCurrentFilter;document.querySelectorAll('[data-list]').forEach(b=>b.onclick=()=>{state.list=b.dataset.list;document.querySelectorAll('[data-list]').forEach(x=>x.classList.toggle('selected',x===b));renderLists();});renderSavedFilters();loadFacets();refreshAuth();restorePage();
})();
