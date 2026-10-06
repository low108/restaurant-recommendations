'use strict';

/* The browser never stores private profiles, session tokens or room data locally. */
const state = {
  session: null, rooms: [], room: null, meal: null, notifications: [],
  catalog: null, catalogState: 'loading', catalogRequest: 0,
  inference: null, inferenceState: 'loading',
  route: ['home'], loading: true, error: '', dirty: false, updates: false,
  authMode: 'register', pending: false, routeRequest: 0, accountEpoch: 0, polling: false, syncFailed: false,
  drafts: new Map(), announcedGeneration: '',
  preferenceDraft: null, preferenceRequest: 0,
  adaptiveQuestion: null, adaptiveRequest: 0,
  learning: null, reminderSettings: null, roomReminderSettings: null, responseConflict: null, emailStatus: null,
};
const $ = (selector, scope = document) => scope.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const list = value => Array.isArray(value) ? value : [];
const csv = value => String(value || '').split(',').map(s => s.trim()).filter(Boolean);
const checked = value => value ? ' checked' : '';
const selected = (a, b) => a === b ? ' selected' : '';
const user = () => state.session?.user;
const language = () => state.uiLanguage || state.session?.profile?.language || 'en';
const t = value => window.MakanI18n?.text(String(value ?? ''),language()) ?? String(value ?? '');
function localizeUI(root=document) { document.documentElement.lang=language(); window.MakanI18n?.apply(root,language()); }
const languageButton = () => `<button class="text-button language-switch" type="button" data-action="language" aria-label="Change interface language">${language()==='ms'?'EN':'BM'}</button>`;
const profile = () => state.session?.profile || {};
const initials = name => String(name || '?').split(/\s+/).slice(0, 2).map(s => s[0]).join('').toUpperCase();
const title = value => t(String(value || '').replace(/_/g, ' ').replace(/\b\w/g, s => s.toUpperCase()));
const terminalMeal = meal => ['cancelled','expired','closed'].includes(meal.status);
const labels = {join:t('Join'),decline:t('Not this time'),pending:t('Decide later'),works:t('Works for me'),prefer_another:t('Prefer another'),cannot_eat:t('Cannot eat here'),ate_here:t('I ate here'),somewhere_else:t('I ate somewhere else'),plans_changed:t('Plans changed'),did_not_join:t('I did not join'),not_yet:t('Not yet'),another_occasion:t('For another occasion'),not_sure:t('Not sure'),skipped:t('Skip'),did_not_enjoy:t('Did not enjoy'),within_estimate:t('Within estimate'),dietary_information:t('Dietary information')};
const currency = minor => Number.isFinite(Number(minor)) ? new Intl.NumberFormat('en-MY', {style:'currency',currency:'MYR',maximumFractionDigits:Number(minor)%100 ? 2 : 0}).format(Number(minor)/100) : 'Price unknown';
const dateLabel = (value, options = {}) => value && !Number.isNaN(new Date(value).getTime()) ? new Intl.DateTimeFormat('en-MY', {timeZone:'Asia/Kuala_Lumpur',month:'short',day:'numeric',...options}).format(new Date(value)) : 'Time not set';
const localInput = date => {
  const offsetDate = new Date(date.getTime() + 8*60*60000);
  return offsetDate.toISOString().slice(0,16);
};
function safeURL(value) {
  try { const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) ? u.href : null; }
  catch { return null; }
}
const paths = {
  home:'M3 10 12 3l9 7M5 9v12h5v-7h4v7h5V9',
  people:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  bell:'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4',
  user:'M20 21v-2a6 6 0 0 0-6-6h-4a6 6 0 0 0-6 6v2M16 6a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  arrow:'M5 12h14m-5-5 5 5-5 5',
  plus:'M12 5v14M5 12h14',
  close:'m6 6 12 12M6 18 18 6',
  location:'M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  clock:'M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  lock:'M5 10h14v11H5zM8 10V6a4 4 0 0 1 8 0v4M12 14v3',
  leaf:'M20 3C6 2 2 8 4 15s14 8 16-12ZM4 20l11-11',
  bowl:'M3 11h18a9 9 0 0 1-18 0ZM5 21h14M7 3v4M12 2v5M17 3v4',
  check:'m5 12 4 4L19 6',
  link:'M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2',
  logout:'M9 21H4V3h5M10 12h11m-4-4 4 4-4 4',
  sun:'M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0',
  heart:'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8',
  shield:'M12 2 3 6v6c0 6 9 10 9 10s9-4 9-10V6ZM8 12l3 3 5-6',
  download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.bowl}"/></svg>`;
const brand = (extra = '') => `<a class="brand ${extra}" href="#home" aria-label="Makan Together home"><span class="brand-mark" aria-hidden="true"><span>MA</span><span>KAN</span></span><span class="brand-wordmark">MAKAN<small>TOGETHER</small></span></a>`;
const avatar = name => `<span class="avatar" aria-hidden="true">${esc(initials(name))}</span>`;
function tableArt() {
  return `<svg class="hero-art" viewBox="0 0 420 280" role="img" aria-label="Two friends sharing a meal at one table">
    <g fill="none" stroke="#111" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
      <path d="M84 152c-18 5-31 22-34 43l-4 40h105l-5-39c-3-23-16-39-34-44" fill="#e6e6e6"/>
      <path d="M67 142c-8-16-5-49 2-63 7-15 21-22 36-19 23 4 29 26 26 53l7 39-27 4-36-2Z" fill="#111"/>
      <path d="M77 101c11-3 19-13 23-25 7 14 16 22 24 24l-2 27c-3 13-11 22-23 22s-21-9-23-23Z" fill="#fff"/>
      <path d="M91 148v12c5 7 14 7 19 0v-13" fill="#fff"/>
      <path d="M84 110h3m24 0h3m-14 3-2 9h4m-10 9c4 4 9 4 13 0"/>
      <path d="m71 186 17 21 32-31c5-5 10-5 13-1s0 8-3 11l-32 34c-6 7-14 6-19 0l-15-17" fill="#e6e6e6"/>
      <path d="m112 183 8-7c5-5 10-5 13-1s0 8-3 11l-7 7" fill="#fff"/>
      <path d="M72 218v17m61-33 4 32"/>
      <path d="M305 152c-21 5-35 23-39 48l-6 35h108l-4-38c-3-23-17-41-37-46" fill="#e6e6e6"/>
      <path d="M290 106c-7-8-7-21-2-30 6-12 19-18 31-17 17-1 27 9 29 22 8 5 10 17 3 28l-7 3-48 1Z" fill="#111"/>
      <path d="M296 99c9 0 16-8 20-18 4 10 15 17 29 18l-1 27c-3 13-11 23-24 23s-21-10-24-23Z" fill="#fff"/>
      <path d="M310 148v13c5 7 14 7 20-1v-13" fill="#fff"/>
      <path d="M304 110h3m23 0h3m-13 3-2 9h4m-9 9c4 4 9 4 13 0"/>
      <path d="m346 186-17 22-31-31c-5-5-10-5-13-1s0 8 3 11l32 34c6 7 14 6 19 0l16-18" fill="#e6e6e6"/>
      <path d="m306 185-8-8c-5-5-10-5-13-1s0 8 3 11l8 8" fill="#fff"/>
      <path d="M278 214v21m70-17 1 17"/>
      <ellipse cx="210" cy="225" rx="102" ry="24" fill="#fff"/>
      <path d="M122 234v32m176-32v32m-165-18h154"/>
      <path d="M175 205h70c-2 21-14 32-35 32s-33-11-35-32Z" fill="#ed1c24"/>
      <ellipse cx="210" cy="205" rx="35" ry="9" fill="#fff"/>
      <path d="M189 206c4-8 10-8 14 0m1 0c4-8 10-8 14 0m1 0c4-8 10-8 14 0m-39-27c-7-8 7-12 0-20m16 20c-7-8 7-12 0-20m16 20c-7-8 7-12 0-20"/>
      <path d="m127 175 63 20m-62-15 60 19m104-23-62 20m61-14-60 19"/>
      <path d="M184 83h52v35h-15l-11 11-11-11h-15Z" fill="#fff"/>
      <path d="M210 110c-4-4-13-8-13-14 0-7 9-8 13-2 4-6 13-5 13 2 0 6-9 10-13 14Z" fill="#ed1c24" stroke="none"/>
      <path d="m171 71-5-7m44 0v-9m39 16 5-7M47 119l-8-2m333 2 8-2"/>
    </g>
  </svg>`;
}

function requestContext() {
  return {account:state.accountEpoch, hash:location.hash, route:state.routeRequest};
}
function isCurrent(context) {
  return context.account === state.accountEpoch && context.hash === location.hash && context.route === state.routeRequest;
}
function staleRequest() { const error=new Error('This page changed.'); error.code='stale_context'; return error; }
const sessionChannel=typeof BroadcastChannel==='function' ? new BroadcastChannel('makan-session') : null;
function setSession(session, broadcast=false) {
  state.accountEpoch += 1;
  state.session=session;
  state.drafts.clear();
  state.rooms=[];state.room=null;state.meal=null;state.notifications=[];state.learning=null;
  state.responseConflict=null;state.preferenceDraft=null;state.preferenceRequest+=1;state.adaptiveQuestion=null;state.adaptiveRequest+=1;state.reminderSettings=null;state.roomReminderSettings=null;state.emailStatus=null;
  state.dirty=false;state.updates=false;state.syncFailed=false;state.announcedGeneration='';state.pendingInvite=null;
  if(broadcast)sessionChannel?.postMessage('session_changed');
}
if(sessionChannel)sessionChannel.onmessage=async event=>{
  if(event.data!=='session_changed')return;
  // Another tab may have replaced the cookie. Clear private data before refreshing identity.
  setSession({user:null,profile:null,csrf_token:null,demo_mode:state.session?.demo_mode});
  closeModal();render();
  try {setSession(await api('/session'));await loadRoute();announce('Your account session changed in another tab.');}
  catch(error){if(error.code!=='stale_context')toast('Sign in again to continue.');}
};
function announce(message) {
  const target=$('#page-announcement');
  if(target) target.textContent=t(message);
}
function captureDraft() {
  if(!user() || !state.dirty) return;
  const form=$('#main form[data-form]');
  if(!form || !['checkin','profile','feedback'].includes(form.dataset.form)) return;
  const key=state.route.join('/');
  state.drafts.delete(key);
  state.drafts.set(key,{type:form.dataset.form,responseRevision:form.dataset.responseRevision,profileRevision:form.dataset.profileRevision,
    fields:[...form.querySelectorAll('input[name],select[name],textarea[name]')].filter(el=>el.type!=='password').map(el=>({name:el.name,type:el.type,value:el.value,checked:el.checked}))});
  // Memory only, bounded to this signed-in browser session. Never persist sensitive drafts.
  if(state.drafts.size>12) state.drafts.delete(state.drafts.keys().next().value);
}
function clearDraft() { state.drafts.delete(state.route.join('/')); }
function restoreDraft() {
  const draft=state.drafts.get(state.route.join('/'));
  if(!draft) return;
  const form=$(`#main form[data-form="${draft.type}"]`); if(!form)return;
  const fields=[...form.querySelectorAll('input[name],select[name],textarea[name]')];
  for(const saved of draft.fields) {
    const input=fields.find(el=>el.name===saved.name && (!['checkbox','radio'].includes(saved.type) || el.value===saved.value));
    if(input) { if(['checkbox','radio'].includes(saved.type))input.checked=saved.checked;else input.value=saved.value; }
  }
  if(draft.responseRevision!==undefined)form.dataset.responseRevision=draft.responseRevision;
  if(draft.profileRevision!==undefined)form.dataset.profileRevision=draft.profileRevision;
  if(draft.type==='checkin') {
    $('[data-joining-fields]',form).disabled=$('[name="attendance"]:checked',form)?.value!=='join';
    $('[name="budget"]',form).required=$('[name="ready"]',form).checked;
  }
  if(draft.type==='feedback')$('[data-visit-fields]',form).disabled=$('[name="outcome"]:checked',form)?.value!=='ate_here';
  state.dirty=true;
  const note=document.createElement('p');note.className='notice draft-note';note.textContent=t('Your unsaved draft is kept in this tab. Review it before submitting.');form.prepend(note);
}
async function api(path, options = {}) {
  const context=requestContext();
  const method = options.method || 'GET';
  const headers = {...(options.headers || {})};
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (!['GET','HEAD'].includes(method) && state.session?.csrf_token) headers['X-CSRF-Token'] = state.session.csrf_token;
  let response;
  try { response=await fetch(`/api${path}`, {credentials:'same-origin',...options,method,headers,body:options.body === undefined ? undefined : JSON.stringify(options.body)}); }
  catch(error) {
    if(!isCurrent(context))throw staleRequest();
    if(error.name==='AbortError')throw error;
    const failure=new Error('We could not reach the server. Your draft is still here. Check your connection and try again.');failure.code='network';throw failure;
  }
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  // A response from another page or signed-in account must never update this page.
  if(!isCurrent(context))throw staleRequest();
  if (!response.ok) {
    if (response.status === 401 && user()) {
      const demo=state.session?.demo_mode;
      setSession({user:null,profile:null,csrf_token:null,demo_mode:demo});
      state.authMode='login';state.error=t('Your session ended. Sign in again. Private drafts were cleared from this tab.');
      closeModal();render();$('#main')?.focus({preventScroll:true});announce(state.error);
    }
    const detail = typeof data?.detail === 'string' ? data.detail : typeof data?.detail?.message === 'string' ? data.detail.message : Array.isArray(data?.detail) ? data.detail.map(e => e.msg).join('. ') : 'Something went wrong. Please try again.';
    const error = new Error(detail); error.status = response.status; error.code=data?.detail?.code; throw error;
  }
  return data;
}
function toast(message) {
  const element = $('#toast'); element.textContent = t(message); element.classList.add('show');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => element.classList.remove('show'), 4500);
}
function demoBanner() {
  if (state.session?.demo_mode || state.catalog?.synthetic === true) return `<div class="demo-banner" id="catalog-banner">${icon('bowl')} <strong>Demo catalog.</strong> Restaurant names and menus are fictional. Use this space to try the flow; these are not places to visit.</div>`;
  const available = state.catalogState === 'available' && state.catalog;
  const hasLocations = available && state.catalog.status !== 'empty' && Number(state.catalog.outlet_count) > 0;
  const label = hasLocations ? 'Real restaurant data.' : available && state.catalog.status === 'empty' ? 'Awaiting restaurant data.' : 'Restaurant data.';
  const description = hasLocations ? 'Collected information still needs the checks for each meal.' : available && state.catalog.status === 'empty' ? 'Restaurant and menu information has not been added yet.' : state.catalogState === 'loading' ? 'Checking available restaurant information.' : 'Coverage information is currently unavailable.';
  return `<div class="demo-banner ${hasLocations ? 'real-data-banner' : ''}" id="catalog-banner">${icon('location')} <strong>${label}</strong> ${description}</div>`;
}
const empty = (heading, message, action = '', name = 'bowl') => `<div class="empty">${icon(name)}<h3>${esc(t(heading))}</h3><p>${esc(t(message))}</p>${action}</div>`;
const privacy = text => `<div class="privacy-caption">${icon('lock')}<span>${esc(t(text))}</span></div>`;
const formError = () => '<div class="form-error" role="alert" tabindex="-1"></div>';

const countValue = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.floor(value) : null;
const countLabel = value => countValue(value) === null ? '—' : countValue(value).toLocaleString('en-MY');
function readableText(value, fallback = '') {
  if (typeof value === 'string') return value;
  if (!value || typeof value !== 'object') return fallback;
  return [value.message, value.reason, value.text, value.label].find(item => typeof item === 'string') || fallback;
}
function coverageContent(catalog, availability = 'available', compact = false) {
  if (!catalog || availability !== 'available') {
    const loading = availability === 'loading';
    return `<div class="coverage-heading"><h2>${compact ? 'About this search' : t('Restaurant coverage')}</h2><span class="tag neutral">${loading ? 'Checking coverage' : 'Currently unavailable'}</span></div><p class="coverage-description">${loading ? 'Checking which restaurant and menu records are available.' : 'We couldn’t load restaurant coverage. This does not mean there are no restaurants. Other parts of your table are still available.'}</p>`;
  }
  const synthetic = catalog.synthetic === true;
  const statuses = {
    empty: ['No locations yet', 'No restaurant locations are available in this collection yet.'],
    demo: ['For trying the flow', 'These fictional menus help you try the app. They are not real places to visit.'],
    review_required: ['Details still under review', 'Some collected information is incomplete or awaiting checks. A location only appears as a suggestion when its evidence supports your group’s requirements.'],
    reviewed: ['Records reviewed', 'The collected records have been reviewed. Each meal still needs its own checks for current information and everyone’s requirements.'],
  };
  const [statusLabel, description] = statuses[catalog.status] || ['Review status unavailable', 'The collection’s review status is not available. We won’t assume its details are ready to use.'];
  const issues = list(catalog.issues).filter(issue => issue?.code !== 'sources_not_current_for_embed').map(issue => ({message:readableText(issue, 'Some supporting information still needs checking.'),count:countValue(issue?.count)}));
  const outlets = countValue(catalog.outlet_count);
  if (!issues.length && outlets !== null && catalog.status === 'review_required') {
    for (const [field, message] of [['outlets_with_menus','Locations without linked menu records.'],['outlets_with_coordinates','Locations without a confirmed map position.'],['outlets_with_hours','Locations without recorded opening hours.']]) {
      const complete = countValue(catalog[field]);
      if (complete !== null && complete < outlets) issues.push({message,count:outlets-complete});
    }
    const heldItems = countValue(catalog.quarantined_item_count);
    if (heldItems) issues.push({message:'Menu records held out of recommendations until their details are reviewed.',count:heldItems});
  }
  return `<div class="coverage-heading"><div><p class="eyebrow">${compact ? 'THE INFORMATION BEHIND THIS RESULT' : 'A CLEARER PICTURE'}</p><h2>${compact ? t('Coverage for this search') : t('Restaurant coverage')}</h2></div><span class="tag ${catalog.status === 'review_required' ? 'outline' : 'neutral'}">${esc(statusLabel)}</span></div><div class="coverage-counts"><div><strong>${countLabel(catalog.outlet_count)}</strong><span>${synthetic ? 'Demo locations' : 'Restaurant locations'}</span></div><div><strong>${countLabel(catalog.outlets_with_menus)}</strong><span data-i18n="With%20menu%20records">With menu records</span></div><div><strong>${countLabel(catalog.item_count)}</strong><span data-i18n="Menu%20items">Menu items</span></div></div><p class="coverage-description">${esc(description)}</p><p class="coverage-caption" data-i18n="Menu%20records%20include%20dishes%2C%20drinks%20and%20size%20options.%20They%20are%20not%20separate%20restaurant%20locations.">Menu records include dishes, drinks and size options. They are not separate restaurant locations.</p>${issues.length ? `<details class="coverage-gaps"><summary data-i18n="What%20still%20needs%20checking">What still needs checking</summary><ul>${issues.map(issue => `<li><span>${esc(issue.message)}</span>${issue.count !== null ? `<span class="coverage-issue-count">${countLabel(issue.count)} ${issue.count === 1 ? 'record' : 'records'}</span>` : ''}</li>`).join('')}</ul></details>` : ''}`;
}
function catalogSection() {
  return `<section class="catalog-coverage" id="restaurant-coverage" aria-label="Restaurant coverage" aria-live="polite">${coverageContent(state.catalog,state.catalogState)}</section>`;
}
function resultCoverage(result) {
  if (!result.coverage || typeof result.coverage !== 'object') return '';
  return `<section class="catalog-coverage meal-catalog-coverage" aria-label="Coverage for this search">${coverageContent(result.coverage,'available',true)}</section>`;
}
function verificationHTML(items) {
  const records = list(items);
  if (!records.length) return '';
  return `<section class="notice warning verification-notice"><h3 data-i18n="Information%20to%20check">Information to check</h3><ul class="verification-list">${records.map(record => {
    const name = typeof record?.name === 'string' ? record.name : typeof record?.outlet_name === 'string' ? record.outlet_name : '';
    const reasons = list(record?.reasons).map(reason => readableText(reason)).filter(Boolean);
    const fallback = readableText(record, 'Supporting restaurant information still needs checking.');
    return `<li>${name ? `<strong>${esc(name)}</strong>` : ''}<ul>${(reasons.length ? reasons : [fallback]).map(reason => `<li>${esc(reason)}</li>`).join('')}</ul></li>`;
  }).join('')}</ul></section>`;
}
function generationMessage(result) {
  if (result?.status === 'shortlisted' && list(result.options).length) return 'Your group’s shortlist is ready to review.';
  if (result?.status === 'needs_verification') return 'Some information needs checking before we can suggest a meal.';
  if (result?.status === 'no_options') return 'No supported matches were found in the available restaurant collection.';
  if (result?.status === 'needs_input') return 'A little more information is needed before finding your meal.';
  return 'The meal check has finished. Review the current result below.';
}
function generationState(job) {
  const states={queued:['Meal check queued','Your request is saved. You can leave this page and return while the check continues.'],running:['Checking the group’s options','We are comparing the available restaurant information with the group’s current answers.'],retry_wait:['The check will retry','A temporary problem interrupted the check. Another attempt is scheduled automatically.'],published:['Meal check complete','The latest result is ready below.'],failed:['The check could not finish','The retry limit was reached. Review the plan before starting a new check.'],superseded:['The earlier check is out of date','The group or meal changed. Earlier work will not replace the current plan.']};
  return states[job?.status] || ['Meal check in progress','Your request is saved. Return here to see the latest status.'];
}
function generationHTML(meal) {
  const job=meal.generation_job;
  if(!job && !list(meal.generation_history).length) return '<div id="generation-status"></div>';
  const [heading,message]=generationState(job);
  const active=['queued','running','retry_wait'].includes(job?.status);
  const delayed=active && meal.generation_worker_status?.status==='waiting_for_provider_shutdown';
  const attempts=job ? `${Number(job.attempt_count)||0} / ${Number(job.max_attempts)||0}` : '';
  const history=list(meal.generation_history);
  return `<section id="generation-status" class="generation-panel${active?' is-active':''}" aria-labelledby="generation-heading">${job ? `<div class="spread"><h2 id="generation-heading">${esc(t(heading))}</h2><span class="tag neutral">${esc(t('Attempts'))} ${esc(attempts)}</span></div><p>${esc(t(message))}</p>${job.status==='retry_wait' && !delayed && job.next_attempt_at ? `<p class="help">${esc(t('Next attempt'))}: ${esc(dateLabel(job.next_attempt_at,{hour:'numeric',minute:'2-digit',second:'2-digit'}))}</p>` : ''}${job.status==='retry_wait' && !delayed && job.retryable && meal.organizer_id===user().id ? '<button class="btn secondary small" data-action="retry-generation">'+esc(t('Retry now'))+'</button>' : ''}` : '<h2 id="generation-heading">'+esc(t('Meal check history'))+'</h2>'}${delayed ? '<p class="notice warning generation-delay" role="status">'+esc(t('Processing is delayed. Your answers are saved. The service needs attention; you can return later.'))+'</p>' : ''}${state.syncFailed ? '<p class="notice warning" role="status">'+esc(t('Connection interrupted. Your saved check can continue on the server. We will reconnect automatically.'))+'</p>' : ''}${history.length ? `<details class="generation-history"><summary>${esc(t('Meal check history'))}</summary><p class="help">${esc(t('Recent checks and attempts only. Personal answers and private requirements are not shown here.'))}</p><ol>${history.map(record=>`<li><strong>${esc(t(generationState(record)[0]))}</strong><span>${esc(dateLabel(record.created_at,{hour:'numeric',minute:'2-digit'}))}</span><span>${esc(t('Attempts'))}: ${Number(record.attempt_count)||0} / ${Number(record.max_attempts)||0}</span>${list(record.attempts).length ? `<ul>${record.attempts.map(attempt=>`<li>${esc(t('Attempt'))} ${Number(attempt.number)||0} · ${esc(t(({running:'In progress',published:'Completed',succeeded:'Completed',failed:'Interrupted',superseded:'Out of date'})[attempt.status]||'Finished'))}${Number.isFinite(attempt.duration_ms)?` · ${Math.max(0,Math.round(attempt.duration_ms/1000))} s`:''}</li>`).join('')}</ul>` : ''}</li>`).join('')}</ol></details>` : ''}</section>`;
}
function updateGeneration(meal) {
  const target=$('#generation-status');
  const signature=JSON.stringify([meal.generation_job,meal.generation_history,meal.generation_worker_status,state.syncFailed,language()]);
  if(target && target.generationSignature!==signature) {
    const active=document.activeElement;const focused=target.contains(active);const focusAction=active?.dataset?.action;const focusSummary=active?.tagName==='SUMMARY';
    const expanded=$('.generation-history',target)?.open;
    target.outerHTML=generationHTML(meal);
    const current=$('#generation-status');current.generationSignature=signature;
    const history=$('.generation-history',current);if(history && expanded)history.open=true;
    if(focused){const replacement=focusSummary?$('summary',current):[...current.querySelectorAll('[data-action]')].find(el=>el.dataset.action===focusAction);if(replacement)replacement.focus({preventScroll:true});else {current.tabIndex=-1;current.focus({preventScroll:true});}}
  }
  const job=meal.generation_job;
  const delayed=['queued','running','retry_wait'].includes(job?.status) && meal.generation_worker_status?.status==='waiting_for_provider_shutdown';
  const key=job?`${job.id}:${job.status}:${job.attempt_count}:${delayed}`:'';
  if(key && key!==state.announcedGeneration) { state.announcedGeneration=key;announce(delayed?'Processing is delayed. Your answers are saved. The service needs attention; you can return later.':generationState(job)[0]); }
}
async function refreshCatalog() {
  const request = ++state.catalogRequest;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 6000);
  try {
    const catalog = await api('/catalog/status', {signal:controller.signal});
    if (!catalog || typeof catalog !== 'object' || Array.isArray(catalog)) throw new Error('Coverage unavailable');
    if (request !== state.catalogRequest) return;
    state.catalog = catalog; state.catalogState = 'available';
  } catch {
    if (request !== state.catalogRequest) return;
    state.catalog = null; state.catalogState = 'unavailable';
  } finally {
    clearTimeout(timeout);
    if (request === state.catalogRequest) {
      const banner = $('#catalog-banner'); if (banner) banner.outerHTML = demoBanner();
      const region = $('#restaurant-coverage');
      if (region) {
        const wasExpanded = $('.coverage-gaps',region)?.open;
        region.innerHTML = coverageContent(state.catalog,state.catalogState);
        const gaps = $('.coverage-gaps',region); if (gaps && wasExpanded) gaps.open = true;
      }
    }
  }
}

function inferenceConfiguration() {
  const config=state.inference;
  if(state.inferenceState==='loading')return 'Checking explanation settings…';
  if(!config)return 'Explanation settings are unavailable';
  if(config.configuration_status==='ready')return config.provider==='ilmu' ? 'ILMU explanations enabled' : 'Model explanations enabled';
  if(config.configuration_status==='not_configured')return 'Rule-based recommendations; model setup incomplete';
  if(config.configuration_status==='disabled')return 'Rule-based recommendations';
  return 'Explanation settings are unavailable';
}
function inferenceResult(result) {
  const agent=result?.agent;
  if(!agent || typeof agent.model_calls!=='number')return ['unknown','Model use was not recorded for this result'];
  const status=agent.inference_status || agent.model_status;
  if(agent.model_calls===0)return ['not_called','No LLM used'];
  if(status==='validated')return ['validated',agent.provider==='ilmu' ? 'Explanation labels selected by ILMU' : 'Explanation labels selected by a model'];
  if(status==='invalid_output')return ['fallback','Model output could not be used; template explanations used'];
  if(['provider_error','template_fallback'].includes(status))return ['fallback','Model unavailable; template explanations used'];
  return ['unknown','Model use was not recorded for this result'];
}
function inferenceHTML(meal=null) {
  const result=meal?.result;
  const [status,heading]=result ? inferenceResult(result) : [state.inference?.configuration_status || state.inferenceState,inferenceConfiguration()];
  const noOptions=result && result.agent?.model_calls===0 && !list(result.options || result.candidates).length;
  return `<section id="inference-status" class="inference-panel" data-inference-status="${esc(status)}" aria-labelledby="inference-heading"><h2 id="inference-heading">${esc(t(heading))}</h2>${result ? `<p class="help inference-configuration">${esc(t('Current settings'))}: ${esc(t(inferenceConfiguration()))}</p>` : ''}${noOptions ? `<p class="help">${esc(t('There were no eligible options to explain, so no model call was needed.'))}</p>` : ''}<details><summary>${esc(t('How suggestions are made'))}</summary><p>${esc(t('Food requirement checks and ranking use fixed rules in the app. When enabled, a model only chooses from approved explanation labels.'))}</p><p>${esc(t('You may also ask AI to draft editable taste choices from only the craving text you approve. Nothing from that draft affects ranking until you apply, review and submit the ordinary check-in form.'))}</p>${!result && state.inference?.configuration_status==='ready' ? `<p>${esc(t('Enabled describes the configuration. Each meal result shows whether a model was actually used.'))}</p>` : ''}</details></section>`;
}
async function refreshInference() {
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),6000);
  try {
    // This endpoint is public deployment configuration, never account data.
    const response=await fetch('/api/inference/status',{credentials:'omit',cache:'no-store',signal:controller.signal});
    if(!response.ok)throw new Error('Unavailable');
    const config=await response.json();
    if(!config || typeof config!=='object' || Array.isArray(config))throw new Error('Unavailable');
    state.inference=config;state.inferenceState='available';
  } catch {state.inference=null;state.inferenceState='unavailable';}
  finally {
    clearTimeout(timeout);
    const panel=$('#inference-status');
    if(panel) {
      const expanded=$('details',panel)?.open;
      const focused=panel.contains(document.activeElement);
      panel.outerHTML=inferenceHTML(state.route[0]==='meal'?state.meal:null);
      const details=$('#inference-status details');if(details && expanded)details.open=true;
      if(focused)$('#inference-status summary')?.focus({preventScroll:true});
    }
  }
}

function tokenPage() {
  const reset=state.route[0]==='reset-password';
  return `<main id="main" tabindex="-1" class="auth-form-side token-page"><div>${brand()}<div class="section-heading"><h1>${reset?'Choose a new password':'Verify your email'}</h1><p>${reset?'Use a strong password you do not use elsewhere.':'Confirm this email-verification link. It will not sign you in automatically.'}</p></div><form data-form="${reset?'reset-password':'verify-email'}">${reset?'<div class="field"><label for="reset-password">New password</label><input id="reset-password" name="password" type="password" autocomplete="new-password" minlength="10" maxlength="128" required></div>':''}${formError()}<button class="btn full" type="submit">${reset?'Reset my password':'Confirm email verification'}</button></form><a class="text-button" href="#home">Back to sign in</a></div></main>`;
}
function emailAccountHTML() {
  const email=state.emailStatus;
  if(!email)return '<p class="help muted">Email verification status is currently unavailable.</p>';
  return `<div class="email-account"><span class="tag neutral">${email.verified?'Email verified':'Email not verified'}</span>${!email.verified&&email.available?'<button class="text-button" type="button" data-action="request-verification">Send verification email</button>':''}${!email.available?'<p class="help muted">Account email delivery is unavailable in this pilot. No verification or password-reset email can be sent yet.</p>':''}</div>`;
}
function renderAuth() {
  const signup = state.authMode === 'register';
  return `<div class="auth-layout"><section class="auth-story">${brand()}<div><div class="eyebrow">FOR YOUR FAVOURITE PEOPLE</div><h1>Good food.<br>Better together.</h1><p data-i18n="Find%20your%20next%20shared%20meal%2C%20with%20everyone%E2%80%99s%20tastes%20in%20mind.">Find your next shared meal, with everyone’s tastes in mind.</p>${tableArt()}</div><div class="auth-story-footer">Made for good company in Kuala Lumpur & Selangor.</div></section><main id="main" tabindex="-1" class="auth-form-side"><div>${brand('mobile-brand')}${languageButton()}<div class="eyebrow">A SEAT AT THE TABLE</div><h2>${signup ? t('Create an account') : t('Welcome back')}</h2><p class="intro">${signup ? 'Create your account, gather your people, and find your next shared meal.' : 'Your people. Your preferences. Your next meal together.'}</p>${state.error ? `<div class="notice error">${esc(state.error)}</div>` : ''}<form data-form="auth">${signup ? `<div class="field"><label for="auth-name" data-i18n="What%20should%20we%20call%20you%3F">What should we call you?</label><input id="auth-name" name="name" autocomplete="name" placeholder="Your name" required maxlength="60"></div>` : ''}<div class="field"><label for="auth-email" data-i18n="Email%20address">Email address</label><input id="auth-email" name="email" type="email" autocomplete="email" placeholder="you@example.com" required maxlength="254"></div><div class="field"><label for="auth-password" data-i18n="Password">Password</label><input id="auth-password" name="password" type="password" autocomplete="${signup ? 'new-password' : 'current-password'}" minlength="10" maxlength="128" placeholder="${signup ? t('At least 10 characters') : t('Your password')}" required></div>${signup ? '<label class="check-label"><input type="checkbox" name="adult_confirmed" required><span data-i18n="I%20am%2018%20or%20older.">I am 18 or older.</span></label><label class="check-label"><input type="checkbox" name="terms_accepted" required><span data-i18n="I%20accept%20the%20pilot%20terms%20and%20privacy%20notice.">I accept the pilot terms and privacy notice.</span></label><button class="text-button" type="button" data-action="pilot-terms" data-i18n="Read%20the%20pilot%20terms%20%26%20privacy%20notice">Read the pilot terms & privacy notice</button>' : ''}${formError()}<button class="btn full" type="submit">${signup ? t('Create my account') : t('Sign in')} ${icon('arrow')}</button></form><button class="text-button" type="button" data-action="forgot-password">Forgot your password?</button><div class="auth-switch">${signup ? 'Already have an account?' : 'New to the table?'} <button data-action="auth-switch">${signup ? t('Sign in') : t('Create an account')}</button></div><div class="auth-privacy">${icon('shield')}<span>Your personal food requirements stay private. Your room sees who is ready and the shared recommendations.</span></div>${demoBanner()}</div></main></div>`;
}

function shell(content) {
  const active = ['room','meal'].includes(state.route[0]) ? 'rooms' : state.route[0];
  const unread = state.notifications.some(n => !n.read);
  const nav = [['home','home','Home'],['rooms','people','Tables'],['inbox','bell','Inbox'],['profile','user','Profile']].map(([route,name,label]) => `<a href="#${route}" class="${active === route ? 'active' : ''}" ${active === route ? 'aria-current="page"' : ''}>${icon(name)}<span data-i18n="${encodeURIComponent(label)}">${esc(t(label))}</span>${route === 'inbox' && unread ? '<span class="notification-dot" aria-label="Unread notifications"></span>' : ''}</a>`).join('');
  const labels = {home:t('Home'),rooms:t('Tables'),room:'Your table',meal:'Your meal',inbox:t('Inbox'),profile:t('Profile')};
  return `<div class="app-shell"><aside class="sidebar">${brand()}<nav class="nav" aria-label="Main navigation">${nav}</nav><div class="sidebar-bottom"><div class="sidebar-note">${icon('leaf')}<p>Good food tastes<br>better together.</p></div><a class="user-button" href="#profile">${avatar(user().name)}<span class="break-word">${esc(user().name)}<small data-i18n="Your%20private%20profile">Your private profile</small></span></a></div></aside><div class="main-column"><header class="topbar">${brand('mobile-brand')}<div class="topbar-label">${esc(labels[state.route[0]] || 'Home')}</div><div class="topbar-actions">${languageButton()}<span class="topbar-location">${icon('location')} KL & Selangor</span><a class="icon-button" href="#inbox" aria-label="Open inbox${unread ? ', unread notifications' : ''}">${icon('bell')}</a><button class="icon-button" data-action="logout" aria-label="Sign out" title="Sign out">${icon('logout')}</button></div></header><main id="main" class="content" tabindex="-1">${demoBanner()}${state.error ? `<div class="notice error" role="alert">${esc(state.error)} <button class="text-button" data-action="reload" data-i18n="Try%20again">Try again</button></div>` : ''}${content}<footer class="page-footer"><span data-i18n="Good%20food.%20Better%20company.">Good food. Better company.</span><span data-i18n="Made%20for%20your%20people%2C%20in%20KL%20%26%20Selangor.">Made for your people, in KL & Selangor.</span></footer></main></div></div>`;
}

function roomCards(rooms) {
  if (!rooms.length) return empty('Every good meal starts with good company.', 'Create a table for your family, friends or lunch crew. Everyone gets their own private check-in.', '<div class="inline"><button class="btn" data-action="create-room">Create a table '+icon('plus')+'</button><button class="btn secondary" data-action="join-room" data-i18n="Join%20with%20an%20invite">Join with an invite</button></div>', 'people');
  return `<div class="room-grid">${rooms.map((room,index) => `<article class="card room-card"><div class="spread"><div class="room-emblem">${icon(index % 3 === 1 ? 'heart' : index % 3 === 2 ? 'sun' : 'people')}</div><span class="tag ${room.archived ? 'neutral' : 'outline'}">${room.archived ? t('Archived') : room.owner_id === user().id ? 'Your table' : 'Member'}</span></div><h3 class="break-word">${esc(room.name)}</h3><p>${Number(room.member_count) || 1} ${Number(room.member_count) === 1 ? 'person' : 'people'} at this table</p><div class="room-bottom"><span data-i18n="A%20little%20less%20deciding.">A little less deciding.</span><a href="#room/${encodeURIComponent(room.id)}" aria-label="Open ${esc(room.name)}">Open table ${icon('arrow')}</a></div></article>`).join('')}</div>`;
}
function homePage() {
  const first = user().name.split(' ')[0];
  const activeRooms = state.rooms.filter(room => !room.archived);
  return `<section class="hero"><div class="hero-copy"><div class="eyebrow">HELLO, ${esc(first.toUpperCase())}</div><h1>Good food.<br>Better together.</h1><p>One table. Everyone’s tastes.<br>Make your next meal a shared decision.</p><div class="inline"><button class="btn" data-action="create-room">Create a table ${icon('plus')}</button><button class="text-button" data-action="join-room">Join with an invite ${icon('arrow')}</button></div></div>${tableArt()}</section><div class="stat-row"><div class="stat"><div class="stat-icon">${icon('people')}</div><div><strong>${activeRooms.length}</strong><span data-i18n="Your%20shared%20tables">Your shared tables</span></div></div><div class="stat"><div class="stat-icon">${icon('bell')}</div><div><strong>${state.notifications.filter(n => !n.read).length}</strong><span data-i18n="New%20updates%20for%20you">New updates for you</span></div></div><div class="stat"><div class="stat-icon">${icon('leaf')}</div><div><strong>${profile().requirements_reviewed ? t('Ready') : 'Let’s start'}</strong><span>${profile().requirements_reviewed ? 'Preferences reviewed' : 'Set your food preferences'}</span></div></div></div>${!profile().requirements_reviewed ? `<div class="layout-18 notice" ><div class="spread"><span><strong>Make this feel like you.</strong><br>Set your preferences before joining a meal.</span><a class="btn small" href="#profile" data-i18n="Set%20preferences">Set preferences</a></div></div>` : ''}<div class="section-heading spread"><div><h2 data-i18n="Your%20tables">Your tables</h2><p data-i18n="Your%20friends%2C%20family%20and%20favourite%20lunch%20crews.">Your friends, family and favourite lunch crews.</p></div><button class="text-button" data-action="join-room">Join a table ${icon('arrow')}</button></div>${roomCards(activeRooms)}${inferenceHTML()}${catalogSection()}<div class="section-heading"><h2 data-i18n="A%20meal%20together%2C%20in%20three%20steps.">A meal together, in three steps.</h2></div><div class="how-grid"><div class="how-item"><span class="step-number">01</span><div><h3 data-i18n="Gather%20your%20people">Gather your people</h3><p data-i18n="A%20table%20for%20the%20family%2C%20your%20lunch%20crew%2C%20or%20your%20oldest%20friends.">A table for the family, your lunch crew, or your oldest friends.</p></div></div><div class="how-item"><span class="step-number">02</span><div><h3 data-i18n="Tell%20us%20today%E2%80%99s%20mood">Tell us today’s mood</h3><p data-i18n="A%20quick%2C%20private%20check-in.%20Light%20lunch%3F%20Something%20spicy%3F%20You%20decide.">A quick, private check-in. Light lunch? Something spicy? You decide.</p></div></div><div class="how-item"><span class="step-number">03</span><div><h3 data-i18n="Find%20your%20common%20craving">Find your common craving</h3><p data-i18n="Compare%20supported%20options%2C%20vote%20together%2C%20and%20make%20a%20plan.">Compare supported options, vote together, and make a plan.</p></div></div></div>`;
}
function roomsPage() {
  return `<div class="page-heading spread"><div><div class="eyebrow">THE PEOPLE MAKE THE MEAL</div><h1 data-i18n="Your%20tables">Your tables</h1><p data-i18n="Keep%20each%20friend%20group%E2%80%99s%20meals%20in%20one%20place.">Keep each friend group’s meals in one place.</p></div><div class="inline"><button class="btn secondary" data-action="join-room" data-i18n="Join%20a%20table">Join a table</button><button class="btn" data-action="create-room">${icon('plus')} New table</button></div></div>${roomCards(state.rooms)}`;
}
function mealRows(meals) {
  return `<div class="meal-list">${meals.map(meal => `<a class="meal-row" href="#meal/${encodeURIComponent(meal.id)}"><span class="meal-icon">${icon(meal.kind === 'breakfast' ? 'sun' : 'bowl')}</span><div><h3>${esc(title(meal.kind))} together <span class="tag neutral">${esc(title(meal.status))}</span></h3><p>${esc(dateLabel(meal.meal_at, {hour:'numeric',minute:'2-digit'}))} · ${esc(meal.location_label || 'Location to be confirmed')}</p></div>${icon('arrow')}</a>`).join('')}</div>`;
}
function roomPage() {
  const room = state.room; if (!room) return empty('Table not available', 'It may have been archived, or your membership may have changed.');
  const owner = room.owner_id === user().id;
  return `<div class="page-heading"><a class="text-button" href="#rooms" data-i18n="%E2%86%90%20All%20tables">← All tables</a><div class="spread"><div><h1 class="break-word">${esc(room.name)}</h1><p>${Number(room.member_count)||list(room.members).length} people · ${room.archived ? 'Archived table' : 'Your shared space for the next good meal'}</p></div>${!room.archived ? `<button class="btn" data-action="start-meal">${icon('plus')} Start a meal</button>` : '<span class="tag neutral" data-i18n="Archived">Archived</span>'}</div></div><div class="two-column"><section><div class="layout-13 section-heading" ><h2 data-i18n="Meals%20at%20this%20table">Meals at this table</h2><p data-i18n="From%20the%20first%20check-in%20to%20%E2%80%9Cwe%20should%20come%20back%E2%80%9D.">From the first check-in to “we should come back”.</p></div>${list(room.meals).length ? mealRows(room.meals) : empty('The table is set. What’s next?', 'Start a breakfast, lunch or dinner. Everyone can tell you what sounds good today.', !room.archived ? '<button class="btn" data-action="start-meal">Plan your first meal '+icon('arrow')+'</button>' : '')}</section><div class="stack"><section class="card"><div class="spread"><h3  class="layout-19" data-i18n="At%20the%20table">At the table</h3><span class="tag">${list(room.members).length} people</span></div><div class="member-list">${list(room.members).map(member => `<div class="member">${avatar(member.name)}<div class="member-info"><strong>${esc(member.name)}${member.id === user().id ? ' (you)' : ''}</strong><small>${member.is_owner ? 'Table host' : 'Table member'}</small></div>${owner && member.id !== user().id && !room.archived ? `<button class="text-button" data-action="manage-member" data-id="${esc(member.id)}" data-name="${esc(member.name)}" data-i18n="Manage">Manage</button>` : ''}</div>`).join('')}</div>${owner && !room.archived ? '<button class="layout-16 btn secondary full" data-action="invite" >'+icon('link')+' Create an invite</button>' : ''}<div  class="layout-17">${privacy('Each person’s profile and check-in answers stay private. Shared recommendations do not name anyone’s sensitive requirements.')}</div></section><section class="card"><h3 data-i18n="Table%20settings">Table settings</h3>${state.roomReminderSettings ? `<button class="btn secondary full" data-action="room-reminders" data-muted="${!state.roomReminderSettings.muted}">${state.roomReminderSettings.muted ? t('Unmute this table') : t('Mute reminders for this table')}</button>` : '<p class="help muted" data-i18n="Reminder%20settings%20are%20currently%20unavailable.">Reminder settings are currently unavailable.</p>'}<p class="layout-6 muted" >${owner ? 'As host, you manage invites and membership.' : 'Leave whenever this table is no longer for you.'}</p>${owner ? `<button class="btn danger full" data-action="archive-room" ${room.archived ? 'disabled' : ''} data-i18n="Archive%20this%20table">Archive this table</button>` : '<button class="btn secondary full" data-action="leave-room" data-i18n="Leave%20this%20table">Leave this table</button>'}</section></div></div>`;
}

const cuisineChoices = ['Malaysian','Malay','Chinese','Indian','Mamak','Japanese','Korean','Thai','Vietnamese','Italian','Western','Middle Eastern','Vegetarian'];
const dishChoices = ['soup','noodles','rice','pasta','pizza','salad','sandwich','grill','noodle_soup'];
const flavourChoices = ['rich','light','smoky','sweet','sour','savoury','spicy'];
function choices(name, values, current = [], type = 'checkbox') {
  return `<div class="choice-row">${values.map(value => `<label class="choice"><input type="${type}" name="${esc(name)}" value="${esc(value)}"${checked(type === 'radio' ? current === value : list(current).includes(value))}><span data-i18n="${encodeURIComponent(labels[value] || String(value).replace(/_/g,' ').replace(/\b\w/g,letter=>letter.toUpperCase()))}">${esc(t(labels[value] || title(value)))}</span></label>`).join('')}</div>`;
}
function selectOptions(values, value) { return values.map(([key,label]) => `<option data-i18n="${encodeURIComponent(label)}" value="${esc(key)}"${selected(key,value)}>${esc(t(label))}</option>`).join(''); }
function preferenceDraftHTML() {
  const draft=state.preferenceDraft;if(!draft)return '';
  if(draft.status==='loading')return '<div class="preference-preview" data-preference-preview role="status">Drafting editable choices…</div>';
  const messages={unavailable:'AI is unavailable. Your ordinary check-in still works.',needs_requirement_review:'This may describe a food requirement. Review your private food requirements instead of treating it as a taste.',private_text:'This text may contain private contact or account information, so it was not sent.',uncertain:'The text was unclear. Edit the ordinary choices yourself.',no_suggestions:'No clear taste choices were found. Edit the ordinary choices yourself.'};
  if(draft.status!=='proposed')return `<div class="notice warning preference-preview" data-preference-preview role="status">${esc(messages[draft.status]||'The draft could not be used. Edit your choices yourself.')}</div>`;
  const s=draft.suggestions||{};const parts=[...list(s.cuisines),...list(s.dish_families),...list(s.flavour_tags),s.appetite,s.spice,s.soft_budget_target?`About RM${s.soft_budget_target}`:null,...list(s.occasion_features),s.novelty].filter(Boolean);
  return `<div class="preference-preview" data-preference-preview><strong>Review the AI draft</strong><p>${esc(parts.join(' · ')||'No suggested choices')}</p><p class="help">Nothing is saved yet. Apply this draft, edit every choice, then submit your check-in to confirm it.</p><button class="btn secondary small" type="button" data-action="apply-preference-draft">Apply draft to my choices</button></div>`;
}
function updatePreferenceDraft(form) {
  const region=$('[data-preference-region]',form);
  if(region)region.innerHTML=preferenceDraftHTML();
}
function clearPreferenceDraft(form) {
  state.preferenceRequest+=1;state.preferenceDraft=null;updatePreferenceDraft(form);
}
function profileFormPage() {
  const p = profile();
  return `<div class="page-heading"><div class="eyebrow">YOUR PRIVATE CORNER</div><h1 data-i18n="A%20little%20about%20your%20appetite.">A little about your appetite.</h1><p data-i18n="Keep%20the%20everyday%20preferences.%20Change%20your%20mood%20at%20each%20meal.">Keep the everyday preferences. Change your mood at each meal.</p></div><div class="two-column"><form class="card" data-form="profile" data-profile-revision="${Number(p.profile_revision)||0}"><section class="form-section"><h3 data-i18n="Your%20food%20requirements">Your food requirements</h3><p class="muted" data-i18n="These%20help%20us%20check%20options.%20Unknown%20information%20stays%20unknown.">These help us check options. Unknown information stays unknown.</p><div class="field"><label for="allergy-status" data-i18n="Food%20allergies">Food allergies</label><select name="allergy_status" id="allergy-status">${selectOptions([['unknown','I haven’t reviewed this yet'],['none','No known food allergies'],['declared','I have food allergies'],['withheld','I prefer not to share']],p.allergy_status||'unknown')}</select></div><div class="field"><label for="allergens" data-i18n="Allergens%20to%20avoid">Allergens to avoid</label><input name="allergens" id="allergens" value="${esc(list(p.allergens).join(', '))}" placeholder="For example: peanuts, shellfish"><span class="help">Separate each with a comma. These are stored only when you select “I have food allergies”. A menu alone cannot establish absence or cross-contact safety.</span></div><div class="field"><label for="dietary" data-i18n="Other%20dietary%20requirements">Other dietary requirements</label><input name="dietary_requirements" id="dietary" value="${esc(list(p.dietary_requirements).join(', '))}" placeholder="For example: vegetarian, no beef"><span class="help" data-i18n="Requirements%20are%20different%20from%20things%20you%20simply%20don%E2%80%99t%20feel%20like%20eating%20today.">Requirements are different from things you simply don’t feel like eating today.</span></div><div class="field"><label for="halal-policy" data-i18n="Your%20halal%20requirement">Your halal requirement</label><select name="halal_policy" id="halal-policy">${selectOptions([['unknown','I haven’t reviewed this yet'],['none','No halal requirement'],['certified','Current certification evidence required'],['review','Show the evidence for me to review']],p.halal_policy||'unknown')}</select></div><div class="consent-box"><label class="check-label"><input type="checkbox" name="sensitive_data_consent"${checked(p.sensitive_data_consent)}><span data-i18n="I%20agree%20to%20this%20app%20storing%20and%20using%20the%20sensitive%20food%20requirements%20I%20choose%20to%20share%20for%20meal%20recommendations.%20I%20can%20edit%20or%20delete%20them.">I agree to this app storing and using the sensitive food requirements I choose to share for meal recommendations. I can edit or delete them.</span></label></div><label class="layout-14 check-label" ><input type="checkbox" name="requirements_reviewed"${checked(p.requirements_reviewed)}><span data-i18n="I%20have%20reviewed%20my%20food%20requirements%20above.">I have reviewed my food requirements above.</span></label></section><section class="form-section"><h3 data-i18n="Things%20you%20usually%20enjoy">Things you usually enjoy</h3><p class="muted" data-i18n="Helpful%20starting%20points%2C%20never%20rules%20about%20what%20you%20must%20eat.">Helpful starting points, never rules about what you must eat.</p><fieldset><legend data-i18n="Cuisines%20you%20enjoy">Cuisines you enjoy</legend>${choices('cuisines',cuisineChoices,p.cuisines)}</fieldset><div class="form-grid"><div class="field"><label for="profile-spice" data-i18n="Usual%20spice%20level">Usual spice level</label><select name="spice" id="profile-spice">${selectOptions([['any','Happy with any'],['none','No chilli'],['mild','Mild'],['medium','Medium'],['hot','Hot']],p.spice||'any')}</select></div><div class="field"><label for="max-budget" data-i18n="Usual%20budget%20per%20person%20%28RM%29">Usual budget per person (RM)</label><input id="max-budget" name="max_budget" type="number" min="1" max="2000" step="0.50" value="${esc(p.max_budget ?? '')}" placeholder="Optional"></div><div class="field"><label for="mobility" data-i18n="How%20you%20usually%20travel">How you usually travel</label><select name="mobility_mode" id="mobility">${selectOptions([['drive','Driving'],['walk','Walking'],['transit','Public transport'],['ehailing','E-hailing']],p.mobility_mode||'drive')}</select><span class="help">Saved for future route support. This pilot checks straight-line distance, not travel time by transport mode.</span></div><div class="field"><label for="language" data-i18n="Preferred%20language">Preferred language</label><select name="language" id="language">${selectOptions([['en','English'],['ms','Bahasa Melayu']],p.language||'en')}</select><span class="help">English and Bahasa Melayu interface labels are available. Restaurant source text and some notices keep their original language.</span></div></div></section><section class="form-section"><h3 data-i18n="Your%20history%2C%20on%20your%20terms">Your history, on your terms</h3><label class="check-label"><input name="reminders_enabled" type="checkbox"${checked(state.reminderSettings?.reminders_enabled)}${state.reminderSettings ? '' : ' disabled'}><span data-i18n="Send%20meal%20check-in%20and%20feedback%20reminders%20to%20my%20inbox.">Send meal check-in and feedback reminders to my inbox.</span></label><p class="help muted">${state.reminderSettings ? 'Optional. These are in-app reminders, not browser push notifications.' : 'Reminder settings are unavailable right now. Your existing choice is unchanged.'}</p><label class="check-label"><input name="memory_enabled" type="checkbox"${checked(p.memory_enabled)}><span data-i18n="Allow%20my%20confirmed%20feedback%20to%20inform%20future%20suggestions.%20New%20inferred%20preferences%20should%20be%20offered%20for%20my%20review.">Allow my confirmed feedback to inform future suggestions. New inferred preferences should be offered for my review.</span></label><p class="layout-3 help muted"  data-i18n="This%20choice%20does%20not%20automatically%20alter%20allergies%20or%20other%20requirements.">This choice does not automatically alter allergies or other requirements.</p></section>${formError()}<button class="btn full" type="submit">Save my preferences ${icon('check')}</button></form><div class="stack"><section class="card"><div class="room-emblem">${icon('lock')}</div><h3 data-i18n="Private%20means%20personal.">Private means personal.</h3><p class="layout-8 muted"  data-i18n="Your%20table%20can%20see%20your%20name%20and%20whether%20you%E2%80%99re%20ready.%20Your%20individual%20food%20requirements%20and%20answers%20are%20not%20a%20shared%20profile.">Your table can see your name and whether you’re ready. Your individual food requirements and answers are not a shared profile.</p>${privacy('The service uses the relevant constraints to check a shared shortlist without exposing who supplied them.')}</section><section class="card"><h3 data-i18n="Your%20account">Your account</h3>${emailAccountHTML()}<p class="layout-6 muted break-word" >${esc(user().email)}</p><button class="btn secondary full" data-action="export">${icon('download')} Export my data</button><div class="account-danger"><h3 data-i18n="Delete%20your%20account">Delete your account</h3><p class="layout-6 muted"  data-i18n="Remove%20your%20account%20and%20personal%20data.%20Tables%20you%20host%20will%20be%20archived%20unless%20you%20transfer%20ownership%20first.">Remove your account and personal data. Tables you host will be archived unless you transfer ownership first.</p><button class="btn danger full" data-action="delete-account" data-i18n="Delete%20my%20account">Delete my account</button></div></section></div></div>`;
}


function profilePage() { return profileFormPage() + learningHTML(); }
function learningHTML() {
  const data = state.learning;
  if (!data || data.unavailable) return '<section class="card learning-section"><h2 data-i18n="Your%20saved%20learning">Your saved learning</h2><p class="muted" data-i18n="Learning%20history%20is%20unavailable%20right%20now.%20Your%20profile%20can%20still%20be%20edited%20above.">Learning history is unavailable right now. Your profile can still be edited above.</p></section>';
  const observations = list(data.observations);
  const proposals = list(data.proposals).filter(proposal => proposal.status === 'pending');
  const saved = list(data.venue_preferences || data.proposals).filter(proposal => proposal.status === 'accepted');
  const sourceLinks = proposal => {
    const ids = new Set([...list(proposal.source_observation_ids),proposal.observation_id]);
    const meals = observations.filter(observation=>ids.has(observation.id));
    return meals.length ? `<div class="learning-sources"><span>${esc(t('Based on your feedback from:'))}</span>${meals.map(observation=>`<a class="text-button" href="#meal/${encodeURIComponent(observation.meal_id)}">${esc(dateLabel(observation.created_at))} ${esc(t('meal'))} ${icon('arrow')}</a>`).join('')}</div>` : '';
  };
  const venueLabel = item => item.outlet_name || `${t('Restaurant reference')}: ${item.outlet_id || t('Unavailable')}`;
  return `<section class="learning-section"><div class="section-heading spread"><div><h2 data-i18n="Your%20saved%20learning">Your saved learning</h2><p data-i18n="Keep%20what%20helps.%20Remove%20what%20no%20longer%20represents%20you.">Keep what helps. Remove what no longer represents you.</p></div>${observations.length || list(data.proposals).length ? '<button class="btn secondary small" data-action="clear-learning" data-i18n="Clear%20learned%20history">Clear learned history</button>' : ''}</div><p class="privacy-caption" data-i18n="Only%20you%20can%20see%20these%20individual%20observations.%20They%20never%20change%20your%20allergies%20or%20other%20requirements.">Only you can see these individual observations. They never change your allergies or other requirements.</p>${proposals.length ? `<div class="learning-grid">${proposals.map(proposal => `<article class="card"><span class="tag" data-i18n="Your%20confirmation%20needed">Your confirmation needed</span><h3>${esc(t(proposal.would_repeat === false ? 'Remember that you’d rather avoid this restaurant?' : 'Remember this as a place you’d return to?'))}</h3><p class="muted break-word">${esc(venueLabel(proposal))}</p><p>${esc(t('Review the original meals before confirming. You can keep this as feedback only.'))}</p>${sourceLinks(proposal)}<div class="inline"><button class="btn small" data-action="learning-proposal" data-id="${esc(proposal.id)}" data-decision="accept" data-i18n="Save%20as%20a%20usual%20preference">Save as a usual preference</button><button class="btn secondary small" data-action="learning-proposal" data-id="${esc(proposal.id)}" data-decision="reject" data-i18n="Keep%20only%20as%20feedback">Keep only as feedback</button></div></article>`).join('')}</div>` : ''}${saved.length ? `<div class="card"><h3>${esc(t('Your confirmed restaurant preferences'))}</h3><p class="help muted">${esc(t('Remove a supporting observation or clear learned history to revoke a saved preference.'))}</p>${saved.map(preference=>`<article class="learning-preference"><strong class="break-word">${esc(venueLabel(preference))}</strong><p>${esc(t(preference.would_repeat ? 'Would return' : 'Would prefer somewhere else'))}</p>${sourceLinks(preference)}</article>`).join('')}</div>` : ''}${observations.length ? `<div class="card observation-list">${observations.map(observation => `<article><div><h3 class="break-word">${esc(venueLabel(observation))}</h3><p class="muted">${esc(dateLabel(observation.created_at))}${observation.rating != null ? ' · '+esc(observation.rating)+'/5' : ''}${observation.enjoyment ? ' · '+esc(title(observation.enjoyment)) : ''}${observation.would_repeat === true ? ' · '+esc(t('Would return')) : observation.would_repeat === false ? ' · '+esc(t('Would prefer somewhere else')) : ''}</p>${list(observation.influences).length ? `<p class="help">${list(observation.influences).map(value=>esc(title(value))).join(' · ')}</p>` : ''}${observation.meal_id ? `<a class="text-button" href="#meal/${encodeURIComponent(observation.meal_id)}">${esc(t('Review this meal'))} ${icon('arrow')}</a>` : ''}</div><button class="text-button" data-action="delete-observation" data-id="${esc(observation.id)}" data-i18n="Remove%20observation">Remove observation</button></article>`).join('')}</div>` : empty('Nothing learned yet.',data.enabled ? 'Confirmed meal feedback can create observations here. You control which lasting preferences to keep.' : 'Personalised learning is off. Your explicit profile preferences still apply.','','leaf')}</section>`;
}
function participantPicker(members, selectedIds, organizerId) {
  return `<fieldset class="participant-picker"><legend data-i18n="Who%20should%20join%3F">Who should join?</legend><p class="help muted" data-i18n="Choose%202%E2%80%938%20people%2C%20including%20the%20meal%20host.%20Other%20room%20members%20will%20not%20be%20invited%20to%20this%20meal.">Choose 2–8 people, including the meal host. Other room members will not be invited to this meal.</p>${members.map(member => `<label class="check-label"><input type="checkbox" name="participant_ids" value="${esc(member.id)}"${checked(selectedIds.includes(member.id))}${member.id === organizerId ? ' disabled' : ''}><span>${esc(member.name)}${member.id === organizerId ? ' · Meal host' : ''}</span></label>`).join('')}<input type="hidden" name="participant_ids" value="${esc(organizerId)}"></fieldset>`;
}
async function participantModal() {
  const meal = state.meal;
  const response = await api(`/rooms/${encodeURIComponent(meal.room_id)}`);
  const room = response.room || response;
  const ids = list(meal.participants).map(person=>person.id);
  modal('Review meal participants', `<p data-i18n="A%20participant%20change%20clears%20the%20old%20shortlist%20and%20votes.%20People%20removed%20from%20this%20meal%20are%20notified%3B%20their%20private%20profiles%20stay%20theirs.">A participant change clears the old shortlist and votes. People removed from this meal are notified; their private profiles stay theirs.</p><form data-form="participants" data-revision="${meal.revision}" data-previous="${esc(JSON.stringify(ids))}">${participantPicker(list(room.members),ids,meal.organizer_id)}<div data-participant-review class="notice"></div><label class="check-label"><input type="checkbox" name="confirm_roster" required><span data-i18n="I%20confirm%20the%20named%20participant%20changes%20below.">I confirm the named participant changes below.</span></label>${formError()}<button class="btn full" type="submit" data-i18n="Confirm%20participant%20list">Confirm participant list</button></form>`);
  const form = $('[data-form="participants"]'); form.memberNames = Object.fromEntries(list(room.members).map(member=>[member.id,member.name])); updateParticipantReview(form);
}
function updateParticipantReview(form) {
  const ids = [...new Set(new FormData(form).getAll('participant_ids'))];
  const previous = JSON.parse(form.dataset.previous || '[]');
  const excluded = previous.filter(id=>!ids.includes(id));
  const included = ids.map(id=>form.memberNames?.[id] || 'Room member');
  const target = $('[data-participant-review]',form);
  if (target) { target.replaceChildren(); const includedLine = document.createElement('p'); includedLine.textContent = `Included (${ids.length}): ${included.join(', ')}`; target.append(includedLine); const excludedLine = document.createElement('p'); excludedLine.textContent = excluded.length ? `Excluded from this meal: ${excluded.map(id=>form.memberNames?.[id] || 'Room member').join(', ')}` : 'No current participant is excluded.'; target.append(excludedLine); }
}
function voteHTML(meal, option) {
  if (meal.decision || terminalMeal(meal)) return '';
  const id = option.id || option.option_id || option.outlet_id;
  const previous = meal.my_votes?.[id];
  const choice = previous?.choice || (meal.my_vote?.[id] === true ? 'works' : meal.my_vote?.[id] === false ? 'prefer_another' : '');
  return `<div class="vote-controls"><p class="help muted" data-i18n="Your%20answer%20is%20private.%20%E2%80%9CCannot%20eat%20here%E2%80%9D%20blocks%20this%20choice%20until%20you%20change%20your%20response.">Your answer is private. “Cannot eat here” blocks this choice until you change your response.</p><div class="option-actions">${['works','prefer_another','cannot_eat'].map(value=>`<button class="btn small ${choice===value?'':'secondary'}" data-action="choose-vote" data-id="${esc(id)}" data-choice="${value}" aria-pressed="${choice===value}">${esc(t(labels[value]))}</button>`).join('')}</div>${previous?.reason ? `<p class="private-vote-reason"><strong>Your private reason:</strong> ${esc(previous.reason)}</p>` : ''}</div>`;
}
function delegationHTML(meal) {
  if (meal.decision || terminalMeal(meal) || meal.result?.status !== 'shortlisted' || !list(meal.frozen_participant_ids).includes(user().id)) return '';
  const enabled = meal.my_delegation?.enabled === true;
  return `<section class="notice delegation-card"><h3 data-i18n="Let%20the%20group%20choose%3F">Let the group choose?</h3><p data-i18n="You%20can%20accept%20any%20currently%20listed%20option%20that%20meets%20your%20requirements.%20A%20%E2%80%9CPrefer%20another%E2%80%9D%20or%20%E2%80%9CCannot%20eat%20here%E2%80%9D%20response%20still%20excludes%20that%20option%20from%20your%20permission.%20You%20can%20revoke%20this%20until%20selection.">You can accept any currently listed option that meets your requirements. A “Prefer another” or “Cannot eat here” response still excludes that option from your permission. You can revoke this until selection.</p><button class="btn ${enabled?'secondary':''} small" data-action="delegation" data-enabled="${!enabled}" data-revision="${meal.revision}">${enabled?t('Revoke my delegation'):t('Review group-choice permission')}</button><span class="tag neutral">${enabled?'Enabled for this shortlist':'Not delegated'}</span></section>`;
}
function lifecycleNotice(meal) {
  const descriptions = {
    reconfirmation_required:'Something important changed after your group chose. The old decision is no longer current. Review your check-in and agree on a new shortlist.',
    decision_overdue:'The decision deadline has passed. No choice has been assumed. The host can explicitly revise the schedule or cancel.',
    expired:'This meal’s planned time has ended without a completed decision. Start a new meal when the group is ready.',
    closed:'This meal is closed. Its history remains available.',
    awaiting_feedback:'Your meal is complete. Tell us what actually happened when you have a moment.',
  };
  if (!descriptions[meal.status]) return '';
  return `<div class="notice lifecycle-notice"><strong>${esc(title(meal.status))}</strong><p>${esc(descriptions[meal.status])}</p>${meal.status==='expired' ? `<a class="btn secondary small" href="#room/${encodeURIComponent(meal.room_id)}" data-i18n="Plan%20another%20meal">Plan another meal</a>` : ''}</div>`;
}

function readiness(meal) {
  const participants = list(meal.participants);
  const joined = participants.filter(p => p.attendance === 'join');
  const ready = joined.filter(p => p.ready).length;
  const pending = participants.filter(p => p.attendance === 'pending');
  const exhausted=meal.generation_job?.status==='failed' && meal.generation_job?.retryable===false && meal.generation_job.context_revision===meal.revision;
  const canGenerate = !exhausted && joined.length >= 2 && ready === joined.length && joined.some(p => p.id === user().id && p.ready) && new Date(meal.decision_by).getTime() > Date.now() && meal.status !== 'generating';
  return `<section class="card"><div class="spread"><h3  class="layout-19" data-i18n="Who%E2%80%99s%20in%3F">Who’s in?</h3><span class="tag" data-i18n="Private%20check-ins">Private check-ins</span></div><div class="layout-18 spread" ><div class="ready-count">${ready}<span> / ${joined.length}</span></div><span class="layout-2 muted"  data-i18n="joined%20%26%20ready">joined & ready</span></div><progress class="progress-track" value="${ready}" max="${joined.length || 1}" aria-label="Joined participants who are ready"></progress>${participants.map(p => `<div class="participant">${avatar(p.name)}<span class="break-word">${esc(p.name)}${p.id === user().id ? ' (you)' : ''}</span><span class="tag ${p.attendance === 'decline' ? 'neutral' : p.ready ? '' : 'orange'}">${p.attendance === 'decline' ? t('Not joining') : p.ready ? t('Ready') : p.attendance === 'join' ? t('Checking in') : t('Invited')}</span></div>`).join('')}<div  class="layout-18">${privacy('You can see readiness, never someone else’s private answers.')}</div>${meal.organizer_id === user().id && !meal.decision && !terminalMeal(meal) ? `<div class="divider"></div><button class="btn secondary full roster-edit" data-action="participants" data-i18n="Review%20participant%20list">Review participant list</button><button class="btn full" data-action="generate" ${canGenerate ? '' : 'disabled'}>${icon('bowl')} ${pending.length ? t('Review unanswered invites') : meal.result ? t('Refresh suggestions') : t('Find a meal together')}</button>${pending.length ? `<p class="muted readiness-help">${pending.length} invited ${pending.length === 1 ? 'person has' : 'people have'} not answered. You must explicitly confirm their exclusion before continuing with the joined group.</p>` : ''}<p class="layout-1 muted"  data-i18n="At%20least%20two%20people%20must%20join%20and%20be%20ready.%20Changes%20to%20the%20group%20or%20answers%20require%20a%20fresh%20shortlist.">At least two people must join and be ready. Changes to the group or answers require a fresh shortlist.</p>` : `<p class="layout-5 muted">${meal.status === 'cancelled' ? 'This meal is cancelled.' : meal.decision ? 'The table has made its choice.' : 'Your meal host can generate suggestions when the group is ready.'}</p>`}</section>`;
}
function checkinForm(meal) {
  const p = profile(); const answer = meal.my_response || {};
  if (meal.decision) return '';
  if (terminalMeal(meal)) return `<div class="notice">${esc(title(meal.status))}. This meal no longer accepts check-ins. You can plan another meal from your table.</div>`;
  return `<form class="card" data-form="checkin" data-response-revision="${Number(meal.my_response_revision)||0}" data-edit-epoch="0">
    <input type="hidden" name="taste_input_mode" value="${esc(answer.taste_input_mode||'legacy_text')}">
    <div class="spread"><div><div class="eyebrow">JUST BETWEEN YOU AND YOUR APPETITE</div><h2 class="layout-12">What sounds good today?</h2></div>${icon('leaf')}</div>
    <p class="layout-7 muted">Your answers help find the overlap. They aren’t shown to the room.</p>
    <fieldset><legend>Will you join this meal?</legend>${choices('attendance',['join','decline','pending'],answer.attendance||'pending','radio')}</fieldset>
    <fieldset data-joining-fields ${answer.attendance==='join'?'':'disabled'}>
      <button class="btn secondary full usual-preferences" type="button" data-action="use-usual">Use my usual preferences</button>
      <p class="help muted">This fills taste choices from your profile. Review today’s requirements and budget before submitting.</p>
      <fieldset><legend>Any cuisines on your mind?</legend>${choices('cuisines',cuisineChoices,answer.cuisines||[])}</fieldset>
      <div class="field"><label for="craving">Describe your craving</label><input id="craving" name="craving" value="${esc(answer.craving||'')}" placeholder="Something warm and soupy, maybe noodles…" maxlength="500"><span class="help">English, Bahasa Melayu, or a mix is fine. Leave blank if you’re open.</span></div>
      <button class="text-button ai-draft-button" type="button" data-action="interpret-preferences">Draft my taste choices with AI</button>
      <p class="help">Optional. Only this craving text is sent to the configured model after you review it. Do not include allergies, health details, contact details or secrets.</p>
      <div data-preference-region>${preferenceDraftHTML()}</div>
      <fieldset><legend>How hungry are you?</legend>${choices('appetite',['light','regular','hearty'],answer.appetite||'regular','radio')}</fieldset>
      <div class="form-grid"><div class="field"><label for="meal-spice">Spice today</label><select id="meal-spice" name="spice">${selectOptions([['any','Anything works'],['none','None'],['mild','Mild'],['medium','Medium'],['hot','Hot']],answer.spice||'any')}</select></div><div class="field"><label for="meal-budget">Firm maximum today (RM per person)</label><input id="meal-budget" name="budget" type="number" min="1" max="2000" step="0.50" value="${esc(answer.budget??p.max_budget??'')}"${answer.ready===false?'':' required'}><span class="help">Food, drinks and required charges combined. This is a firm limit.</span></div></div>
      <div class="form-grid"><div class="field"><label for="soft-budget">Comfortable target (RM, optional)</label><input id="soft-budget" name="soft_budget_target" type="number" min="1" max="2000" step="0.50" value="${esc(answer.soft_budget_target??'')}"></div><div class="field"><label for="novelty">Familiar or something new?</label><select id="novelty" name="novelty">${selectOptions([['any','No preference'],['familiar','Familiar favourites'],['variety','Some variety'],['explore','Something new']],answer.novelty||'any')}</select></div></div>
      <details class="feedback-details"><summary>More about today (optional)</summary>
        <fieldset><legend>What kind of dish sounds good?</legend>${choices('dish_families',dishChoices,answer.dish_families||[])}</fieldset>
        <fieldset><legend>Which flavours sound good?</legend>${choices('flavour_tags',flavourChoices,answer.flavour_tags||[])}</fieldset>
        <fieldset><legend>Venue preferences today</legend>${choices('occasion_features',['quick','quiet','indoor'],answer.occasion_features||[])}</fieldset>
        <div class="field"><label for="comfortable-travel">Comfortable travel time (minutes, optional)</label><input id="comfortable-travel" name="comfortable_travel_minutes" type="number" min="1" max="180" value="${esc(answer.comfortable_travel_minutes??'')}"><span class="help">A preference, not a verified travel-time guarantee.</span></div>
      </details>
      <div class="field"><label for="avoid">Not in the mood for…</label><input id="avoid" name="avoid" value="${esc(list(answer.avoid).join(', '))}" placeholder="Optional, separate with commas" maxlength="300"></div>
      <div class="consent-box"><label class="check-label"><input type="checkbox" name="requirements_confirmed"${checked(answer.requirements_confirmed)}><span>My <a href="#profile">private food requirements</a> are up to date for this meal.</span></label><label class="check-label"><input type="checkbox" name="ready"${checked(answer.ready??true)}><span>I’m ready for the group to use these answers.</span></label></div>
    </fieldset>${formError()}<div data-response-conflict></div><button class="btn full" type="submit">${meal.my_response?t('Update my check-in'):t('Send my check-in')} ${icon('check')}</button>
  </form>`;
}

function evidenceHTML(evidence) {
  if (!list(evidence).length) return '<p data-i18n="No%20source%20evidence%20is%20attached%20to%20this%20option.">No source evidence is attached to this option.</p>';
  return `<ul>${evidence.map(item => {
    if (typeof item === 'string') return `<li>${esc(item)}</li>`;
    const url = safeURL(item.url || item.source_url);
    const label = item.title || item.label || item.source_id || item.field || 'Source';
    return `<li>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : esc(label)}${item.claim || item.value ? ': '+esc(item.claim || (typeof item.value === 'object' ? JSON.stringify(item.value) : item.value)) : ''}${(item.fetched_at || item.observed_at) ? ' · observed '+esc(dateLabel(item.fetched_at || item.observed_at)) : ''}${item.status ? ' · '+esc(title(item.status)) : ''}</li>`;
  }).join('')}</ul>`;
}
function optionsHTML(meal) {
  const result = meal.result; if (!result) return '';
  const options = list(result.options || result.candidates);
  let header = `<div class="section-heading"><div class="eyebrow">A LITTLE COMMON GROUND</div><h2 data-i18n="Your%20table%E2%80%99s%20shortlist">Your table’s shortlist</h2><p data-i18n="Compare%20the%20evidence%2C%20share%20a%20vote%2C%20and%20choose%20together.">Compare the evidence, share a vote, and choose together.</p></div>`;
  if (result.explanation) header += `<div class="notice">${esc(readableText(result.explanation))}</div>`;
  header += resultCoverage(result) + delegationHTML(meal);
  if (result.status !== 'shortlisted' && !options.length) {
    const status = result.status === 'needs_verification' ? 'A few facts need checking first.' : 'No supported matches in this catalog yet.';
    return header + empty(status, readableText(result.message) || readableText(result.reason) || 'We won’t fill the gap with a guess. Review the information below. A limited collection cannot tell us that no suitable restaurant exists elsewhere.', '', 'shield') + verificationHTML(result.verification);
  }
  return header + verificationHTML(result.verification) + `<div class="option-grid">${options.map((option,index) => {
    const id = option.id || option.option_id || option.outlet_id;
    const chosen = meal.decision?.option_id === id;
    const vote = meal.my_vote?.[id];
    const acceptance = meal.acceptance?.[id];
    const range = option.price_range || {};
    const low = range.min_minor ?? option.price_min_minor ?? option.estimated_cost_minor;
    const high = range.max_minor ?? option.price_max_minor;
    const cost = low != null ? currency(low)+(high != null && high !== low ? '–'+currency(high) : '') : 'Price unknown';
    return `<article class="card option-card ${chosen ? 'option-selected' : ''}"><div class="option-top"><div><span class="tag outline">${chosen ? 'Your chosen meal' : `Option ${index+1}`}</span><h3 class="break-word">${esc(option.name || option.outlet_name || 'Meal option')}</h3><p>${esc(option.area || option.location_label || '')}</p></div><div class="option-price">${esc(cost)}<small>${low != null ? 'per person · check details' : 'See source details'}</small></div></div><div class="option-body">${option.description ? `<p>${esc(option.description)}</p>` : ''}<div class="inline">${list(option.cuisines).map(c => `<span class="tag neutral">${esc(c)}</span>`).join('')}</div><ul class="reason-list">${list(option.reasons).map(reason => `<li>${icon('check')}<span>${esc(typeof reason === 'string' ? reason : reason.text || reason.message || JSON.stringify(reason))}</span></li>`).join('')}</ul>${list(option.tradeoffs).length ? `<div class="notice warning">${list(option.tradeoffs).map(t => `<div>${esc(typeof t === 'string' ? t : t.message || JSON.stringify(t))}</div>`).join('')}</div>` : ''}${list(option.menu_items).length ? `<details><summary data-i18n="Suggested%20dishes">Suggested dishes</summary><ul>${option.menu_items.map(item => `<li>${esc(typeof item === 'string' ? item : item.name || item.item_name || 'Dish')}${item.price_minor != null ? ' — '+esc(currency(item.price_minor)) : ''}</li>`).join('')}</ul></details>` : ''}<details><summary data-i18n="Sources%20%26%20what%20we%20know">Sources & what we know</summary>${evidenceHTML(option.evidence || option.sources)}<p data-i18n="Source%20information%20is%20limited%20to%20what%20was%20collected.%20A%20matching%20menu%20is%20not%20a%20guarantee%20of%20allergen%20safety%20or%20current%20availability.">Source information is limited to what was collected. A matching menu is not a guarantee of allergen safety or current availability.</p></details>${acceptance ? `<p class="muted acceptance-count">${Number(acceptance.approved_count)||0} of ${Number(acceptance.required_count)||0} included people approve this option.</p>` : ''}${!meal.decision ? `${voteHTML(meal,option)}${meal.organizer_id === user().id ? `<button class="layout-22 text-button" data-action="select" data-id="${esc(id)}" data-name="${esc(option.name || option.outlet_name || 'this option')}" ${acceptance && acceptance.approved_count < acceptance.required_count ? 'disabled title="Every included participant must approve this option first"' : ''} >Choose for this meal ${icon('arrow')}</button>` : ''}` : chosen ? '<div class="layout-16 notice" >'+icon('check')+'Selected for your table.</div>' : ''}<div class="spread" style="margin-top:0.5rem;"><button type="button" class="text-button small report-data-btn" data-action="report-data-error" data-outlet-id="${esc(option.outlet_id || '')}" data-name="${esc(option.name || option.outlet_name || '')}">Report data or dietary issue</button></div></div></article>`;
  }).join('')}</div>${(() => {
    const fullyApproved = options.filter(opt => {
      const a = meal.acceptance?.[opt.id || opt.option_id || opt.outlet_id];
      return a && a.approved_count >= a.required_count;
    });
    if (meal.organizer_id === user().id && !meal.decision && fullyApproved.length >= 2) {
      return `<div class="card inline spread" style="margin-top:1rem;"><div><strong>Multiple options approved (${fullyApproved.length})</strong><p class="muted">You can select a specific option above, break ties by score, or conduct an agreed random draw.</p></div><div class="inline"><button class="btn secondary small" data-action="tiebreak-select" data-revision="${meal.revision}">Score tie-break</button><button class="btn secondary small" data-action="random-draw-select" data-revision="${meal.revision}">Agreed random draw</button></div></div>`;
    }
    return '';
  })()}`;
}
function manualPlanHTML(meal) {
  const plan=meal.manual_plan;if(!plan)return '';
  const counts=meal.manual_acceptance||{};const included=list(meal.frozen_participant_ids).includes(user().id);
  return `<section class="card manual-plan-card"><span class="tag outline">Your own plan — restaurant checks unresolved</span><h2>${esc(plan.name)}</h2>${plan.address?`<p>${esc(plan.address)}</p>`:''}${plan.note?`<p class="muted">${esc(plan.note)}</p>`:''}<div class="notice warning"><strong>This is an unverified group choice.</strong><p>Restaurant availability, prices and dietary requirements have not been confirmed by the app. An acknowledgment records your understanding; it does not make the place suitable or safe.</p></div>${meal.decision?'<p class="manual-ack-count">The group recorded this plan with unresolved checks.</p>':`<p class="manual-ack-count">${Number(counts.acknowledged_count)||0} of ${Number(counts.required_count)||0} included people have acknowledged the unresolved checks.</p>${included?`<form data-form="manual-ack" data-revision="${meal.revision}"><label class="check-label"><input name="acknowledge_unverified" type="checkbox"${checked(meal.my_manual_ack)} required><span>I understand the checks are unresolved and agree to record this unverified group plan.</span></label>${formError()}<div class="inline"><button class="btn secondary small" type="submit">Save my acknowledgment</button>${meal.my_manual_ack?'<button class="text-button" type="button" data-action="revoke-manual-ack">Revoke my acknowledgment</button>':''}</div></form>`:''}${meal.organizer_id===user().id?`<button class="btn full manual-select" data-action="manual-select" data-revision="${meal.revision}" ${Number(counts.required_count)<2||Number(counts.acknowledged_count)!==Number(counts.required_count)?'disabled':''}>Record this unverified plan</button>`:''}`}</section>`;
}
function manualPlanModal() {
  const meal=state.meal;const pending=list(meal.participants).filter(person=>person.attendance==='pending');const joined=list(meal.participants).filter(person=>person.attendance==='join');
  modal('Record your own plan', `<p>This is a coordination option when you already have a place in mind. It is not a checked restaurant recommendation. Each included person must acknowledge the unresolved checks.</p><p><strong>Included:</strong> ${esc(joined.map(person=>person.name).join(', ')||'No joined participants yet')}</p><form data-form="manual-plan" data-revision="${meal.revision}"><div class="field"><label for="manual-name">Place name</label><input id="manual-name" name="name" required maxlength="120"></div><div class="field"><label for="manual-address">Address or meeting instructions (optional)</label><input id="manual-address" name="address" maxlength="300"></div><div class="field"><label for="manual-note">Shared note (optional)</label><textarea id="manual-note" name="note" maxlength="500"></textarea><span class="help">Do not put another person’s private requirements in a shared note.</span></div>${pending.length?`<p>Not joined: ${esc(pending.map(person=>person.name).join(', '))}</p><label class="check-label"><input name="exclude_pending" type="checkbox" required><span>I explicitly exclude these unanswered invitees from this plan.</span></label>`:''}<label class="check-label"><input name="confirm_unverified" type="checkbox" required><span>I understand this plan does not claim restaurant checks have passed.</span></label>${formError()}<button class="btn full" type="submit">Propose this unverified plan</button></form>`);
}

function feedbackHTML(meal) {
  if (!meal.decision) return '';
  const option = list(meal.result?.options).find(o => (o.id || o.option_id || o.outlet_id) === meal.decision.option_id);
  const feedback = meal.my_feedback || {};
  const canFeedback=Date.now()>=new Date(meal.meal_at).getTime()+Number(meal.duration_minutes||60)*60000;
  const outcome = feedback.outcome || (feedback.visited === true ? 'ate_here' : feedback.visited === false ? 'plans_changed' : 'not_yet');
  const enjoyment = feedback.enjoyment || (feedback.rating >= 4 ? 'enjoyed' : feedback.rating >= 3 ? 'okay' : feedback.rating ? 'did_not_enjoy' : 'skipped');
  const repeat = feedback.repeat_intent || (feedback.would_repeat === true ? 'yes' : feedback.would_repeat === false ? 'no' : 'not_sure');
  const modeText = meal.decision.choice_mode === 'random_draw' ? ' · Selected by agreed random draw' : meal.decision.choice_mode === 'tie_break' ? ' · Selected by score tie-break' : meal.decision.choice_mode === 'manual' ? ' · Selected by unanimous agreement' : meal.decision.choice_mode === 'manual_unverified' ? ' · Recorded unverified plan' : '';
  return `<div class="layout-11 card decision-card"><div class="eyebrow">${meal.decision.checked===false?'YOUR OWN PLAN — CHECKS UNRESOLVED':'LESS DECIDING. MORE DINING.'}</div><h2>${esc(option?.name || option?.outlet_name || meal.manual_plan?.name || meal.decision.name || 'Your table has made a choice.')}</h2><p class="muted">${esc(dateLabel(meal.meal_at,{hour:'numeric',minute:'2-digit'}))} · ${esc(meal.location_label)}${modeText}</p><p data-i18n="Confirm%20practical%20details%20directly%20with%20the%20restaurant%20before%20heading%20out.">Confirm practical details directly with the restaurant before heading out.</p></div>${!canFeedback ? '<div class="notice">Feedback opens after the planned meal finishes. Come back then to tell us what actually happened.</div>' : `<form class="card" data-form="feedback"><h2>${meal.my_feedback ? t('Your meal, remembered.') : t('How did it go?')}</h2><p class="muted" data-i18n="All%20feedback%20is%20optional.%20A%20changed%20plan%20is%20not%20a%20dislike.">All feedback is optional. A changed plan is not a dislike.</p><fieldset><legend data-i18n="What%20actually%20happened%3F">What actually happened?</legend>${choices('outcome',['ate_here','somewhere_else','plans_changed','did_not_join','not_yet'],outcome,'radio')}</fieldset><fieldset data-visit-fields ${outcome === 'ate_here' ? '' : 'disabled'}><fieldset><legend data-i18n="How%20was%20the%20food%20for%20you%3F">How was the food for you?</legend>${choices('enjoyment',['enjoyed','okay','did_not_enjoy','skipped'],enjoyment,'radio')}</fieldset><fieldset><legend data-i18n="Would%20you%20choose%20it%20again%3F">Would you choose it again?</legend>${choices('repeat_intent',['yes','another_occasion','no','not_sure'],repeat,'radio')}</fieldset><details class="feedback-details"><summary data-i18n="Add%20a%20little%20more%20detail%20%28optional%29">Add a little more detail (optional)</summary><fieldset><legend data-i18n="What%20influenced%20your%20answer%3F">What influenced your answer?</legend>${choices('influences',['taste','portion','value','travel','queue','service','atmosphere','dietary_information','other'],feedback.influences||[])}</fieldset><div class="form-grid"><div class="field"><label for="cost-expectation" data-i18n="Was%20the%20cost%20as%20expected%3F">Was the cost as expected?</label><select name="cost_expectation" id="cost-expectation">${selectOptions([['','Skip'],['within_estimate','Within estimate'],['higher','Higher'],['lower','Lower'],['not_sure','Not sure']],feedback.cost_expectation||'')}</select></div><div class="field"><label for="actual-cost" data-i18n="What%20did%20you%20spend%3F%20%28RM%2C%20optional%29">What did you spend? (RM, optional)</label><input name="actual_cost" id="actual-cost" type="number" min="0" max="2000" step="0.01" value="${feedback.actual_cost_minor != null ? esc(feedback.actual_cost_minor/100) : ''}"></div></div><div class="field"><label for="dish-text" data-i18n="What%20did%20you%20eat%3F%20%28optional%29">What did you eat? (optional)</label><input name="dish_text" id="dish-text" maxlength="500" value="${esc(feedback.dish_text||'')}"></div></details></fieldset><div class="field" data-alternate-field ${outcome === 'somewhere_else' ? '' : 'style="display:none;"'}><label for="alternate-outlet-name">Where did you eat instead? (optional)</label><input id="alternate-outlet-name" name="alternate_outlet_name" placeholder="Restaurant or place name" value="${esc(feedback.alternate_outlet_name||'')}"><span class="help">Associates this outing with an alternate venue without recording a dislike for the shortlisted option.</span></div><fieldset><legend data-i18n="Did%20choosing%20together%20feel%20fair%3F">Did choosing together feel fair?</legend>${choices('fairness',['yes','somewhat','no','skipped'],feedback.fairness||'skipped','radio')}</fieldset><div class="field"><label for="feedback-comment" data-i18n="Anything%20to%20remember%3F%20%28optional%29">Anything to remember? (optional)</label><textarea id="feedback-comment" name="comment" maxlength="500" placeholder="A detail about this meal, not a permanent preference…">${esc(feedback.comment||'')}</textarea></div>${privacy('Only you can see your enjoyment, fairness and personal feedback. Lasting preference changes need your review.')}${formError()}<button class="layout-18 btn full" type="submit">${meal.my_feedback ? t('Update my feedback') : t('Save my feedback')} ${icon('heart')}</button></form>`}`;
}

function adaptiveQuestionHTML(meal) {
  if(meal.decision||terminalMeal(meal)||!meal.my_response?.ready)return '';
  const question=state.adaptiveQuestion;
  if(!question)return `<section class="card adaptive-question"><div class="eyebrow">OPTIONAL · ONE USEFUL FOLLOW-UP</div><h2>Could one choice sharpen the shortlist?</h2><p class="muted">The server can compare the current verified candidates and ask at most one question only when an answer could change their preliminary order.</p><button class="btn secondary full" data-action="request-adaptive-question">Check for a useful question</button></section>`;
  if(question.status==='loading')return '<section class="card adaptive-question" role="status"><p>Checking whether one useful question exists…</p></section>';
  if(question.status==='available')return `<section class="card adaptive-question" data-adaptive-question><div class="eyebrow">OPTIONAL · M09</div><h2>${esc(question.prompt)}</h2><p class="help">${esc(question.purpose)} No restaurant names or another person’s answers are shown here.</p><div class="adaptive-choices">${list(question.choices).map(choice=>`<button class="btn secondary" data-action="answer-adaptive-question" data-request-id="${esc(question.request_id)}" data-choice="${esc(choice)}">${esc(choice)}</button>`).join('')}</div><button class="text-button" data-action="skip-adaptive-question" data-request-id="${esc(question.request_id)}">Skip this optional question</button></section>`;
  const messages={not_needed:'No useful follow-up is needed for your current answer.',already_answered:'You already answered this meal’s optional follow-up.',already_skipped:'You skipped this meal’s optional follow-up.',already_stale:'The earlier question expired because the meal changed.'};
  return `<section class="notice adaptive-question" role="status">${esc(messages[question.status]||'No optional follow-up is available.')}</section>`;
}

function mealPage() {
  const meal = state.meal; if (!meal) return empty('Meal not available', 'You may no longer have access, or the meal may have changed.');
  const step = meal.decision ? 3 : meal.result ? 2 : 1;
  const deadlinePassed = !meal.decision && !terminalMeal(meal) && meal.status !== 'decision_overdue' && new Date(meal.decision_by).getTime() <= Date.now();
  const deadlineNotice = deadlinePassed ? `<div class="notice warning deadline-notice"><strong>The decision deadline has passed.</strong><p>${meal.organizer_id === user().id ? 'Update the meal times to reopen planning. Everyone will need to confirm their check-in again.' : 'Your meal host needs to update the times before the group can generate new recommendations.'}</p>${meal.organizer_id === user().id ? '<button class="btn secondary small" data-action="edit-meal" data-i18n="Update%20the%20schedule">Update the schedule</button>' : ''}</div>` : '';
  const scheduleNotice = list(meal.schedule_conflicts).map(c => `<div class="notice warning"><strong>Schedule notice:</strong> ${esc(c.message)}.</div>`).join('');
  return `${state.updates ? '<div class="refresh-notice"><span>There are new updates. Your draft answers are still here.</span><button class="text-button" data-action="reload" data-i18n="Refresh">Refresh</button></div>' : ''}<div class="page-heading"><a class="text-button" href="#room/${encodeURIComponent(meal.room_id)}" data-i18n="%E2%86%90%20Back%20to%20the%20table">← Back to the table</a><div class="spread"><div><div class="eyebrow">MAKE ROOM FOR A GOOD MEAL</div><h1>${esc(title(meal.kind))}${language()==='ms' ? ' bersama.' : ', together.'}</h1></div><span class="tag ${meal.decision ? '' : 'orange'}">${esc(title(meal.status))}</span></div><div class="meal-summary"><span>${icon('clock')}${esc(dateLabel(meal.meal_at,{hour:'numeric',minute:'2-digit'}))}</span><span>${icon('location')}${esc(meal.location_label)} · within ${esc(meal.radius_km)} km</span></div></div>${lifecycleNotice(meal)}${deadlineNotice}${scheduleNotice}${inferenceHTML(meal)}${generationHTML(meal)}${manualPlanHTML(meal)}<div class="steps"><div class="flow-step active"><span>1</span>Check in</div><div class="step-line"></div><div class="flow-step ${step>=2?'active':''}"><span>2</span>Find the overlap</div><div class="step-line"></div><div class="flow-step ${step>=3?'active':''}"><span>3</span>Eat & reflect</div></div><div class="two-column ${meal.decision ? 'has-decision' : ''}"><div class="stack">${feedbackHTML(meal)}${checkinForm(meal)}${adaptiveQuestionHTML(meal)}</div><div class="stack">${readiness(meal)}<div class="card"><h3 data-i18n="The%20plan">The plan</h3><p class="layout-6 muted" >Answer by ${esc(dateLabel(meal.answer_by,{hour:'numeric',minute:'2-digit'}))}<br>Decide by ${esc(dateLabel(meal.decision_by,{hour:'numeric',minute:'2-digit'}))}</p><p class="layout-2 muted" >${esc(meal.duration_minutes || 60)} minutes together. The shared meeting area is used for distance checks; travel time is only shown when supported by route evidence.</p>${meal.organizer_id === user().id && !meal.decision && !terminalMeal(meal) ? '<button class="text-button" data-action="edit-meal">Edit time & meeting area '+icon('arrow')+'</button>' : ''}${meal.organizer_id===user().id && !meal.decision && !terminalMeal(meal) ? '<button class="btn secondary full manual-plan-button" data-action="manual-plan">Record our own plan</button>' : ''}${meal.organizer_id === user().id && !terminalMeal(meal) && new Date(meal.meal_at).getTime() > Date.now() ? '<button class="btn danger full cancel-meal-button" data-action="cancel-meal" data-i18n="Cancel%20this%20meal">Cancel this meal</button>' : ''}</div></div></div>${optionsHTML(meal)}`;
}
function inboxPage() {
  return `<div class="page-heading"><div class="eyebrow">A LITTLE NUDGE FROM YOUR PEOPLE</div><h1 data-i18n="Your%20inbox">Your inbox</h1><p data-i18n="Meal%20invitations%2C%20check-ins%20and%20updates%2C%20all%20in%20one%20place.">Meal invitations, check-ins and updates, all in one place.</p></div><div class="layout-10 notice" >Updates appear here while you use the app. Browser push notifications are not enabled in this pilot.</div>${state.notifications.length ? `<section class="layout-20 card" >${state.notifications.map(n => `<article class="notification ${n.read ? '' : 'unread'}"><div class="notification-icon">${icon(n.kind?.includes('meal') ? 'bowl' : 'bell')}</div><div  class="layout-0"><p class="break-word">${esc(n.message)}</p><small>${esc(dateLabel(n.created_at,{hour:'numeric',minute:'2-digit'}))}</small><div class="inline">${n.meal_id ? `<a class="text-button" data-read="${esc(n.id)}" href="#meal/${encodeURIComponent(n.meal_id)}">Open meal ${icon('arrow')}</a>` : n.room_id ? `<a class="text-button" data-read="${esc(n.id)}" href="#room/${encodeURIComponent(n.room_id)}">Open table ${icon('arrow')}</a>` : ''}${!n.read ? `<button class="text-button" data-action="read-notification" data-id="${esc(n.id)}" data-i18n="Mark%20as%20read">Mark as read</button>` : ''}</div></div></article>`).join('')}</section>` : empty('Nothing waiting for you.', 'Your meal invites and table updates will appear here. Time to get your people together.', '<a class="btn secondary" href="#rooms">Go to my tables '+icon('arrow')+'</a>', 'bell')}`;
}
function render() {
  if (!state.session) return;
  captureDraft();
  const focused=document.activeElement;
  const focusId=focused?.id;
  const focusAction=focused?.dataset?.action;
  const focusOption=focused?.dataset?.id;
  const focusChoice=focused?.dataset?.choice;
  const hadFocus=focused && $('#app').contains(focused);
  if (['verify-email','reset-password'].includes(state.route[0])) { $('#app').innerHTML=tokenPage();localizeUI();return; }
  if (!user()) { $('#app').innerHTML = renderAuth(); localizeUI(); return; }
  const page = state.loading ? '<div class="stack"><div class="skeleton"></div><div class="layout-9 skeleton" ></div></div>' : ({home:homePage,rooms:roomsPage,room:roomPage,meal:mealPage,inbox:inboxPage,profile:profilePage}[state.route[0]] || homePage)();
  $('#app').innerHTML = shell(page); localizeUI();
  if(!state.loading) restoreDraft();
  const replacement=focusId ? document.getElementById(focusId) : focusAction ? [...document.querySelectorAll('[data-action]')].find(el=>el.dataset.action===focusAction && el.dataset.id===focusOption && el.dataset.choice===focusChoice) : null;
  if(hadFocus) (replacement && !replacement.disabled ? replacement : $('#main'))?.focus({preventScroll:true});
}

async function loadRoute({quiet = false} = {}) {
  captureDraft();
  state.dirty=false;
  const request = ++state.routeRequest;
  const account=state.accountEpoch;
  void refreshCatalog();
  const nextRoute=(location.hash.slice(1)||'home').split('/').map(value => {try{return decodeURIComponent(value);}catch{return value;}});
  if(nextRoute.join('/')!==state.route.join('/')){state.meal=null;state.room=null;state.adaptiveQuestion=null;state.adaptiveRequest+=1;}
  state.route=nextRoute;
  if (!user()) { if (['room','meal'].includes(state.route[0]) && /^[A-Za-z0-9_-]{1,100}$/.test(state.route[1]||'')) state.pendingRoute=state.route[0]+'/'+encodeURIComponent(state.route[1]); state.loading=false;render();return; }
  state.error = ''; state.updates = false;
  if (!quiet) { state.loading = true; render(); }
  try {
    const route = state.route.slice();
    const roomPromise = ['home','rooms'].includes(route[0]) ? api('/rooms') : null;
    const detailsPromise = route[0] === 'room' ? api(`/rooms/${encodeURIComponent(route[1])}`) : route[0] === 'meal' ? api(`/meals/${encodeURIComponent(route[1])}`) : route[0] === 'profile' ? api('/profile') : null;
    const learningPromise = route[0] === 'profile' ? api('/learning').catch(()=>({unavailable:true})) : null;
    const reminderPromise = route[0] === 'profile' ? api('/notification-settings').catch(()=>null) : null;
    const roomReminderPromise = route[0] === 'room' ? api(`/rooms/${encodeURIComponent(route[1])}/notification-settings`).catch(()=>null) : null;
    const emailPromise=route[0]==='profile'?api('/auth/email/status').catch(()=>null):null;
    const [rooms, details, notifications, learning, reminders, roomReminders,emailStatus] = await Promise.all([roomPromise,detailsPromise,api('/notifications'),learningPromise,reminderPromise,roomReminderPromise,emailPromise]);
    if (request !== state.routeRequest || account!==state.accountEpoch || !user()) return;
    if (rooms) state.rooms = list(rooms.rooms || rooms);
    if (route[0] === 'room') state.room = details?.room || details;
    if (route[0] === 'meal') state.meal = details?.meal || details;
    if (route[0] === 'profile') { state.session.profile = details?.profile || details; state.learning = learning; state.reminderSettings = reminders; state.emailStatus=emailStatus; }
    if (route[0] === 'room') state.roomReminderSettings = roomReminders;
    state.notifications = list(notifications?.notifications || notifications);
  } catch(error) { if(error.code==='stale_context')return; if (request === state.routeRequest && account===state.accountEpoch) state.error = error.message; }
  if (request === state.routeRequest && account===state.accountEpoch) { state.loading = false; state.dirty = false; render(); if(!quiet){$('#main')?.focus({preventScroll:true});announce($('#main h1')?.textContent || 'Page ready');} if(state.route[0]==='meal'&&state.meal)updateGeneration(state.meal); if (state.pendingInvite) { const token = state.pendingInvite; state.pendingInvite = null; showInvite(token); } }
}
function navigate(route) {
  captureDraft();
  state.dirty = false;
  if (location.hash === '#'+route) loadRoute(); else location.hash = route;
}

function modal(titleText, body) {
  if(!$('#modal-root dialog'))state.modalOpener=document.activeElement;
  $('#modal-root').innerHTML = `<dialog class="modal" aria-labelledby="modal-title"><div class="modal-header"><h2 id="modal-title">${esc(t(titleText))}</h2><button class="icon-button" data-action="close-modal" aria-label="Close dialog">${icon('close')}</button></div>${body}</dialog>`;
  const dialog = $('#modal-root dialog');
  dialog.addEventListener('cancel',event=>{event.preventDefault();closeModal();});
  localizeUI(dialog); dialog.showModal();
}
function closeModal() { const dialog = $('#modal-root dialog'); if(!dialog)return; if (dialog.open) dialog.close(); $('#modal-root').replaceChildren();const opener=state.modalOpener;state.modalOpener=null;if(opener?.isConnected)opener.focus({preventScroll:true});else $('#main')?.focus({preventScroll:true}); }
function mealModal(edit = false) {
  const meal = edit ? state.meal : {};
  const tomorrow = new Date(Date.now()+24*60*60*1000);
  const klDate = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kuala_Lumpur',year:'numeric',month:'2-digit',day:'2-digit'}).format(tomorrow);
  const needsNewSchedule = edit && ['meal_at','answer_by','decision_by'].some(field => new Date(meal[field]).getTime() <= Date.now());
  const mealAt = meal.meal_at && !needsNewSchedule ? new Date(meal.meal_at) : new Date(klDate+'T12:30:00+08:00');
  const answerBy = meal.answer_by && !needsNewSchedule ? new Date(meal.answer_by) : new Date(mealAt.getTime()-60*60*1000);
  const decisionBy = meal.decision_by && !needsNewSchedule ? new Date(meal.decision_by) : new Date(mealAt.getTime()-30*60*1000);
  modal(edit ? 'Update the plan' : 'What’s the next meal?', `<p>${edit ? 'Changes will require a fresh check of the group’s recommendations.' : 'Choose a meeting area and give everyone time to check in.'} All times below are Malaysia time (MYT, UTC+8).</p>${needsNewSchedule ? '<div class="notice warning schedule-note">The old schedule has passed. Tomorrow’s times are proposed below for you to review; nothing changes until you save.</div>' : ''}<form data-form="${edit?'edit-meal':'start-meal'}">${!edit ? participantPicker(list(state.room?.members),list(state.room?.members).length<=8 ? list(state.room?.members).map(member=>member.id) : [user().id],user().id) : ''}<div class="field"><label for="meal-kind" data-i18n="Meal">Meal</label><select id="meal-kind" name="kind">${selectOptions([['breakfast','Breakfast'],['lunch','Lunch'],['dinner','Dinner'],['other','Other']],meal.kind || 'lunch')}</select></div><div class="form-grid"><div class="field field-full"><label for="meal-at" data-i18n="When%20are%20we%20eating%3F">When are we eating?</label><input id="meal-at" name="meal_at" type="datetime-local" value="${esc(localInput(mealAt))}" required></div><div class="field"><label for="answer-by" data-i18n="Answer%20by">Answer by</label><input id="answer-by" name="answer_by" type="datetime-local" value="${esc(localInput(answerBy))}" required></div><div class="field"><label for="decision-by" data-i18n="Decide%20by">Decide by</label><input id="decision-by" name="decision_by" type="datetime-local" value="${esc(localInput(decisionBy))}" required></div><div class="field"><label for="duration" data-i18n="Meal%20duration%20%28minutes%29">Meal duration (minutes)</label><input id="duration" name="duration_minutes" type="number" min="15" max="240" value="${esc(meal.duration_minutes || 60)}" required></div><div class="field"><label for="radius" data-i18n="Search%20radius%20%28km%29">Search radius (km)</label><input id="radius" name="radius_km" type="number" min="0.5" max="30" step="0.1" value="${esc(meal.radius_km || 5)}" required></div><div class="field field-full"><label for="location-label" data-i18n="Meeting%20area">Meeting area</label><input id="location-label" name="location_label" value="${esc(meal.location_label || 'Petaling Jaya')}" placeholder="For example: near SS2, Petaling Jaya" required maxlength="120"><span class="help" data-i18n="A%20shared%20meeting%20point%2C%20not%20everyone%E2%80%99s%20home%20address.">A shared meeting point, not everyone’s home address.</span><div class="inline wrap layout-2" style="margin-top:0.25rem;"><span class="help" style="margin-right:0.25rem;">Quick areas:</span><button type="button" class="tag neutral" data-action="pick-neighbourhood" data-label="SS2, Petaling Jaya" data-lat="3.118" data-lon="101.622">SS2</button><button type="button" class="tag neutral" data-action="pick-neighbourhood" data-label="Bangsar, Kuala Lumpur" data-lat="3.130" data-lon="101.670">Bangsar</button><button type="button" class="tag neutral" data-action="pick-neighbourhood" data-label="Bukit Bintang, KL" data-lat="3.147" data-lon="101.710">Bukit Bintang</button><button type="button" class="tag neutral" data-action="pick-neighbourhood" data-label="Subang Jaya" data-lat="3.073" data-lon="101.588">Subang Jaya</button></div></div><div class="field"><label for="latitude" data-i18n="Meeting%20point%20latitude">Meeting point latitude</label><input id="latitude" name="latitude" type="number" min="2.4" max="3.9" step="any" value="${esc(meal.latitude ?? 3.12)}" placeholder="3.118" required></div><div class="field"><label for="longitude" data-i18n="Meeting%20point%20longitude">Meeting point longitude</label><input id="longitude" name="longitude" type="number" min="100.7" max="102.0" step="any" value="${esc(meal.longitude ?? 101.62)}" placeholder="101.622" required></div></div><button class="text-button" type="button" data-action="use-location">${icon('location')} Use my current position as the meeting point</button><p class="layout-2 muted"  data-i18n="Only%20use%20your%20current%20position%20if%20you%20want%20to%20share%20it%20as%20this%20meal%E2%80%99s%20meeting%20point.%20You%20can%20enter%20coordinates%20manually%20instead.">Only use your current position if you want to share it as this meal’s meeting point. You can enter coordinates manually instead.</p>${formError()}<button class="btn full" type="submit">${edit ? 'Save the plan' : 'Invite my table to check in'} ${icon('arrow')}</button></form>`);
}
function confirmModal(heading, text, action, buttonText, data = {}) {
  const attrs = Object.entries(data).map(([key,value]) => ` data-${esc(key)}="${esc(value)}"`).join('');
  modal(heading, `<p>${esc(text)}</p><div class="form-error" role="alert" tabindex="-1"></div><div class="inline"><button class="btn secondary" data-action="close-modal" data-i18n="Cancel">Cancel</button><button class="btn" data-action="${esc(action)}"${attrs}>${esc(t(buttonText))}</button></div>`);
}

async function mutate(button, fn) {
  if (state.pending) return;
  state.pending = true;
  if (button) button.disabled = true;
  try { await fn(); }
  catch(error) {
    if(error.code==='stale_context')return;
    const target = $('#modal-root .form-error');
    if (target) { target.textContent=t(error.message);target.tabIndex=-1;target.focus(); } else toast(error.message);
    if (error.status === 409) { state.updates = true; if ($('#modal-root dialog')) { if (target) target.textContent += ' Close this dialog to refresh before trying again.'; } else if (!state.dirty) await loadRoute({quiet:true}); }
  } finally { state.pending = false; if (button?.isConnected) button.disabled = false; }
}

document.addEventListener('click', async event => {
  const readLink = event.target.closest('[data-read]');
  if (readLink) api(`/notifications/${encodeURIComponent(readLink.dataset.read)}/read`,{method:'POST',body:{}}).catch(() => {});
  const button = event.target.closest('[data-action]'); if (!button) return;
  const action = button.dataset.action;
  if (action === 'boot-reload') { location.reload(); return; }
  if (action === 'language') {
    const inputs=[...document.querySelectorAll('#app input,#app select,#app textarea')].map(input=>({name:input.name,id:input.id,type:input.type,value:input.value,checked:input.checked}));
    const focus=document.activeElement?.id;const dirty=state.dirty;state.uiLanguage=language()==='ms'?'en':'ms';render();
    const remaining=[...document.querySelectorAll('#app input,#app select,#app textarea')];
    for(const saved of inputs){const input=remaining.find(element=>saved.id?element.id===saved.id:element.name===saved.name&&element.type===saved.type&&(['checkbox','radio'].includes(saved.type)?element.value===saved.value:true));if(input){if(['checkbox','radio'].includes(saved.type))input.checked=saved.checked;else input.value=saved.value;remaining.splice(remaining.indexOf(input),1);}}
    const checkin=$('[data-form="checkin"]');if(checkin){const selectedAttendance=$('[name="attendance"]:checked',checkin);$('[data-joining-fields]',checkin).disabled=selectedAttendance?.value!=='join';}
    state.dirty=dirty;if(focus)document.getElementById(focus)?.focus();return;
  }
  if (action === 'auth-switch') { state.authMode = state.authMode === 'register' ? 'login' : 'register'; state.error = ''; render(); return; }
  if (action === 'pilot-terms') { modal('Pilot terms & privacy', '<p>This adult-only pilot helps groups compare meal options using the information available. Restaurant facts can be incomplete or outdated. Confirm important dietary and practical details directly with the restaurant.</p><p>Your account, profile, check-ins and feedback are used to provide the service. Your room sees your name, readiness and shared meal decisions; private profile fields and individual answers are not shared.</p><p>Sensitive food requirements have a separate consent control in your profile. History-based personalisation is optional. You can edit your profile, export your own data, or delete your account in My preferences.</p><p>Do not upload information about another person without their permission. Invites are private access codes; share them only with intended members.</p><button class="btn full" data-action="close-modal" data-i18n="Back%20to%20my%20account">Back to my account</button>'); return; }
  if (action === 'close-modal') { closeModal(); if (state.updates && !state.dirty) await loadRoute({quiet:true}); return; }
  if (action === 'reload') { if (state.dirty) { confirmModal('Reload saved information?', 'Your unsaved edits on this page will be replaced by the latest saved information.', 'confirmed-reload', 'Reload saved answers'); } else await loadRoute(); return; }
  if (action === 'confirmed-reload') { closeModal(); clearDraft();state.dirty = false; await loadRoute(); return; }
  if (action === 'forgot-password') { modal('Reset your password', '<p>Enter your account email. If a reset can be sent, its link lets you choose a new password.</p><form data-form="request-password-reset"><div class="field"><label for="reset-email">Email address</label><input id="reset-email" name="email" type="email" autocomplete="email" required></div>'+formError()+'<button class="btn full" type="submit">Request reset link</button></form>'); return; }
  if (action === 'create-room') { modal('Give your table a name', `<p data-i18n="Make%20a%20little%20space%20for%20your%20favourite%20people.">Make a little space for your favourite people.</p><form data-form="create-room"><div class="field"><label for="room-name" data-i18n="Table%20name">Table name</label><input id="room-name" name="name" placeholder="The lunch crew" maxlength="80" required autofocus></div>${formError()}<button class="btn full" type="submit">Create our table ${icon('arrow')}</button></form>`); return; }
  if (action === 'join-room') { modal('There’s a place for you', `<p data-i18n="Paste%20the%20invite%20code%20your%20table%20host%20shared%20with%20you.">Paste the invite code your table host shared with you.</p><form data-form="join-room"><div class="field"><label for="invite-token" data-i18n="Invite%20code">Invite code</label><div class="inline"><input id="invite-token" name="token" autocomplete="off" required placeholder="Paste your invite code"><button type="button" class="btn secondary small" data-action="preview-invite">Preview</button></div></div><div id="invite-preview-target"></div>${formError()}<button class="btn full" type="submit">Join the table ${icon('arrow')}</button></form>`); return; }
  if (action === 'preview-invite') {
    const input = $('#invite-token');
    const token = input?.value.trim();
    if (!token) return;
    try {
      const data = await api(`/rooms/preview?token=${encodeURIComponent(token)}`);
      const target = $('#invite-preview-target');
      if (target) {
        target.innerHTML = `<div class="notice" style="margin:0.5rem 0;"><strong>${esc(data.name)}</strong><p class="muted">Hosted by ${esc(data.owner_name || 'Table host')} · ${data.member_count} member${data.member_count === 1 ? '' : 's'}</p></div>`;
      }
    } catch (err) {
      toast(err.message || 'Could not load preview for this invite code.');
    }
    return;
  }
  if (action === 'start-meal') { mealModal(false); return; }
  if (action === 'edit-meal') { mealModal(true); return; }
  if (action === 'use-usual') {
    const form = button.closest('form'); const p = profile();
    for (const input of form.querySelectorAll('[name="cuisines"]')) input.checked = list(p.cuisines).includes(input.value);
    $('[name="spice"]',form).value = p.spice || 'any';
    if (p.max_budget != null) $('[name="budget"]',form).value = p.max_budget;
    state.dirty = true; toast('Usual preferences filled in. Review today’s answers before submitting.'); return;
  }
  if (action === 'interpret-preferences') {
    const form=button.closest('form');const text=$('[name="craving"]',form)?.value.trim();
    if(!text){toast('Describe your craving first.');return;}
    modal('Review what is sent to AI', `<p>Only the craving text shown below is sent to the configured model. Your profile, allergies, contact details, room, location and restaurant data are not sent.</p><blockquote class="preference-consent-text">${esc(text)}</blockquote><p class="help">Do not continue if this text contains a food allergy, health detail, contact detail, password or other secret. You can use the ordinary choices without AI.</p><div class="form-error" role="alert" tabindex="-1"></div><div class="inline confirmation-actions"><button class="btn secondary" data-action="close-modal">Cancel</button><button class="btn" data-action="confirm-preference-interpret">Send this text to AI</button></div>`);return;
  }
  if (action === 'confirm-preference-interpret') {
    const form=$('#main form[data-form="checkin"]');const text=$('[name="craving"]',form)?.value.trim();
    if(!form||!text){closeModal();return;}
    const sequence=++state.preferenceRequest;const epoch=form.dataset.editEpoch||'0';const context=requestContext();
    closeModal();state.preferenceDraft={status:'loading'};updatePreferenceDraft(form);
    button.disabled=true;
    try {
      const data=await api(`/meals/${encodeURIComponent(state.meal.id)}/interpret-preferences`,{method:'POST',body:{text,expected_revision:state.meal.revision,expected_response_revision:Number(form.dataset.responseRevision)||0,allow_model_processing:true}});
      if(sequence!==state.preferenceRequest||!isCurrent(context)||!form.isConnected||form.dataset.editEpoch!==epoch||$('[name="craving"]',form)?.value.trim()!==text)return;
      state.preferenceDraft=data;updatePreferenceDraft(form);
    } catch(error) {
      if(error.code==='stale_context'||sequence!==state.preferenceRequest||!form.isConnected)return;
      state.preferenceDraft={status:'unavailable'};updatePreferenceDraft(form);toast(error.message);
    }
    return;
  }
  if (action === 'apply-preference-draft') {
    const form=button.closest('form');const suggestions=state.preferenceDraft?.suggestions||{};
    const setChecks=(name,values)=>{for(const input of form.querySelectorAll(`[name="${name}"]`))input.checked=list(values).includes(input.value);};
    setChecks('cuisines',suggestions.cuisines);setChecks('dish_families',suggestions.dish_families);setChecks('flavour_tags',suggestions.flavour_tags);setChecks('occasion_features',suggestions.occasion_features);
    if(suggestions.appetite){const input=form.querySelector(`[name="appetite"][value="${CSS.escape(suggestions.appetite)}"]`);if(input)input.checked=true;}
    if(suggestions.spice)$('[name="spice"]',form).value=suggestions.spice;
    if(suggestions.soft_budget_target!=null)$('[name="soft_budget_target"]',form).value=suggestions.soft_budget_target;
    if(suggestions.novelty)$('[name="novelty"]',form).value=suggestions.novelty;
    const details=$('.feedback-details',form);if(details&&(list(suggestions.dish_families).length||list(suggestions.flavour_tags).length||list(suggestions.occasion_features).length))details.open=true;
    $('[name="taste_input_mode"]',form).value='structured';
    $('[name="requirements_confirmed"]',form).checked=false;$('[name="ready"]',form).checked=false;$('[name="budget"]',form).required=false;
    state.preferenceRequest+=1;state.preferenceDraft=null;form.dataset.editEpoch=String(Number(form.dataset.editEpoch||0)+1);updatePreferenceDraft(form);
    const region=$('[data-preference-region]',form);if(region)region.innerHTML='<div class="notice preference-applied" role="status">Draft applied. Review every choice and submit your check-in to confirm it.</div>';
    state.dirty=true;return;
  }
  if (action === 'request-adaptive-question') {
    if(state.dirty){toast('Save or discard your current check-in edits first.');return;}
    const sequence=++state.adaptiveRequest;const context=requestContext();
    state.adaptiveQuestion={status:'loading'};render();
    try{
      const data=await api(`/meals/${encodeURIComponent(state.meal.id)}/adaptive-question`,{method:'POST',body:{expected_revision:state.meal.revision,expected_response_revision:state.meal.my_response_revision}});
      if(sequence!==state.adaptiveRequest||!isCurrent(context))return;
      state.adaptiveQuestion=data;render();
    }catch(error){
      if(error.code==='stale_context'||sequence!==state.adaptiveRequest)return;
      state.adaptiveQuestion=null;render();toast(error.message);
    }
    return;
  }
  if (action === 'answer-adaptive-question' || action === 'skip-adaptive-question') {
    if(state.dirty){toast('Save or discard your current check-in edits first.');return;}
    await mutate(button,async()=>{
      const skipped=action==='skip-adaptive-question';
      const data=await api(`/meals/${encodeURIComponent(state.meal.id)}/adaptive-question/${encodeURIComponent(button.dataset.requestId)}`,{method:'POST',body:{choice:skipped?null:button.dataset.choice,skip:skipped}});
      state.meal=data.meal||data;state.adaptiveQuestion={status:skipped?'already_skipped':'already_answered'};render();toast(skipped?'Optional question skipped. Your saved answer is unchanged.':'Your choice is saved. The group context will use the updated answer.');
    });return;
  }
  if (action === 'choose-vote') {
    const prior = state.meal.my_votes?.[button.dataset.id];
    modal('Your private response', `<p data-i18n="%E2%80%9CCannot%20eat%20here%E2%80%9D%20blocks%20this%20option%20for%20your%20participation.%20Only%20you%20can%20change%20your%20response.%20Your%20reason%20is%20not%20shown%20to%20the%20table.">“Cannot eat here” blocks this option for your participation. Only you can change your response. Your reason is not shown to the table.</p><form data-form="vote" data-option="${esc(button.dataset.id)}" data-revision="${state.meal.revision}"><fieldset><legend data-i18n="How%20does%20this%20option%20work%20for%20you%3F">How does this option work for you?</legend>${choices('choice',['works','prefer_another','cannot_eat'],button.dataset.choice,'radio')}</fieldset><div class="field"><label for="vote-reason" data-i18n="Private%20reason%20%28optional%29">Private reason (optional)</label><textarea id="vote-reason" name="reason" maxlength="500">${esc(prior?.reason || '')}</textarea></div>${formError()}<button class="btn full" type="submit" data-i18n="Save%20my%20response">Save my response</button></form>`); return;
  }
  if (action === 'delegation' && button.dataset.enabled === 'true') { confirmModal('Let the group choose from this shortlist?', 'This permission applies only to the current listed options that meet your requirements. Explicit negative responses still block those options. You can revoke permission before selection.', 'confirm-delegation','Allow this shortlist',{revision:button.dataset.revision}); return; }
  if (action === 'delete-observation') { confirmModal('Remove this learned observation?', 'It will no longer be used for personalisation. Your original meal feedback remains available.', 'confirm-delete-observation','Remove observation',{id:button.dataset.id}); return; }
  if (action === 'clear-learning') { confirmModal('Clear your learned history?', 'Remove all learned observations and preference proposals. Your explicit profile requirements and original feedback are kept.', 'confirm-clear-learning','Clear learned history'); return; }
  if (action === 'use-latest-response') { const latest=state.responseConflict; closeModal(); state.responseConflict=null; state.meal=latest; clearDraft();state.dirty=false; render(); return; }
  if (action === 'keep-response-draft') {
    const latest=state.responseConflict; const form=$('[data-form="checkin"]');
    if (form && latest) { form.dataset.responseRevision=String(latest.my_response_revision||0); state.meal=latest; $('[name="requirements_confirmed"]',form).checked=false; $('[name="ready"]',form).checked=false; $('[data-response-conflict]',form).textContent='Your draft is kept. Recheck your requirements and submit again when ready.'; state.dirty=true; }
    state.responseConflict=null; closeModal(); return;
  }
  if (action === 'manage-member') {
    modal(`Manage ${button.dataset.name}`, `<p>Membership changes can invalidate active meal recommendations.</p><div class="form-error" role="alert" tabindex="-1"></div><div class="stack"><button class="btn secondary" data-action="transfer-owner" data-id="${esc(button.dataset.id)}" data-name="${esc(button.dataset.name)}">Make ${esc(button.dataset.name)} the table host</button><button class="btn danger" data-action="remove-member" data-id="${esc(button.dataset.id)}" data-name="${esc(button.dataset.name)}">Remove from this table</button></div>`); return;
  }
  if (action === 'transfer-owner') { confirmModal('Transfer table ownership?', `${button.dataset.name} will manage this table and its membership.`, 'confirmed-transfer','Transfer ownership',{id:button.dataset.id}); return; }
  if (action === 'remove-member') { confirmModal('Remove this member?', `${button.dataset.name} will lose access to the table. Active recommendations may need to be regenerated.`, 'confirmed-remove','Remove member',{id:button.dataset.id}); return; }
  if (action === 'archive-room') { confirmModal('Archive this table?', 'The table will stop accepting new meals and invites.', 'confirmed-archive','Archive table'); return; }
  if (action === 'leave-room') { confirmModal('Leave this table?', 'You will lose access to its shared meals. Active meal recommendations may need to be regenerated.', 'confirmed-leave','Leave table'); return; }
  if (action === 'manual-plan') { manualPlanModal();return; }
  if (action === 'manual-select') { confirmModal('Record this unverified choice?', 'Every included person acknowledged the unresolved checks. This records your own plan; it does not certify restaurant suitability.', 'confirm-manual-select','Record our plan',{revision:button.dataset.revision});return; }
  if (action === 'cancel-meal') { confirmModal('Cancel this meal?', 'Everyone at this meal will be notified. The shared shortlist, votes and any selected option will be cleared. You can plan a new meal from your table.', 'confirmed-cancel', 'Cancel this meal', {revision:state.meal.revision}); return; }
  if (action === 'select') { confirmModal('Choose this meal?', `Confirm ${button.dataset.name} as this table’s choice. The server will check current votes, eligibility and meal revision before accepting.`, 'confirmed-select','Confirm this choice',{id:button.dataset.id,revision:state.meal.revision}); return; }
  if (action === 'tiebreak-select') { confirmModal('Break tie by score?', 'Select among unanimously approved options using the PRD tie-breaking rules (highest fit score, shortest travel, greatest evidence coverage).', 'confirmed-tiebreak', 'Confirm tie-break selection', {revision:button.dataset.revision}); return; }
  if (action === 'random-draw-select') { confirmModal(t('Agreed random draw?'), t('Randomize fairly among all options that everyone has approved.'), 'confirmed-random-draw', t('Conduct random draw'), {revision:button.dataset.revision}); return; }
  if (action === 'report-data-error') { modal('Report data or dietary issue', `<p data-i18n="Report%20incorrect%20dietary%2C%20halal%2C%20price%2C%20or%20hours%20information%20for%20this%20outlet.%20This%20triggers%20operator%20investigation%20and%20is%20separate%20from%20personal%20taste%20feedback.">Report incorrect dietary, halal, price, or hours information for this outlet. This triggers operator investigation and is separate from personal taste feedback.</p><form data-form="report-data-error" data-outlet-id="${esc(button.dataset.outletId || '')}"><div class="field"><label for="report-category" data-i18n="Issue%20category">Issue category</label><select id="report-category" name="category" required>${selectOptions([['dietary_claim',t('Dietary claim (e.g. vegetarian / allergen)')],['halal_status',t('Halal status or certification')],['price_error',t('Price or menu item error')],['hours_closure',t('Operating hours or closure')],['other',t('Other factual discrepancy')]],'dietary_claim')}</select></div><div class="field"><label for="report-description" data-i18n="Description%20of%20discrepancy">Description of discrepancy</label><textarea id="report-description" name="description" required minlength="5" maxlength="1000" placeholder="${esc(t('Please specify the error, dish, or dietary discrepancy you noticed...'))}"></textarea></div>${formError()}<button class="btn full" type="submit" data-i18n="Submit%20report%20for%20investigation">Submit report for investigation</button></form>`); return; }
  if (action === 'delete-account') { modal('Delete your account?', `<p data-i18n="This%20removes%20your%20personal%20account%20data.%20Shared%20history%20may%20be%20retained%20without%20your%20identity%20where%20needed.%20Tables%20you%20host%20will%20be%20archived.%20Transfer%20ownership%20first%20if%20you%20want%20those%20tables%20to%20remain%20active.">This removes your personal account data. Shared history may be retained without your identity where needed. Tables you host will be archived. Transfer ownership first if you want those tables to remain active.</p><form data-form="delete-account"><div class="field"><label for="delete-confirm" data-i18n="Type%20DELETE%20to%20confirm">Type DELETE to confirm</label><input id="delete-confirm" name="confirmation" autocomplete="off" pattern="DELETE" required></div>${formError()}<button class="btn danger full" type="submit" data-i18n="Delete%20my%20account">Delete my account</button></form>`); return; }
  if (action === 'pick-neighbourhood') {
    const form = button.closest('form');
    if (form) {
      if (button.dataset.label) $('[name="location_label"]', form).value = button.dataset.label;
      if (button.dataset.lat) $('[name="latitude"]', form).value = button.dataset.lat;
      if (button.dataset.lon) $('[name="longitude"]', form).value = button.dataset.lon;
    }
    return;
  }
  if (action === 'use-location') {
    if (!navigator.geolocation) { toast('This browser does not support location access. Enter a meeting point manually.'); return; }
    button.disabled = true;const context=requestContext();
    navigator.geolocation.getCurrentPosition(position => { if(!isCurrent(context)||!button.isConnected)return;const form = button.closest('form'); $('[name="latitude"]',form).value = position.coords.latitude.toFixed(6); $('[name="longitude"]',form).value = position.coords.longitude.toFixed(6); button.disabled = false; toast('Position added. Give this meeting area a name before saving.'); }, () => { if(!isCurrent(context)||!button.isConnected)return;button.disabled = false; toast('Location was unavailable. You can enter the meeting coordinates manually.'); }, {enableHighAccuracy:false,timeout:10000,maximumAge:0}); return;
  }
  await mutate(button, async () => {
    if (action === 'request-verification') { await api('/auth/email/request-verification',{method:'POST',body:{}});toast('Check your email for the verification link.'); }
    if (action === 'confirm-manual-select') { await api(`/meals/${encodeURIComponent(state.meal.id)}/manual-select`,{method:'POST',body:{expected_revision:Number(button.dataset.revision)}});closeModal();await loadRoute({quiet:true});toast('Your own plan is recorded. Restaurant checks remain unresolved.'); }
    if (action === 'revoke-manual-ack') { await api(`/meals/${encodeURIComponent(state.meal.id)}/manual-ack`,{method:'POST',body:{expected_revision:state.meal.revision,acknowledge_unverified:false}});await loadRoute({quiet:true});toast('Your acknowledgment is revoked.'); }
    if (action === 'participants') await participantModal();
    if (action === 'review-response-conflict') {
      const response=await api(`/meals/${encodeURIComponent(state.meal.id)}`); const latest=response.meal||response; state.responseConflict=latest;
      const answer=latest.my_response||{};
      modal('Another answer was saved', `<p>Your draft is still on the page. Review the latest saved answer before choosing which to keep.</p><dl class="saved-response"><dt>Attendance</dt><dd>${esc(labels[answer.attendance]||'Decide later')}</dd><dt>Craving</dt><dd>${esc(answer.craving||'No specific craving')}</dd><dt>Cuisines</dt><dd>${esc(list(answer.cuisines).join(', ')||'No specific cuisine')}</dd><dt>Budget</dt><dd>${answer.budget!=null ? esc('RM '+answer.budget) : 'Not set'}</dd></dl><div class="inline"><button class="btn secondary" data-action="use-latest-response" data-i18n="Use%20the%20saved%20answer">Use the saved answer</button>${!terminalMeal(latest) && !latest.decision ? '<button class="btn" data-action="keep-response-draft" data-i18n="Keep%20my%20draft%20for%20review">Keep my draft for review</button>' : ''}</div>`);
    }
    if (action === 'delegation' || action === 'confirm-delegation') { await api(`/meals/${encodeURIComponent(state.meal.id)}/delegation`,{method:'POST',body:{expected_revision:Number(button.dataset.revision),enabled:action==='confirm-delegation'}});closeModal();await loadRoute({quiet:true});toast(action==='confirm-delegation'?'Permission saved for this shortlist.':'Your group-choice permission is revoked.'); }
    if (action === 'learning-proposal') { await api(`/learning/proposals/${encodeURIComponent(button.dataset.id)}`,{method:'POST',body:{decision:button.dataset.decision}});await loadRoute({quiet:true});toast(button.dataset.decision==='accept'?'Your preference was confirmed.':'The proposal was declined.'); }
    if (action === 'confirm-delete-observation') { await api(`/learning/observations/${encodeURIComponent(button.dataset.id)}`,{method:'DELETE'});closeModal();await loadRoute({quiet:true});toast('Observation removed from learning.'); }
    if (action === 'confirm-clear-learning') { await api('/learning/clear',{method:'POST',body:{}});closeModal();await loadRoute({quiet:true});toast('Learned history cleared.'); }
    if (action === 'room-reminders') { await api(`/rooms/${encodeURIComponent(state.room.id)}/notification-settings`,{method:'PATCH',body:{muted:button.dataset.muted==='true'}});await loadRoute({quiet:true});toast('Table reminder preference saved.'); }

    if (action === 'logout') { await api('/auth/logout',{method:'POST',body:{}}); state.pendingRoute=null;setSession({user:null,profile:null,csrf_token:null,demo_mode:state.session?.demo_mode},true);state.authMode='login';navigate('home');render(); }
    if (action === 'invite') { const data = await api(`/rooms/${encodeURIComponent(state.room.id)}/invite`,{method:'POST',body:{}}); showInvite(data.invite_token); }
    if (action === 'copy-invite') { await navigator.clipboard.writeText(button.dataset.token); toast('Invite code copied. Share it with your people.'); }
    if (action === 'confirmed-transfer') { await api(`/rooms/${encodeURIComponent(state.room.id)}/transfer`,{method:'POST',body:{user_id:button.dataset.id}}); closeModal(); await loadRoute({quiet:true}); toast('Table host updated.'); }
    if (action === 'confirmed-remove') { await api(`/rooms/${encodeURIComponent(state.room.id)}/members/${encodeURIComponent(button.dataset.id)}`,{method:'DELETE'}); closeModal(); await loadRoute({quiet:true}); toast('Member removed.'); }
    if (action === 'confirmed-archive') { await api(`/rooms/${encodeURIComponent(state.room.id)}/archive`,{method:'POST',body:{}}); closeModal(); await loadRoute({quiet:true}); toast('Table archived.'); }
    if (action === 'confirmed-leave') { await api(`/rooms/${encodeURIComponent(state.room.id)}/leave`,{method:'POST',body:{}}); closeModal(); navigate('rooms'); toast('You have left the table.'); }
    if (action === 'read-notification') { await api(`/notifications/${encodeURIComponent(button.dataset.id)}/read`,{method:'POST',body:{}}); await loadRoute({quiet:true}); }
    if (action === 'generate' || action === 'confirmed-generate' || action === 'retry-generation') {
      const pending = list(state.meal.participants).filter(p => p.attendance === 'pending');
      if (action === 'generate' && pending.length) { modal('Review unanswered invites', `<p>These people have not joined this meal: <strong>${esc(pending.map(p=>p.name).join(', '))}</strong>.</p><p data-i18n="Only%20the%20joined%2C%20ready%20group%20will%20be%20included.%20If%20someone%20joins%20later%2C%20everyone%20must%20review%20a%20fresh%20shortlist.">Only the joined, ready group will be included. If someone joins later, everyone must review a fresh shortlist.</p><label class="check-label"><input type="checkbox" id="exclude-pending-confirm"><span data-i18n="I%20confirm%20that%20these%20unanswered%20invitees%20are%20excluded%20from%20this%20recommendation.">I confirm that these unanswered invitees are excluded from this recommendation.</span></label><div class="form-error" role="alert" tabindex="-1"></div><div class="inline confirmation-actions"><button class="btn secondary" data-action="close-modal" data-i18n="Wait%20for%20them">Wait for them</button><button class="btn" data-action="confirmed-generate" data-revision="${state.meal.revision}" data-i18n="Continue%20with%20joined%20members">Continue with joined members</button></div>`); return; }
      if (action === 'confirmed-generate' && !$('#exclude-pending-confirm')?.checked) throw new Error('Confirm the unanswered invitees’ exclusion, or choose to wait for them.');
      const data = await api(`/meals/${encodeURIComponent(state.meal.id)}/generate`,{method:'POST',body:{expected_revision:action==='confirmed-generate' ? Number(button.dataset.revision) : state.meal.revision,exclude_pending:action==='confirmed-generate' || action==='retry-generation',...(action==='retry-generation'?{participant_ids:state.meal.frozen_participant_ids}:{})}}); closeModal(); captureDraft();state.meal = data.meal || data; render();updateGeneration(state.meal);toast(state.meal.generation_job ? generationState(state.meal.generation_job)[0] : generationMessage(state.meal.result));
    }
    if (action === 'vote') { await api(`/meals/${encodeURIComponent(state.meal.id)}/votes`,{method:'POST',body:{option_id:button.dataset.id,expected_revision:state.meal.revision,approve:button.dataset.approve==='true'}}); await loadRoute({quiet:true}); toast('Your vote is saved.'); }
    if (action === 'confirmed-cancel') { await api(`/meals/${encodeURIComponent(state.meal.id)}/cancel`,{method:'POST',body:{expected_revision:Number(button.dataset.revision)}}); closeModal(); state.dirty = false; await loadRoute({quiet:true}); toast('This meal has been cancelled.'); }
    if (action === 'confirmed-select') { await api(`/meals/${encodeURIComponent(state.meal.id)}/select`,{method:'POST',body:{option_id:button.dataset.id,expected_revision:Number(button.dataset.revision)}}); closeModal(); await loadRoute({quiet:true}); toast('A meal together, decided.'); }
    if (action === 'confirmed-tiebreak') { await api(`/meals/${encodeURIComponent(state.meal.id)}/select`,{method:'POST',body:{expected_revision:Number(button.dataset.revision),choice_mode:'tie_break'}}); closeModal(); await loadRoute({quiet:true}); toast('A meal together, decided by score tie-break.'); }
    if (action === 'confirmed-random-draw') { await api(`/meals/${encodeURIComponent(state.meal.id)}/select`,{method:'POST',body:{expected_revision:Number(button.dataset.revision),choice_mode:'random_draw'}}); closeModal(); await loadRoute({quiet:true}); toast('A meal together, decided by agreed random draw.'); }
    if (action === 'export') { const data = await api('/export'); const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})); const link = document.createElement('a'); link.href = url; link.download = 'makan-together-my-data.json'; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); toast('Your personal data export is ready.'); }
  });
});
function showInvite(token) {
  modal('A seat for your people', `<p>Share this invite code privately with the people you want at your table.</p><div class="invite-code">${esc(token)}</div><p class="layout-4 muted" >They can create an account, choose “Join a table”, and paste this code.</p><div class="form-error" role="alert" tabindex="-1"></div><button class="btn full" data-action="copy-invite" data-token="${esc(token)}">${icon('link')} Copy invite code</button>`);
}

document.addEventListener('submit', async event => {
  const form = event.target.closest('form[data-form]'); if (!form) return;
  event.preventDefault(); if (state.pending || !form.reportValidity()) return;
  const type = form.dataset.form; const fd = new FormData(form); const value = name => String(fd.get(name) || '');
  const submit = $('[type="submit"]',form); const errorElement = $('.form-error',form);
  errorElement.textContent = ''; state.pending = true; submit.disabled = true;form.setAttribute('aria-busy','true');
  try {
    if (type === 'request-password-reset') { await api('/auth/password/request-reset',{method:'POST',body:{email:value('email').trim()}});closeModal();toast('If this account is eligible, check its inbox for a reset link.');
    } else if (type === 'verify-email' || type === 'reset-password') {
      const token=state.route[1];if(!token)throw new Error('This link is missing its token. Request a new link.');
      await api(type==='verify-email'?'/auth/email/verify':'/auth/password/reset',{method:'POST',body:type==='verify-email'?{token}:{token,password:value('password')}});
      history.replaceState(null,'',location.pathname+location.search+'#home');state.route=['home'];state.authMode='login';setSession(await api('/session'),type==='reset-password');await loadRoute();toast(type==='verify-email'?'Your email is verified.':'Your password was reset. Sign in with your new password.');
    } else if (type === 'auth') {
      const body = {email:value('email').trim(),password:value('password')};
      if (state.authMode === 'register') { body.name = value('name').trim(); body.adult_confirmed = fd.has('adult_confirmed'); body.terms_accepted = fd.has('terms_accepted'); }
      setSession(await api(`/auth/${state.authMode}`,{method:'POST',body}),true);
      state.dirty=false; const destination=profile().requirements_reviewed?(state.pendingRoute||'home'):'profile'; if(destination!=='profile')state.pendingRoute=null;navigate(destination);render();
    } else if (type === 'profile') {
      const body = {allergy_status:value('allergy_status'),allergens:value('allergy_status')==='declared' ? csv(value('allergens')) : [],dietary_requirements:csv(value('dietary_requirements')),halal_policy:value('halal_policy'),cuisines:fd.getAll('cuisines'),spice:value('spice'),memory_enabled:fd.has('memory_enabled'),requirements_reviewed:fd.has('requirements_reviewed'),sensitive_data_consent:fd.has('sensitive_data_consent'),max_budget:value('max_budget') ? Number(value('max_budget')) : null,mobility_mode:value('mobility_mode'),language:value('language')};
      if (form.dataset.profileRevision != null) body.expected_profile_revision=Number(form.dataset.profileRevision);
      const data = await api('/profile',{method:'PATCH',body}); state.session.profile = data.profile || data; state.uiLanguage=state.session.profile.language; if (state.reminderSettings) state.reminderSettings = await api('/notification-settings',{method:'PATCH',body:{reminders_enabled:fd.has('reminders_enabled')}}); clearDraft();state.dirty = false; toast('Your preferences are saved.'); const destination=state.pendingRoute||'home';state.pendingRoute=null;navigate(destination);
    } else if (type === 'create-room') {
      const data = await api('/rooms',{method:'POST',body:{name:value('name').trim()}}); closeModal(); state.pendingInvite = data.invite_token; navigate(`room/${encodeURIComponent(data.room?.id || data.id)}`);
    } else if (type === 'join-room') {
      const data = await api('/rooms/join',{method:'POST',body:{token:value('token').trim()}}); closeModal(); navigate(`room/${encodeURIComponent(data.room?.id || data.id)}`); toast('Welcome to the table.');
    } else if (type === 'start-meal' || type === 'edit-meal') {
      const body = {kind:value('kind'),meal_at:new Date(value('meal_at')+'+08:00').toISOString(),answer_by:new Date(value('answer_by')+'+08:00').toISOString(),decision_by:new Date(value('decision_by')+'+08:00').toISOString(),duration_minutes:Number(value('duration_minutes')),location_label:value('location_label').trim(),latitude:Number(value('latitude')),longitude:Number(value('longitude')),radius_km:Number(value('radius_km'))};
      if (new Date(body.answer_by) >= new Date(body.decision_by) || new Date(body.decision_by) >= new Date(body.meal_at)) throw new Error('The answer deadline must come before the decision deadline, which must come before the meal.');
      if (type === 'start-meal') { body.idempotency_key = form.dataset.requestKey ||= crypto.randomUUID(); body.participant_ids = [...new Set(fd.getAll('participant_ids'))]; if (body.participant_ids.length < 2 || body.participant_ids.length > 8) throw new Error('Choose 2–8 people, including yourself.'); }
      if (type === 'edit-meal') body.expected_revision = state.meal.revision;
      const data = await api(type === 'start-meal' ? `/rooms/${encodeURIComponent(state.room.id)}/meals` : `/meals/${encodeURIComponent(state.meal.id)}`,{method:type === 'start-meal' ? 'POST' : 'PATCH',body}); closeModal(); state.dirty = false; navigate(`meal/${encodeURIComponent(data.meal?.id || data.id)}`); toast(type==='start-meal' ? 'The meal is open for check-ins.' : 'The meal plan is updated.');
    } else if (type === 'manual-plan') {
      await api(`/meals/${encodeURIComponent(state.meal.id)}/manual-plan`,{method:'POST',body:{expected_revision:Number(form.dataset.revision),name:value('name').trim(),address:value('address').trim(),note:value('note').trim(),exclude_pending:fd.has('exclude_pending')}});closeModal();state.dirty=false;await loadRoute({quiet:true});toast('Each included person must acknowledge the unverified plan.');
    } else if (type === 'manual-ack') {
      await api(`/meals/${encodeURIComponent(state.meal.id)}/manual-ack`,{method:'POST',body:{expected_revision:Number(form.dataset.revision),acknowledge_unverified:fd.has('acknowledge_unverified')}});state.dirty=false;await loadRoute({quiet:true});toast('Your acknowledgment is saved.');
    } else if (type === 'participants') {
      const ids=[...new Set(fd.getAll('participant_ids'))]; if(ids.length<2||ids.length>8)throw new Error('Choose 2–8 current members, including the meal host.');
      await api(`/meals/${encodeURIComponent(state.meal.id)}/participants`,{method:'POST',body:{participant_ids:ids,expected_revision:Number(form.dataset.revision)}});closeModal();state.dirty=false;await loadRoute({quiet:true});toast('Participants updated. Review a new shortlist together.');
    } else if (type === 'vote') {
      await api(`/meals/${encodeURIComponent(state.meal.id)}/votes`,{method:'POST',body:{option_id:form.dataset.option,expected_revision:Number(form.dataset.revision),choice:value('choice'),reason:value('reason').trim()}});closeModal();await loadRoute({quiet:true});toast('Your private response is saved.');
    } else if (type === 'checkin') {
      const attendance = value('attendance');
      const body = attendance !== 'join' ? {attendance} : {attendance:'join',taste_input_mode:value('taste_input_mode')||'legacy_text',cuisines:fd.getAll('cuisines'),craving:value('craving').trim(),appetite:value('appetite'),spice:value('spice'),budget:value('budget')?Number(value('budget')):null,soft_budget_target:value('soft_budget_target')?Number(value('soft_budget_target')):null,comfortable_travel_minutes:value('comfortable_travel_minutes')?Number(value('comfortable_travel_minutes')):null,occasion_features:fd.getAll('occasion_features'),dish_families:fd.getAll('dish_families'),flavour_tags:fd.getAll('flavour_tags'),novelty:value('novelty')||'any',avoid:csv(value('avoid')),must_leave_by:value('must_leave_by')?value('must_leave_by').trim():null,ready:fd.has('ready'),requirements_confirmed:fd.has('requirements_confirmed')};
      body.expected_response_revision=Number(form.dataset.responseRevision)||0;
      await api(`/meals/${encodeURIComponent(state.meal.id)}/response`,{method:'PUT',body}); clearDraft();state.adaptiveQuestion=null;state.adaptiveRequest+=1;state.dirty = false; await loadRoute({quiet:true}); toast(attendance==='decline' ? 'The table knows you’re sitting this one out.' : attendance==='pending' ? 'You can decide later. You are not included until you join and check in.' : t('Your private check-in is saved.'));
    } else if (type === 'feedback') {
      const visited=value('outcome')==='ate_here';
      await api(`/meals/${encodeURIComponent(state.meal.id)}/feedback`,{method:'POST',body:{visited,outcome:value('outcome'),option_id:state.meal.decision.option_id,enjoyment:visited?value('enjoyment'):null,repeat_intent:visited?value('repeat_intent'):null,would_repeat:visited && ['yes','no'].includes(value('repeat_intent')) ? value('repeat_intent')==='yes' : null,influences:visited?fd.getAll('influences'):[],cost_expectation:visited?(value('cost_expectation')||null):null,actual_cost_minor:visited&&value('actual_cost')!==''?Math.round(Number(value('actual_cost'))*100):null,dish_text:visited?value('dish_text').trim():'',fairness:value('fairness')||null,comment:value('comment').trim(),alternate_outlet_name:value('alternate_outlet_name')?value('alternate_outlet_name').trim():null}}); clearDraft();state.dirty = false; await loadRoute({quiet:true}); toast('Thanks. Your feedback is saved.');
    } else if (type === 'report-data-error') {
      await api(`/meals/${encodeURIComponent(state.meal.id)}/report-data-error`,{method:'POST',body:{outlet_id:form.dataset.outletId,category:value('category'),description:value('description').trim()}});
      closeModal();
      toast('Report logged for operator investigation. It will not affect your taste preferences.');
    } else if (type === 'delete-account') {
      if (value('confirmation') !== 'DELETE') throw new Error('Type DELETE to confirm.');
      await api('/account',{method:'DELETE',body:{confirmation:'DELETE'}}); closeModal();state.pendingRoute=null;setSession({user:null,profile:null,csrf_token:null,demo_mode:state.session?.demo_mode},true);navigate('home');render();toast('Your account has been deleted.');
    }
  } catch(error) {
    if(error.code==='stale_context')return;
    captureDraft();
    if (errorElement.isConnected) {errorElement.textContent = t(error.message);errorElement.focus();} else toast(error.message);
    if(type==='checkin' && error.status===409 && form.isConnected) { const target=$('[data-response-conflict]',form);target.innerHTML='<div class="notice warning response-conflict"><p>Your draft has not been replaced. The meal or your saved answer changed. Review the saved version before trying again.</p><button class="btn secondary small" type="button" data-action="review-response-conflict" data-i18n="Review%20saved%20answer">Review saved answer</button></div>'; }
  }
  finally { state.pending = false;form.removeAttribute('aria-busy'); if (submit.isConnected) submit.disabled = false; }
});
document.addEventListener('input',event => {
  const form=event.target.closest('#main form');if(form)state.dirty=true;
  if(form?.dataset.form==='checkin' && !event.target.closest('[data-preference-region]')){
    form.dataset.editEpoch=String(Number(form.dataset.editEpoch||0)+1);clearPreferenceDraft(form);
  }
});
document.addEventListener('change',event => {
  const form=event.target.closest('form');
  if (event.target.closest('#main form')) state.dirty=true;
  if(form?.dataset.form==='checkin' && !event.target.closest('[data-preference-region]')){
    form.dataset.editEpoch=String(Number(form.dataset.editEpoch||0)+1);clearPreferenceDraft(form);
  }
  if(form?.dataset.form==='checkin' && event.target.name==='attendance') $('[data-joining-fields]',form).disabled=event.target.value!=='join';
  if(form?.dataset.form==='checkin' && event.target.name==='ready') $('[name="budget"]',form).required=event.target.checked;
  if(form?.dataset.form==='participants' && event.target.name==='participant_ids') { $('[name="confirm_roster"]',form).checked=false;updateParticipantReview(form); }
  if(form?.dataset.form==='feedback' && event.target.name==='outcome') $('[data-visit-fields]',form).disabled=event.target.value!=='ate_here';
});
window.addEventListener('hashchange',() => { captureDraft();closeModal(); state.dirty = false; loadRoute(); });
window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});

async function poll() {
  if (!user() || document.hidden || state.pending || state.loading || state.polling || $('#modal-root dialog')) return;
  const context=requestContext();const mealId=state.route[0]==='meal' ? state.meal?.id : null;
  state.polling=true;
  try {
    const requests=[api('/notifications')];
    if(mealId)requests.push(api(`/meals/${encodeURIComponent(mealId)}`));
    const [notifications,data]=await Promise.all(requests);
    if(!isCurrent(context) || state.pending)return;
    state.notifications=list(notifications?.notifications || notifications);
    state.syncFailed=false;
    if(mealId) {
      const meal=data.meal || data;
      if(JSON.stringify(meal)!==JSON.stringify(state.meal)) {
        if(state.dirty || document.activeElement?.closest('form')) {
          state.updates=true;updateGeneration(meal);
          if(!$('.refresh-notice')) { const notice=document.createElement('div');notice.className='refresh-notice';notice.setAttribute('role','status');notice.innerHTML='<span>'+esc(t('New updates are available. Your draft answers are still here.'))+'</span><button class="text-button" data-action="reload">'+esc(t('Refresh'))+'</button>';$('#main').prepend(notice); }
          return;
        }
        state.meal=meal;render();
      }
      updateGeneration(meal);
    } else if(state.route[0]==='inbox' && !state.dirty)render();
  } catch(error) {
    if(error.code!=='stale_context' && isCurrent(context)) {state.syncFailed=true;if(state.meal && mealId)updateGeneration(state.meal);}
  } finally {state.polling=false;}
}
async function boot() {
  void refreshInference();
  try { setSession(await api('/session')); await loadRoute(); }
  catch(error) { if(error.code==='stale_context')return;$('#app').innerHTML = `<main id="main" class="initial-loading"><span class="loading-mark">m.</span><h1>We couldn’t set the table.</h1><p>${esc(error.message)}</p><button class="btn" data-action="boot-reload" data-i18n="Try%20again">Try again</button></main>`; }
}
async function schedulePoll() { await poll(); const active=['queued','running','retry_wait'].includes(state.meal?.generation_job?.status) && state.route[0]==='meal';setTimeout(schedulePoll,active?3000:12000); }
setTimeout(schedulePoll,3000);
document.addEventListener('visibilitychange',()=> { if (!document.hidden) poll(); });
boot();
