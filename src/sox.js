import { SOX_REFERENCE } from './sox_reference.js';

const MODEL = 'tga-public-face-residual-v1';
const MAX_BYTES = 700_000;
const PRIVATE_PATHS = new Set(['/api/sox/state', '/api/sox/ingest']);
const reply = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: {'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
});
const dateOK = value => typeof value === 'string' && /^20\d\d-\d\d-\d\d$/.test(value) &&
  !Number.isNaN(Date.parse(value + 'T00:00:00Z')) && new Date(value + 'T00:00:00Z').toISOString().slice(0,10) === value;
const numOK = value => value === null || (typeof value === 'number' && Number.isFinite(value));

async function authorized(request, env) {
  if (!env.TREASURY_INGEST_TOKEN) return false;
  const value = request.headers.get('authorization') || '';
  if (value.length > 1024) return false;
  const expected = 'Bearer ' + env.TREASURY_INGEST_TOKEN;
  const enc = new TextEncoder();
  const [a, b] = await Promise.all([value, expected].map(v => crypto.subtle.digest('SHA-256', enc.encode(v))));
  const aa = new Uint8Array(a), bb = new Uint8Array(b);
  let diff = 0;
  for (let i = 0; i < aa.length; i++) diff |= aa[i] ^ bb[i];
  return diff === 0;
}

async function getReport(db, name) {
  const r = await db.prepare('SELECT payload_json FROM sox_reports WHERE name=?').bind(name).first();
  return r ? JSON.parse(r.payload_json) : null;
}

const selectFields = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  ? Object.fromEntries(keys.filter(k => value[k] !== undefined).map(k => [k, value[k]])) : null;
const DAILY_PUBLIC_KEYS = ['date','u1_bn','u4_bn','u3_bn','u5_bn','signed_residual_bn','delta_tga_bn',
  'net_issuance_bn','drawdown_pct','rebound_pct','observed_sessions','macro_ready','first_complete_seen_at',
  'net_status','tga_source_date','issues','status4','status5'];
const SOURCE_PUBLIC_KEYS = ['status','observed_through','fetched_at','source','inventory_asof','auction_rows',
  'inventory_from','inventory_snapshots','known_days','detail','provider','series'];
const withoutOutcomes = row => row && ({...row,drawdown_pct:null,rebound_pct:null,status4:'pending',status5:'pending'});

function sanitizeStatus(status) {
  return selectFields(status, ['state','last_attempt','last_success_day','last_success_at','config_hash',
    'warnings','source_dates','errors']);
}

function sanitizePublicReport(report) {
  if (!report) return null;
  // Public responses never expose stored raw SOX closes or private per-date inputs.
  const keys = ['schema_version','module_version','model_id','generated_at','analysis_from','analysis_through',
    'public_use_confirmed','personal_use_confirmed','analysis_use','selection','sources','warnings','data_days','stats','recent','latest_macro','points'];
  const out = Object.fromEntries(keys.filter(k => report[k] !== undefined).map(k => [k, report[k]]));
  // Protect nested structures too, including future collector changes that
  // accidentally include private input fields in a derived report.
  out.points = Object.fromEntries(['drawdown','rebound'].map(k => [k,
    (report.points?.[k] || []).map(p => selectFields(p, ['date','x','y','delta_tga_bn','net_issuance_bn','first_complete_seen_at']))]));
  out.stats = Object.fromEntries(['drawdown','rebound'].map(k => [k,
    selectFields(report.stats?.[k], ['n','r','slope','intercept'])]));
  out.recent = (report.recent || []).map(r => selectFields(r, DAILY_PUBLIC_KEYS)).filter(Boolean);
  out.latest_macro = selectFields(report.latest_macro, DAILY_PUBLIC_KEYS);
  if (report.sources) out.sources = Object.fromEntries(['tga','principal','sox']
    .filter(k => report.sources[k] !== undefined)
    .map(k => [k, selectFields(report.sources[k], SOURCE_PUBLIC_KEYS)]));
  // Confirmed personal/noncommercial research can show derived charts under
  // FRED's personal-use terms. This does not claim a general redistribution
  // license; raw closes remain excluded in either mode.
  const personalAnalysis = out.personal_use_confirmed === true && out.analysis_use === 'personal_noncommercial';
  if (out.public_use_confirmed !== true && !personalAnalysis) {
    out.points = {drawdown: [], rebound: []};
    out.stats = Object.fromEntries(['drawdown','rebound'].map(k => [k, {n:0,r:null,slope:null,intercept:null}]));
    out.recent = out.recent.map(withoutOutcomes);
    out.latest_macro = withoutOutcomes(out.latest_macro);
  }
  return out;
}

function validPoint(p) {
  return p && dateOK(p.date) && typeof p.x === 'number' && typeof p.y === 'number' &&
    Number.isFinite(p.x) && p.x >= 0 && p.x < 100000 && Number.isFinite(p.y) && Math.abs(p.y) < 100000;
}

function validateReport(r) {
  if (!r || r.model_id !== MODEL || r.schema_version !== 1 || !r.points || !Array.isArray(r.recent)) return false;
  if (r.personal_use_confirmed !== undefined && typeof r.personal_use_confirmed !== 'boolean') return false;
  if (r.analysis_use !== undefined && !['disabled','personal_noncommercial','approved_public'].includes(r.analysis_use)) return false;
  if (r.personal_use_confirmed === true && r.public_use_confirmed !== true && r.analysis_use !== 'personal_noncommercial') return false;
  for (const kind of ['drawdown','rebound']) {
    if (!Array.isArray(r.points[kind]) || r.points[kind].length > 700 || !r.points[kind].every(validPoint)) return false;
  }
  return r.recent.length <= 50 && typeof r.public_use_confirmed === 'boolean';
}

export async function handleSox(request, env) {
  const url = new URL(request.url);
  if (!url.pathname.startsWith('/api/sox')) return null;
  if (!['/api/sox','/api/sox/status',...PRIVATE_PATHS].includes(url.pathname)) return reply({error:'not_found'},404);
  if (PRIVATE_PATHS.has(url.pathname) && !(await authorized(request, env))) return reply({error:'unauthorized'},401);
  try {
    if ((url.pathname === '/api/sox' || url.pathname === '/api/sox/status') && request.method === 'GET') {
      let live = null, status = null;
      try {
        [live, status] = await Promise.all([getReport(env.DB,'live'),getReport(env.DB,'status')]);
      } catch (e) {
        if (String(e).includes('no such table')) status = {state:'MIGRATION_REQUIRED', warnings:['0033_sox.sql을 먼저 적용하세요.']};
        else throw e;
      }
      if (!status) status = {state:'WAITING_FIRST_RUN', warnings:['기존 Treasury collector의 다음 실행을 기다리는 중입니다.']};
      if (url.pathname === '/api/sox/status') return reply(sanitizeStatus(status));
      return reply({module_version:'3.3.0',reference:SOX_REFERENCE,live:sanitizePublicReport(live),status:sanitizeStatus(status),
        server_time:new Date().toISOString(),methods:METHODS});
    }
    if (url.pathname === '/api/sox/state' && request.method === 'GET') {
      const [inputs, auctions, status] = await Promise.all([
        env.DB.prepare('SELECT payload_json FROM sox_daily_inputs ORDER BY date DESC LIMIT 500').all(),
        env.DB.prepare("SELECT raw_json FROM auctions WHERE issue_date >= date('now','-100 days') ORDER BY issue_date LIMIT 2500").all(),
        getReport(env.DB,'status')
      ]);
      if ((auctions.results || []).length === 2500) return reply({error:'auction_context_truncated'},503);
      return reply({inputs:(inputs.results || []).map(r => JSON.parse(r.payload_json)),auctions:auctions.results || [],status});
    }
    if (url.pathname === '/api/sox/ingest' && request.method === 'POST') {
      const size = Number(request.headers.get('content-length') || 0);
      if (size > MAX_BYTES) return reply({error:'payload_too_large'},413);
      const text = await request.text();
      if (new TextEncoder().encode(text).length > MAX_BYTES) return reply({error:'payload_too_large'},413);
      let body;
      try { body = JSON.parse(text); } catch { return reply({error:'invalid_json'},400); }
      if (!body || typeof body !== 'object') return reply({error:'invalid_body'},400);
      const rows = body.inputs || [];
      if (!Array.isArray(rows) || rows.length > 50) return reply({error:'max_50_inputs'},400);
      for (const row of rows) {
        if (!row || row.model_id !== MODEL || !dateOK(row.date) || !/^[a-f0-9]{64}$/.test(row.input_hash || ''))
          return reply({error:'invalid_input_identity'},400);
        for (const key of ['tga_bn','prior_tga_bn','net_issuance_bn','sox_close'])
          if (!numOK(row[key])) return reply({error:'invalid_numeric_field',field:key},400);
        if (row.sox_close !== null && row.sox_close <= 0) return reply({error:'nonpositive_sox_close'},400);
      }
      if (body.report !== undefined && !validateReport(body.report)) return reply({error:'invalid_report'},400);
      if (body.status !== undefined && (!body.status || typeof body.status.state !== 'string')) return reply({error:'invalid_status'},400);
      const stmts = [];
      for (const row of rows) {
        const serialized = JSON.stringify(row);
        stmts.push(env.DB.prepare(`INSERT INTO sox_input_revisions(date,input_hash,payload_json) VALUES(?,?,?)
            ON CONFLICT(date,input_hash) DO NOTHING`).bind(row.date,row.input_hash,serialized));
        stmts.push(env.DB.prepare(`INSERT INTO sox_daily_inputs(date,model_id,input_hash,payload_json) VALUES(?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET model_id=excluded.model_id,input_hash=excluded.input_hash,
                payload_json=excluded.payload_json,updated_at=datetime('now')`).bind(row.date,row.model_id,row.input_hash,serialized));
      }
      for (const name of ['live','status']) {
        const value = name === 'live' ? body.report : body.status;
        if (value !== undefined) stmts.push(env.DB.prepare(`INSERT INTO sox_reports(name,payload_json) VALUES(?,?)
          ON CONFLICT(name) DO UPDATE SET payload_json=excluded.payload_json,updated_at=datetime('now')`).bind(name,JSON.stringify(value)));
      }
      if (!stmts.length) return reply({error:'empty_ingest'},400);
      // Validated, idempotent batch. The client sends at most 25 input rows.
      await env.DB.batch(stmts);
      return reply({ok:true,input_days:rows.length,report_updated:body.report!==undefined,status_updated:body.status!==undefined});
    }
    return reply({error:'method_not_allowed'},405);
  } catch (e) {
    console.error('SOX module failure:', String(e));
    return reply({error:'sox_storage_error',hint:'Check 0033_sox.sql migration and Worker logs.'},503);
  }
}

export const METHODS = {
  residual:'d_t = ΔTGA_t − N_t',
  shock:'U_t(k) = |Σ d_(t−i)|, i=0..k−1. 절댓값은 합산한 뒤 한 번만 적용.',
  drawdown:'100 × [1 − min(P_t,…,P_(t+4)) / P_t]',
  rebound:'100 × [P_(t+5) / min(P_t,…,P_(t+4)) − 1]',
  original:'원본 CSV에 저장된 순발행값·선정 이벤트를 그대로 사용. 14개 점 고정.',
  live:'N은 actual accepted−SOMA−공개보유 만기원금의 액면 대용치. 쿠폰 차감 후 net cash와 섞지 않음. 원본의 일부 추정 입력과 별도 모델.',
  selection:'자동 표본은 결과를 보고 고르지 않은 전체 유효 거래일. 인접일 관측창이 중첩되므로 r을 독립 표본 검정·매매 신호로 해석하지 않음.',
  timing:'이벤트 날짜와 실제 수집시각을 분리. TGA는 사후 발표되므로 이벤트 당일 매매 가능한 신호의 백테스트가 아님.',
  scope:'이 값은 준비금 전달을 직접 측정한 값이 아니라 TGA와 순발행의 회계적 잔차에 기반한 분석용 대용지표. 세수·지출·이자 등이 반영될 수 있음.',
  missing:'미발표·누락은 null/PENDING. 발행이 없는 날에도 만기 일정의 독립 검증 없이는 0으로 두지 않음.',
  sources:['https://home.treasury.gov/data/receipts-outlays','https://www.treasurydirect.gov/auctions/auction-query/',
           'https://fred.stlouisfed.org/series/NASDAQSOX']
};
