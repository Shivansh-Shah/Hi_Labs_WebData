import { useState, useEffect, useCallback, useRef } from 'react'
import axios from 'axios'
import { motion } from 'motion/react'
import { HolographicCard } from './components/ui/holographic-card'
import LandingPage from './LandingPage'

const API = '/api'

// ── Design tokens ──────────────────────────────────────────────────────────────
const BG         = '#0a0b10'
const SURFACE    = '#11131c'
const SURF_MID   = '#1d1f28'
const SURF_HIGH  = '#282933'
const OUTLINE    = '#3a494b'
const CYAN       = '#00dbe7'
const CYAN_LIGHT = '#74f5ff'
const PURPLE     = '#6f00be'
const PURPLE_LT  = '#ddb7ff'
const GREEN      = '#4ae176'
const TEXT       = '#e1e1ee'
const TEXT_SUB   = '#b9cacb'
const TEXT_MUTED = '#849495'

// ── Material icon helper ───────────────────────────────────────────────────────
function Icon({ name, style = {}, filled = false }) {
  return (
    <span
      className="material-symbols-outlined"
      style={{ fontVariationSettings: `'FILL' ${filled ? 1 : 0}`, userSelect: 'none', ...style }}
    >
      {name}
    </span>
  )
}

// ── Label (JetBrains Mono caps) ────────────────────────────────────────────────
function Label({ children, color = CYAN, style = {} }) {
  return (
    <span style={{
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: 10, fontWeight: 700, letterSpacing: '0.12em',
      textTransform: 'uppercase', color, ...style,
    }}>
      {children}
    </span>
  )
}

// ── Spinner ────────────────────────────────────────────────────────────────────
function Spinner({ size = 24, color = CYAN }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%',
      border: `2px solid ${color}22`,
      borderTopColor: color,
      animation: 'spin 0.85s linear infinite',
      flexShrink: 0,
    }} />
  )
}

// ── Toast ──────────────────────────────────────────────────────────────────────
function Toast({ msg, onClose }) {
  useEffect(() => {
    if (!msg) return
    const t = setTimeout(onClose, 5500)
    return () => clearTimeout(t)
  }, [msg, onClose])
  if (!msg) return null
  const isErr = msg.startsWith('Error')
  return (
    <div className="anim-toast" style={{
      position: 'fixed', bottom: 24, right: 24, zIndex: 9999,
      maxWidth: 360, borderRadius: 12, padding: '14px 18px',
      display: 'flex', alignItems: 'flex-start', gap: 10,
      fontFamily: "'Inter', sans-serif", fontSize: 13,
      background: isErr ? '#1a0808' : '#081412',
      border: `1px solid ${isErr ? 'rgba(255,107,107,0.3)' : 'rgba(0,219,231,0.25)'}`,
      color: isErr ? '#ff8585' : '#74f5ff',
      boxShadow: isErr ? '0 0 30px rgba(255,50,50,0.1)' : '0 0 30px rgba(0,219,231,0.1)',
    }}>
      <span style={{ flexShrink: 0, marginTop: 1 }}>{isErr ? '⚠' : '✓'}</span>
      <span style={{ flex: 1, lineHeight: 1.5 }}>{msg}</span>
      <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'inherit', opacity: 0.5, cursor: 'pointer', fontSize: 16 }}>×</button>
    </div>
  )
}

// ── Tier badge + row styling ───────────────────────────────────────────────────
const TIER = {
  high:   { color: '#ff6b8a', bg: 'rgba(255,107,138,0.1)', border: 'rgba(255,107,138,0.35)', glow: '0 0 10px rgba(255,107,138,0.35)' },
  medium: { color: '#f4c430', bg: 'rgba(244,196,48,0.1)',  border: 'rgba(244,196,48,0.35)',  glow: '0 0 10px rgba(244,196,48,0.3)'   },
  low:    { color: TEXT_MUTED, bg: 'rgba(255,255,255,0.03)', border: 'rgba(255,255,255,0.07)', glow: 'none' },
}

function TierBadge({ tier }) {
  const t = TIER[tier] || TIER.low
  return (
    <span style={{
      display: 'inline-flex', padding: '3px 10px', borderRadius: 99,
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: 9, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase',
      color: t.color, background: t.bg, border: `1px solid ${t.border}`,
    }}>{tier}</span>
  )
}

function Avatar({ name, tier }) {
  const t = TIER[tier] || TIER.low
  const init = name?.split(/\s+/).slice(0,2).map(w => w[0]).join('').toUpperCase() || '?'
  return (
    <div style={{
      width: 32, height: 32, borderRadius: 8, flexShrink: 0,
      background: t.bg, border: `1px solid ${t.border}`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: 10, fontWeight: 700, color: t.color,
    }}>{init}</div>
  )
}

// ── Glass card ─────────────────────────────────────────────────────────────────
function GlassCard({ children, style = {}, glow = false }) {
  return (
    <div style={{
      background: 'rgba(22,24,33,0.88)',
      backdropFilter: 'blur(20px)',
      WebkitBackdropFilter: 'blur(20px)',
      border: '0.5px solid rgba(255,255,255,0.09)',
      borderRadius: 16,
      ...(glow && { boxShadow: `0 0 20px rgba(0,219,231,0.12)` }),
      ...style,
    }}>
      {children}
    </div>
  )
}

// ── Stat card ──────────────────────────────────────────────────────────────────
const STAT_COLORS = [CYAN, PURPLE_LT, GREEN, '#ff6b8a', '#f4c430', CYAN_LIGHT]

function StatCard({ num, label, value, sub }) {
  const color = STAT_COLORS[(num - 1) % STAT_COLORS.length]
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: (num - 1) * 0.06 }}
    >
      <HolographicCard>
        <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.15)', marginBottom: 14, letterSpacing: '0.1em' }}>
          {String(num).padStart(2, '0')}
        </div>
        <div style={{ fontSize: 32, fontWeight: 800, lineHeight: 1, color, letterSpacing: '-0.03em', marginBottom: 10, fontFamily: "'Hanken Grotesk', sans-serif" }}>
          {value ?? '—'}
        </div>
        <div style={{ fontSize: 12, color: TEXT_MUTED, fontWeight: 600, fontFamily: "'Inter', sans-serif" }}>{label}</div>
        {sub && <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.18)', marginTop: 3, fontFamily: "'Inter', sans-serif" }}>{sub}</div>}
      </HolographicCard>
    </motion.div>
  )
}

// ── Deals table ────────────────────────────────────────────────────────────────
function DealsTable({ deals, loading, selected, setSelected }) {
  const [hov, setHov] = useState(null)

  if (loading) return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '60px 0' }}>
      <Spinner size={28} />
    </div>
  )

  const COLS = ['#', 'Company', 'Vendor', 'Category', 'Confidence', 'Closes', 'Window', 'Tier', '']

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr>
            {COLS.map((h, i) => (
              <th key={i} style={{
                padding: '12px 16px', textAlign: 'left',
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 9, fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase',
                color: 'rgba(255,255,255,0.2)', whiteSpace: 'nowrap',
                borderBottom: `1px solid ${OUTLINE}44`,
                background: `${SURFACE}cc`,
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {deals.map((d, idx) => {
            const open  = selected?.intelligence_id === d.intelligence_id
            const isHov = hov === d.intelligence_id
            const tier  = (d.tier || 'low').toLowerCase()
            const t     = TIER[tier] || TIER.low
            return (
              <>
                <tr
                  key={d.intelligence_id}
                  onClick={() => setSelected(open ? null : d)}
                  onMouseEnter={() => setHov(d.intelligence_id)}
                  onMouseLeave={() => setHov(null)}
                  style={{
                    borderBottom: `1px solid ${OUTLINE}33`,
                    cursor: 'pointer',
                    background: open ? `${t.bg}` : isHov ? 'rgba(255,255,255,0.018)' : 'transparent',
                    transition: 'background 0.15s',
                  }}
                >
                  <td style={{
                    padding: '15px 16px',
                    fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 700,
                    color: 'rgba(255,255,255,0.2)',
                    borderLeft: open ? `3px solid ${t.color}` : isHov ? `3px solid ${CYAN}66` : '3px solid transparent',
                    transition: 'border-color 0.15s',
                  }}>{String(idx + 1).padStart(2, '0')}</td>

                  <td style={{ padding: '15px 16px', whiteSpace: 'nowrap' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <Avatar name={d.target_company} tier={tier} />
                      <span style={{ fontWeight: 700, fontSize: 13, color: TEXT, fontFamily: "'Inter', sans-serif" }}>
                        {d.target_company}
                      </span>
                    </div>
                  </td>

                  <td style={{ padding: '15px 16px', color: TEXT_SUB, whiteSpace: 'nowrap', fontFamily: "'Inter', sans-serif", fontSize: 13 }}>
                    {d.suspected_vendor}
                  </td>

                  <td style={{ padding: '15px 16px', whiteSpace: 'nowrap' }}>
                    <span style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 10, fontWeight: 600, padding: '3px 8px', borderRadius: 6,
                      background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.07)',
                      color: TEXT_MUTED,
                    }}>{d.vendor_category}</span>
                  </td>

                  <td style={{ padding: '15px 16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ width: 72, height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 99, overflow: 'hidden' }}>
                        <div className="anim-bar" style={{
                          height: '100%', borderRadius: 99,
                          width: `${(d.confidence * 100).toFixed(0)}%`,
                          background: `linear-gradient(90deg, ${CYAN} 0%, ${PURPLE_LT} 100%)`,
                        }} />
                      </div>
                      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, fontWeight: 700, color: CYAN }}>
                        {(d.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                  </td>

                  <td style={{ padding: '15px 16px', color: TEXT_MUTED, fontSize: 12, whiteSpace: 'nowrap', fontFamily: "'Inter', sans-serif" }}>
                    {d.deal_closed_estimate}
                  </td>
                  <td style={{ padding: '15px 16px', color: TEXT_MUTED, fontSize: 12, whiteSpace: 'nowrap', fontFamily: "'Inter', sans-serif" }}>
                    {d.outreach_window}
                  </td>
                  <td style={{ padding: '15px 16px' }}><TierBadge tier={tier} /></td>
                  <td style={{ padding: '15px 16px', color: TEXT_MUTED, fontSize: 11 }}>
                    <span style={{ display: 'inline-block', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.25s' }}>▼</span>
                  </td>
                </tr>

                {open && (
                  <tr key={`${d.intelligence_id}-exp`} className="anim-expand">
                    <td colSpan={9} style={{
                      padding: '22px 28px 28px',
                      background: `linear-gradient(180deg, ${t.bg}, transparent)`,
                      borderBottom: `1px solid ${OUTLINE}33`,
                      borderLeft: `3px solid ${t.color}`,
                    }}>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 40 }}>
                        <div>
                          <Label style={{ display: 'block', marginBottom: 10, color: TEXT_MUTED }}>AI Reasoning</Label>
                          <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 13, color: TEXT_SUB, lineHeight: 1.75 }}>
                            {d.reasoning}
                          </p>
                          {d.new_vendor_patterns?.length > 0 && (
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 14 }}>
                              {d.new_vendor_patterns.map((p, i) => (
                                <span key={i} style={{
                                  fontFamily: "'JetBrains Mono', monospace",
                                  fontSize: 11, padding: '3px 10px', borderRadius: 6,
                                  background: `${CYAN}12`, border: `1px solid ${CYAN}28`, color: CYAN_LIGHT,
                                }}>{p.pattern} → {p.vendor}</span>
                              ))}
                            </div>
                          )}
                        </div>
                        {d.signals?.length > 0 && (
                          <div>
                            <Label style={{ display: 'block', marginBottom: 10, color: TEXT_MUTED }}>
                              Signals ({d.signals.length})
                            </Label>
                            <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 8 }}>
                              {d.signals.map((s, i) => (
                                <li key={i} style={{ display: 'flex', gap: 10, fontFamily: "'Inter', sans-serif", fontSize: 12, color: TEXT_SUB, lineHeight: 1.55 }}>
                                  <span style={{ color: CYAN, flexShrink: 0, marginTop: 1 }}>◈</span>{s}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </>
            )
          })}

          {deals.length === 0 && (
            <tr>
              <td colSpan={9} style={{ padding: '72px 24px', textAlign: 'center' }}>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14 }}>
                  <div style={{
                    width: 52, height: 52, borderRadius: '50%',
                    border: `1px solid ${CYAN}28`,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 20, color: `${CYAN}55`,
                  }}>◎</div>
                  <span style={{ fontFamily: "'Inter', sans-serif", fontSize: 13, color: TEXT_MUTED, lineHeight: 1.65 }}>
                    No deals yet — ingest sample data or run the pipeline
                  </span>
                </div>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

// ── GTM Dashboard tab ──────────────────────────────────────────────────────────

function GTMDashboard({ showToast }) {
  const [stats,      setStats]      = useState(null)
  const [deals,      setDeals]      = useState([])
  const [loading,    setLoading]    = useState(false)
  const [tierFilter, setTierFilter] = useState('')
  const [selected,   setSelected]   = useState(null)
  const [runForm,    setRunForm]    = useState({ domains: '', window_days: 30, github_orgs: '' })
  const [digestForm, setDigestForm] = useState({ to_email: '', tier_filter: 'high' })
  const [ingestFile, setIngestFile] = useState('acme.json')

  const fetchStats = useCallback(async () => {
    try { const r = await axios.get(`${API}/stats`); setStats(r.data) } catch {}
  }, [])

  const fetchDeals = useCallback(async () => {
    setLoading(true)
    try {
      const r = await axios.get(`${API}/intelligence`, { params: tierFilter ? { tier: tierFilter } : {} })
      setDeals(r.data)
    } catch (e) { showToast(`Error: ${e.message}`) }
    finally { setLoading(false) }
  }, [tierFilter, showToast])

  useEffect(() => { fetchStats(); fetchDeals() }, [fetchStats, fetchDeals])

  const pollStats = () => {
    let n = 0
    const id = setInterval(() => { fetchStats(); if (++n >= 12) clearInterval(id) }, 5000)
  }

  const handleRun = async () => {
    const domains = runForm.domains.split(',').map(s => s.trim()).filter(Boolean)
    if (!domains.length) { showToast('Error: enter at least one domain'); return }
    try {
      const r = await axios.post(`${API}/run`, {
        target_domains: domains,
        github_orgs: runForm.github_orgs.split(',').map(s => s.trim()).filter(Boolean),
        window_days: Number(runForm.window_days), enrich: true,
      })
      showToast(`Pipeline started — ${r.data.run_id.slice(0, 8)}…`)
      pollStats()
    } catch (e) { showToast(`Error: ${e.response?.data?.detail || e.message}`) }
  }

  const handleIngest = async () => {
    try {
      const r = await axios.post(`${API}/ingest?filename=${encodeURIComponent(ingestFile)}`)
      showToast(`Ingested ${r.data.ingested} records (${r.data.skipped} skipped)`)
      fetchStats(); fetchDeals()
    } catch (e) { showToast(`Error: ${e.response?.data?.detail || e.message}`) }
  }

  const handleDigest = async () => {
    if (!digestForm.to_email) { showToast('Error: enter recipient email'); return }
    try {
      const r = await axios.post(`${API}/digest/send`, digestForm)
      showToast(`Digest sent to ${digestForm.to_email} — ${r.data.count} deals`)
    } catch (e) { showToast(`Error: ${e.response?.data?.detail || e.message}`) }
  }

  const S = { fontFamily: "'Inter', sans-serif" }

  return (
    <div style={{ padding: '32px 36px', ...S }}>

      {/* Heading */}
      <div style={{ marginBottom: 32 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
          <Icon name="radar" filled style={{ color: CYAN, fontSize: 20 }} />
          <Label color={CYAN}>Ghost Pipeline Detector</Label>
        </div>
        <h1 style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 28, fontWeight: 800, color: TEXT, letterSpacing: '-0.02em', margin: 0 }}>
          Deal Intelligence Feed
        </h1>
        <p style={{ color: TEXT_MUTED, fontSize: 14, marginTop: 6 }}>
          Real-time signals from CT logs, DNS mutations, Wayback snapshots &amp; GitHub spikes.
        </p>
      </div>

      {/* Live Metrics */}
      <div style={{ marginBottom: 10 }}>
        <SectionHead icon="monitoring" label="Live Metrics" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(155px, 1fr))', gap: 12, marginBottom: 36 }}>
        <StatCard num={1} label="Signals"        value={stats?.signals} />
        <StatCard num={2} label="Clusters"        value={stats?.clusters} />
        <StatCard num={3} label="Intel Records"   value={stats?.intelligence} />
        <StatCard num={4} label="HIGH priority"   value={stats?.tiers?.high}   sub="hot leads" />
        <StatCard num={5} label="MEDIUM priority" value={stats?.tiers?.medium} sub="watch list" />
        <StatCard num={6} label="AI Spend"        value={stats ? `$${stats.total_ai_cost_usd.toFixed(4)}` : null} sub="gpt-4o-mini" />
      </div>

      {/* Deals table */}
      <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <SectionHead icon="table_rows" label="Deal Intelligence" count={deals.length} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 11, color: TEXT_MUTED }}>Tier</span>
          <select className="nym-select" style={{ width: 'auto', padding: '5px 10px', fontSize: 12 }}
            value={tierFilter} onChange={e => setTierFilter(e.target.value)}>
            <option value="">All</option>
            <option value="high">HIGH</option>
            <option value="medium">MEDIUM</option>
            <option value="low">LOW</option>
          </select>
        </div>
      </div>

      <div style={{ borderRadius: 16, overflow: 'hidden', background: SURFACE, border: `1px solid ${OUTLINE}44`, marginBottom: 40 }}>
        <DealsTable deals={deals} loading={loading} selected={selected} setSelected={setSelected} />
      </div>

      {/* Pipeline Controls */}
      <div style={{ marginBottom: 20 }}>
        <SectionHead icon="settings_applications" label="Pipeline Controls" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 20, marginBottom: 40 }}>

        <ControlCard
          num="01" numColor={CYAN} borderColor={CYAN}
          title="Run Pipeline" sub="Stages 1–5 · background worker"
          icon="play_circle"
        >
          <input className="nym-input" placeholder="acme.com, rival.io"
            value={runForm.domains} onChange={e => setRunForm(f => ({ ...f, domains: e.target.value }))} />
          <input className="nym-input" placeholder="GitHub orgs (optional)"
            value={runForm.github_orgs} onChange={e => setRunForm(f => ({ ...f, github_orgs: e.target.value }))} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 11, color: TEXT_MUTED, whiteSpace: 'nowrap' }}>Window (days)</span>
            <input className="nym-input" type="number" min={1} max={365}
              value={runForm.window_days} onChange={e => setRunForm(f => ({ ...f, window_days: e.target.value }))} />
          </div>
          <button className="btn-primary" onClick={handleRun} style={{ background: CYAN, color: '#001a1c', fontWeight: 700 }}>
            ▶ Run Pipeline
          </button>
        </ControlCard>

        <ControlCard
          num="02" numColor={PURPLE_LT} borderColor={PURPLE_LT}
          title="Ingest Sample Data" sub="Load from stage6/sample_deals/"
          icon="upload_file"
        >
          <input className="nym-input" placeholder="filename.json"
            value={ingestFile} onChange={e => setIngestFile(e.target.value)} />
          <p style={{ fontSize: 12, color: TEXT_MUTED, lineHeight: 1.6 }}>
            Drop a <code style={{ color: TEXT_SUB, background: SURF_HIGH, padding: '1px 6px', borderRadius: 4, fontFamily: "'JetBrains Mono', monospace" }}>.json</code>{' '}
            into <code style={{ color: TEXT_SUB, background: SURF_HIGH, padding: '1px 6px', borderRadius: 4, fontFamily: "'JetBrains Mono', monospace" }}>stage6/sample_deals/</code>
          </p>
          <button className="btn-ghost" onClick={handleIngest}>↑ Ingest JSON</button>
        </ControlCard>

        <ControlCard
          num="03" numColor={GREEN} borderColor={GREEN}
          title="Email Digest" sub="Send via Gmail SMTP"
          icon="mail"
        >
          <input className="nym-input" type="email" placeholder="you@company.com"
            value={digestForm.to_email} onChange={e => setDigestForm(f => ({ ...f, to_email: e.target.value }))} />
          <select className="nym-select" value={digestForm.tier_filter}
            onChange={e => setDigestForm(f => ({ ...f, tier_filter: e.target.value }))}>
            <option value="high">HIGH tier only</option>
            <option value="medium">MEDIUM tier</option>
            <option value="low">LOW tier</option>
          </select>
          <button className="btn-primary" onClick={handleDigest} style={{ background: GREEN, color: '#001a09', fontWeight: 700 }}>
            ✉ Send Digest
          </button>
          <a href={`/api/digest/preview?tier_filter=${digestForm.tier_filter}`} target="_blank" rel="noreferrer"
            style={{ textAlign: 'center', fontSize: 11, color: TEXT_MUTED, textDecoration: 'none' }}
            onMouseEnter={e => (e.target.style.color = CYAN)}
            onMouseLeave={e => (e.target.style.color = TEXT_MUTED)}
          >Preview HTML ↗</a>
        </ControlCard>

      </div>

      {/* Footer */}
      <div style={{ borderTop: `1px solid ${OUTLINE}33`, paddingTop: 24, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <div style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontWeight: 700, fontSize: 13, color: TEXT }}>GTM Intelligence Platform</div>
          <div style={{ fontFamily: "'Inter', sans-serif", fontSize: 11, color: TEXT_MUTED, marginTop: 3 }}>You bring the domain. We bring the deal signal.</div>
        </div>
        <Label color={TEXT_MUTED} style={{ fontSize: 9, letterSpacing: '0.14em' }}>CT Logs · DNS · Wayback · GitHub · Bright Data · GPT-4o-mini</Label>
      </div>
    </div>
  )
}

// ── Sniper results table ───────────────────────────────────────────────────────

function SniperResultsTable({ results, loading, selected, setSelected }) {
  const [hov, setHov] = useState(null)

  if (loading) return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '60px 0' }}>
      <Spinner size={28} />
    </div>
  )

  const COLS = ['#', 'Competitor', 'Suspected Product', 'Confidence', 'Signals', 'Tier', 'Scanned', '']

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr>
            {COLS.map((h, i) => (
              <th key={i} style={{
                padding: '12px 16px', textAlign: 'left',
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 9, fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase',
                color: 'rgba(255,255,255,0.2)', whiteSpace: 'nowrap',
                borderBottom: `1px solid ${OUTLINE}44`,
                background: `${SURFACE}cc`,
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {results.map((result, idx) => {
            const rowKey  = `${result.run_id}-${result.competitor}`
            const open    = selected?.rowKey === rowKey
            const isHov   = hov === rowKey
            const tier    = (result.tier || 'low').toLowerCase()
            const t       = TIER[tier] || TIER.low
            const conf    = result.confidence ?? 0
            return (
              <>
                <tr
                  key={rowKey}
                  onClick={() => setSelected(open ? null : { ...result, rowKey })}
                  onMouseEnter={() => setHov(rowKey)}
                  onMouseLeave={() => setHov(null)}
                  style={{
                    borderBottom: `1px solid ${OUTLINE}33`,
                    cursor: 'pointer',
                    background: open ? t.bg : isHov ? 'rgba(255,255,255,0.018)' : 'transparent',
                    transition: 'background 0.15s',
                  }}
                >
                  <td style={{
                    padding: '15px 16px',
                    fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 700,
                    color: 'rgba(255,255,255,0.2)',
                    borderLeft: open ? `3px solid ${t.color}` : isHov ? `3px solid ${GREEN}66` : '3px solid transparent',
                    transition: 'border-color 0.15s',
                  }}>{String(idx + 1).padStart(2, '0')}</td>

                  <td style={{ padding: '15px 16px', whiteSpace: 'nowrap' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <Avatar name={result.competitor} tier={tier} />
                      <span style={{ fontWeight: 700, fontSize: 13, color: TEXT, fontFamily: "'Inter', sans-serif" }}>
                        {result.competitor}
                      </span>
                    </div>
                  </td>

                  <td style={{ padding: '15px 16px', color: TEXT_SUB, fontFamily: "'Inter', sans-serif", fontSize: 13, maxWidth: 220 }}>
                    <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {result.suspected_product || '—'}
                    </span>
                  </td>

                  <td style={{ padding: '15px 16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ width: 72, height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 99, overflow: 'hidden' }}>
                        <div className="anim-bar" style={{
                          height: '100%', borderRadius: 99,
                          width: `${conf}%`,
                          background: `linear-gradient(90deg, ${GREEN} 0%, ${CYAN} 100%)`,
                        }} />
                      </div>
                      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, fontWeight: 700, color: GREEN }}>
                        {conf}%
                      </span>
                    </div>
                  </td>

                  <td style={{ padding: '15px 16px', textAlign: 'center' }}>
                    <span style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 11, fontWeight: 700, padding: '3px 8px', borderRadius: 6,
                      background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.07)',
                      color: TEXT_MUTED,
                    }}>{result.signal_count ?? 0}</span>
                  </td>

                  <td style={{ padding: '15px 16px' }}><TierBadge tier={tier} /></td>

                  <td style={{ padding: '15px 16px', color: TEXT_MUTED, fontSize: 12, whiteSpace: 'nowrap', fontFamily: "'Inter', sans-serif" }}>
                    {result.scan_date ? new Date(result.scan_date).toLocaleDateString() : '—'}
                  </td>

                  <td style={{ padding: '15px 16px', color: TEXT_MUTED, fontSize: 11 }}>
                    <span style={{ display: 'inline-block', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.25s' }}>▼</span>
                  </td>
                </tr>

                {open && (
                  <tr key={`${rowKey}-exp`} className="anim-expand">
                    <td colSpan={8} style={{
                      padding: '22px 28px 28px',
                      background: `linear-gradient(180deg, ${t.bg}, transparent)`,
                      borderBottom: `1px solid ${OUTLINE}33`,
                      borderLeft: `3px solid ${t.color}`,
                    }}>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 40 }}>
                        <div>
                          <Label style={{ display: 'block', marginBottom: 10, color: TEXT_MUTED }}>
                            Detected Signals ({(result.signals || []).length})
                          </Label>
                          {(result.signals || []).length > 0
                            ? (
                              <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 8 }}>
                                {result.signals.map((s, i) => (
                                  <li key={i} style={{ display: 'flex', gap: 10, fontFamily: "'Inter', sans-serif", fontSize: 12, color: TEXT_SUB, lineHeight: 1.55 }}>
                                    <span style={{ color: GREEN, flexShrink: 0, marginTop: 1 }}>◈</span>{s}
                                  </li>
                                ))}
                              </ul>
                            )
                            : <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 12, color: TEXT_MUTED }}>No individual signals captured — see raw report for details.</p>
                          }
                        </div>
                        <div>
                          <Label style={{ display: 'block', marginBottom: 10, color: TEXT_MUTED }}>Counter-Playbook</Label>
                          {result.counter_playbook
                            ? <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 13, color: TEXT_SUB, lineHeight: 1.75 }}>{result.counter_playbook}</p>
                            : <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 12, color: TEXT_MUTED }}>No specific playbook generated — run with full enrichment for recommendations.</p>
                          }
                          <div style={{ marginTop: 16, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                            {(result.domains || []).map((d, i) => (
                              <span key={i} style={{
                                fontFamily: "'JetBrains Mono', monospace",
                                fontSize: 10, padding: '3px 10px', borderRadius: 6,
                                background: `${GREEN}12`, border: `1px solid ${GREEN}28`, color: GREEN,
                              }}>{d}</span>
                            ))}
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            )
          })}

          {results.length === 0 && (
            <tr>
              <td colSpan={8} style={{ padding: '72px 24px', textAlign: 'center' }}>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14 }}>
                  <div style={{
                    width: 52, height: 52, borderRadius: '50%',
                    border: `1px solid ${GREEN}28`,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 20, color: `${GREEN}55`,
                  }}>⊙</div>
                  <span style={{ fontFamily: "'Inter', sans-serif", fontSize: 13, color: TEXT_MUTED, lineHeight: 1.65 }}>
                    No launches detected yet — run a scan to start intercepting signals
                  </span>
                </div>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

// ── Launch Sniper tab ──────────────────────────────────────────────────────────

function SniperDashboard({ showToast }) {
  const [stats,        setStats]        = useState(null)
  const [results,      setResults]      = useState([])
  const [loading,      setLoading]      = useState(false)
  const [tierFilter,   setTierFilter]   = useState('')
  const [domainFilter, setDomainFilter] = useState('')
  const [selected,     setSelected]     = useState(null)
  const [runForm,      setRunForm]      = useState({ domains: '' })
  const [emailForm,    setEmailForm]    = useState({ to_email: '', tier_filter: 'high' })
  const [polling,      setPolling]      = useState(false)
  const pollRef = useRef(null)

  const fetchStats = useCallback(async () => {
    try { const r = await axios.get(`${API}/sniper/stats`); setStats(r.data) } catch {}
  }, [])

  const fetchResults = useCallback(async () => {
    setLoading(true)
    try {
      const params = {}
      if (tierFilter)   params.tier   = tierFilter
      if (domainFilter) params.domain = domainFilter
      const r = await axios.get(`${API}/sniper/results`, { params })
      setResults(r.data)
    } catch (e) { showToast(`Error: ${e.message}`) }
    finally { setLoading(false) }
  }, [tierFilter, domainFilter, showToast])

  useEffect(() => { fetchStats(); fetchResults() }, [fetchStats, fetchResults])

  const startPolling = () => {
    clearInterval(pollRef.current)
    setPolling(true)
    let ticks = 0
    pollRef.current = setInterval(async () => {
      await fetchStats()
      await fetchResults()
      ticks++
      // Stop after ~3 min (45 × 4s) or when active_runs hits 0
      try {
        const r = await axios.get(`${API}/sniper/stats`)
        if (r.data.active_runs === 0 || ticks >= 45) {
          clearInterval(pollRef.current)
          setPolling(false)
          if (r.data.launches_detected > 0)
            showToast(`Scan complete — ${r.data.launches_detected} launch signal${r.data.launches_detected !== 1 ? 's' : ''} detected`)
          else
            showToast('Scan complete — no new launches detected')
        }
      } catch {}
    }, 4000)
  }

  useEffect(() => () => clearInterval(pollRef.current), [])

  const handleRun = async () => {
    const d = runForm.domains.split(',').map(s => s.trim()).filter(Boolean)
    if (!d.length) { showToast('Error: enter at least one domain'); return }
    try {
      const r = await axios.post(`${API}/sniper/run`, { domains: d })
      showToast(`Launch Sniper started — run ${r.data.run_id}`)
      startPolling()
    } catch (e) { showToast(`Error: ${e.response?.data?.detail || e.message}`) }
  }

  const handleEmail = async () => {
    if (!emailForm.to_email) { showToast('Error: enter recipient email'); return }
    try {
      const r = await axios.post(`${API}/sniper/email`, emailForm)
      showToast(`Sniper report sent to ${emailForm.to_email} — ${r.data.count} result${r.data.count !== 1 ? 's' : ''}`)
    } catch (e) { showToast(`Error: ${e.response?.data?.detail || e.message}`) }
  }

  const S = { fontFamily: "'Inter', sans-serif" }

  return (
    <div style={{ padding: '32px 36px', ...S }}>

      {/* Heading */}
      <div style={{ marginBottom: 32 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
          <Icon name="track_changes" filled style={{ color: GREEN, fontSize: 20 }} />
          <Label color={GREEN}>Launch Sniper</Label>
        </div>
        <h1 style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 28, fontWeight: 800, color: TEXT, letterSpacing: '-0.02em', margin: 0 }}>
          Competitor Launch Intelligence
        </h1>
        <p style={{ color: TEXT_MUTED, fontSize: 14, marginTop: 6 }}>
          WHOIS registrations · Trademark filings · robots.txt mutations · GitHub spikes — detect launches 45 days early.
        </p>
      </div>

      {/* Live Metrics */}
      <div style={{ marginBottom: 10 }}>
        <SectionHead icon="monitoring" label="Live Metrics" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(155px, 1fr))', gap: 12, marginBottom: 36 }}>
        <StatCard num={1} label="Total Scans"       value={stats?.total_runs}         sub="all runs" />
        <StatCard num={2} label="Domains Scanned"   value={stats?.domains_scanned}    sub="unique domains" />
        <StatCard num={3} label="Launches Detected" value={stats?.launches_detected}  sub="across all runs" />
        <StatCard num={4} label="HIGH Confidence"   value={stats?.tiers?.high}        sub="≥75% confidence" />
        <StatCard num={5} label="MEDIUM Signals"    value={stats?.tiers?.medium}      sub="45–74% confidence" />
        <StatCard num={6} label="Active Scans"      value={stats?.active_runs != null ? (polling ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Spinner size={16} color={GREEN} />{stats.active_runs}</span> : stats.active_runs) : null} sub="running now" />
      </div>

      {/* Results table */}
      <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <SectionHead icon="table_rows" label="Detected Launches" count={results.length} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 11, color: TEXT_MUTED }}>Tier</span>
          <select className="nym-select" style={{ width: 'auto', padding: '5px 10px', fontSize: 12 }}
            value={tierFilter} onChange={e => setTierFilter(e.target.value)}>
            <option value="">All</option>
            <option value="high">HIGH</option>
            <option value="medium">MEDIUM</option>
            <option value="low">LOW</option>
          </select>
        </div>
      </div>

      <div style={{ borderRadius: 16, overflow: 'hidden', background: SURFACE, border: `1px solid ${OUTLINE}44`, marginBottom: 40 }}>
        <SniperResultsTable results={results} loading={loading} selected={selected} setSelected={setSelected} />
      </div>

      {/* Sniper Controls */}
      <div style={{ marginBottom: 20 }}>
        <SectionHead icon="settings_applications" label="Sniper Controls" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 20, marginBottom: 40 }}>

        <ControlCard
          num="01" numColor={GREEN} borderColor={GREEN}
          title="Run Sniper" sub="WHOIS · Trademarks · robots.txt · GitHub"
          icon="radar"
        >
          <input className="nym-input" placeholder="competitor.com, rival.io"
            value={runForm.domains}
            onChange={e => setRunForm(f => ({ ...f, domains: e.target.value }))}
            onKeyDown={e => e.key === 'Enter' && handleRun()}
          />
          <p style={{ fontSize: 12, color: TEXT_MUTED, lineHeight: 1.6 }}>
            Comma-separate multiple domains. Pipeline runs all 6 intelligence stages in background.
          </p>
          <button
            className="btn-primary"
            onClick={handleRun}
            disabled={polling}
            style={{
              background: polling ? SURF_HIGH : GREEN, color: polling ? TEXT_MUTED : '#001a09',
              fontWeight: 700, cursor: polling ? 'not-allowed' : 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
            }}
          >
            {polling
              ? <><Spinner size={14} color={CYAN} /> Scanning…</>
              : <><Icon name="search" style={{ fontSize: 16 }} /> Run Sniper</>
            }
          </button>
        </ControlCard>

        <ControlCard
          num="02" numColor={CYAN} borderColor={CYAN}
          title="Filter Results" sub="Narrow by tier or domain keyword"
          icon="filter_list"
        >
          <div>
            <div style={{ fontSize: 11, color: TEXT_MUTED, marginBottom: 5 }}>Confidence Tier</div>
            <select className="nym-select"
              value={tierFilter} onChange={e => setTierFilter(e.target.value)}>
              <option value="">All tiers</option>
              <option value="high">HIGH (≥75%)</option>
              <option value="medium">MEDIUM (45–74%)</option>
              <option value="low">LOW (&lt;45%)</option>
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: TEXT_MUTED, marginBottom: 5 }}>Domain keyword</div>
            <input className="nym-input" placeholder="e.g. stripe"
              value={domainFilter} onChange={e => setDomainFilter(e.target.value)} />
          </div>
          <button className="btn-ghost" onClick={fetchResults} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
            <Icon name="refresh" style={{ fontSize: 16 }} /> Apply Filters
          </button>
        </ControlCard>

        <ControlCard
          num="03" numColor={PURPLE_LT} borderColor={PURPLE_LT}
          title="Email Report" sub="Send via Gmail SMTP"
          icon="mail"
        >
          <input className="nym-input" type="email" placeholder="you@company.com"
            value={emailForm.to_email}
            onChange={e => setEmailForm(f => ({ ...f, to_email: e.target.value }))} />
          <select className="nym-select"
            value={emailForm.tier_filter}
            onChange={e => setEmailForm(f => ({ ...f, tier_filter: e.target.value }))}>
            <option value="">All tiers</option>
            <option value="high">HIGH confidence only</option>
            <option value="medium">MEDIUM confidence</option>
            <option value="low">LOW confidence</option>
          </select>
          <button
            className="btn-primary"
            onClick={handleEmail}
            style={{ background: PURPLE_LT, color: '#1a0033', fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}
          >
            <Icon name="send" style={{ fontSize: 15 }} /> Send Report
          </button>
        </ControlCard>

      </div>

      {/* Footer */}
      <div style={{ borderTop: `1px solid ${OUTLINE}33`, paddingTop: 24, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <div style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontWeight: 700, fontSize: 13, color: TEXT }}>Launch Sniper · GTM Intelligence Platform</div>
          <div style={{ fontFamily: "'Inter', sans-serif", fontSize: 11, color: TEXT_MUTED, marginTop: 3 }}>See it before they announce it.</div>
        </div>
        <Label color={TEXT_MUTED} style={{ fontSize: 9, letterSpacing: '0.14em' }}>WHOIS · USPTO Trademarks · robots.txt · GitHub · Bright Data · GPT-4o-mini</Label>
      </div>
    </div>
  )
}

// ── Section heading helper ─────────────────────────────────────────────────────
function SectionHead({ icon, label, count }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 0 }}>
      <Icon name={icon} style={{ fontSize: 16, color: CYAN }} />
      <Label color={CYAN}>{label}</Label>
      {count != null && (
        <span style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 99,
          background: `${CYAN}15`, border: `1px solid ${CYAN}28`, color: CYAN,
        }}>{count}</span>
      )}
    </div>
  )
}

// ── Control card ───────────────────────────────────────────────────────────────
function ControlCard({ num, numColor, borderColor, title, sub, icon, children }) {
  return (
    <GlassCard style={{ padding: 24, borderLeft: `3px solid ${borderColor}40`, display: 'flex', flexDirection: 'column', gap: 0 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 18 }}>
        <Icon name={icon} filled style={{ color: numColor, fontSize: 20, marginTop: 1 }} />
        <div>
          <div style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 14, fontWeight: 700, color: TEXT }}>{title}</div>
          {sub && <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: TEXT_MUTED, marginTop: 2, letterSpacing: '0.06em' }}>{sub}</div>}
        </div>
        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 700, color: numColor, marginLeft: 'auto', opacity: 0.5 }}>{num}</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1 }}>
        {children}
      </div>
    </GlassCard>
  )
}

// ── Sidebar ────────────────────────────────────────────────────────────────────
const NAV_ITEMS = [
  { id: 'gtm',    icon: 'dashboard',     label: 'GTM Intelligence' },
  { id: 'sniper', icon: 'track_changes', label: 'Launch Sniper'    },
]

function Sidebar({ activeTab, setActiveTab, onHome }) {
  return (
    <div style={{
      width: 256, flexShrink: 0, height: '100vh', position: 'fixed', left: 0, top: 0,
      background: SURFACE, borderRight: `1px solid ${OUTLINE}30`,
      display: 'flex', flexDirection: 'column', padding: '24px 16px',
      zIndex: 100,
    }}>
      {/* Logo */}
      <div style={{ marginBottom: 28, padding: '0 8px', cursor: 'pointer' }} onClick={onHome}>
        <div style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 18, fontWeight: 800, color: CYAN_LIGHT, lineHeight: 1 }}>
          DealSignal
        </div>
        <Label color={TEXT_MUTED} style={{ fontSize: 9, letterSpacing: '0.2em', marginTop: 3 }}>Strategic Intelligence</Label>
      </div>

      {/* Nav items */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
        {NAV_ITEMS.map(item => {
          const active = activeTab === item.id
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              style={{
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '10px 12px', borderRadius: 10, border: 'none',
                background: active ? `${CYAN}14` : 'transparent',
                borderRight: active ? `3px solid ${CYAN}` : '3px solid transparent',
                color: active ? CYAN_LIGHT : TEXT_MUTED,
                fontFamily: "'Inter', sans-serif", fontSize: 13, fontWeight: active ? 600 : 400,
                cursor: 'pointer', transition: 'all 0.15s', textAlign: 'left',
              }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'rgba(255,255,255,0.03)' }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent' }}
            >
              <Icon name={item.icon} filled={active} style={{ fontSize: 18, flexShrink: 0 }} />
              <span>{item.label}</span>
            </button>
          )
        })}
      </div>

      {/* Bottom links */}
      <div style={{ borderTop: `1px solid ${OUTLINE}33`, paddingTop: 16, display: 'flex', flexDirection: 'column', gap: 4 }}>
        <a
          href="http://localhost:8000/docs" target="_blank" rel="noreferrer"
          style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderRadius: 8, color: TEXT_MUTED, textDecoration: 'none', fontSize: 12, fontFamily: "'Inter', sans-serif" }}
          onMouseEnter={e => (e.currentTarget.style.color = TEXT)} onMouseLeave={e => (e.currentTarget.style.color = TEXT_MUTED)}
        >
          <Icon name="api" style={{ fontSize: 16 }} />
          API Docs
        </a>
        <button onClick={onHome} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderRadius: 8, color: TEXT_MUTED, background: 'none', border: 'none', fontSize: 12, fontFamily: "'Inter', sans-serif", cursor: 'pointer', textAlign: 'left' }}
          onMouseEnter={e => (e.currentTarget.style.color = TEXT)} onMouseLeave={e => (e.currentTarget.style.color = TEXT_MUTED)}
        >
          <Icon name="home" style={{ fontSize: 16 }} />
          Back to Home
        </button>
      </div>
    </div>
  )
}

// LandingPage is imported from './LandingPage' — see top of file.
// The function below is the old inline version, kept only for reference
// during transition. It is never called.

/* eslint-disable-next-line no-unused-vars */
function _LandingPageLegacy({ onEnterDashboard }) {
  const phrases = []
  const [phrase, setPhrase] = useState(0)
  useEffect(() => {}, [])

  return (
    <div style={{ background: BG, color: TEXT, minHeight: '100vh', fontFamily: "'Inter', sans-serif" }}>

      {/* NAV */}
      <nav style={{
        position: 'sticky', top: 0, zIndex: 50,
        background: `${SURFACE}cc`, backdropFilter: 'blur(20px)',
        borderBottom: `1px solid ${OUTLINE}33`, height: 64,
      }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 24px', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 18, fontWeight: 800, color: CYAN_LIGHT }}>
            DealSignal
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 32 }}>
            {['Solutions', 'Intelligence', 'Pricing'].map(link => (
              <a key={link} href="#" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 700, letterSpacing: '0.12em', color: TEXT_MUTED, textDecoration: 'none', textTransform: 'uppercase', transition: 'color 0.2s' }}
                onMouseEnter={e => (e.target.style.color = TEXT)} onMouseLeave={e => (e.target.style.color = TEXT_MUTED)}
              >{link}</a>
            ))}
          </div>
          <button
            onClick={onEnterDashboard}
            style={{
              padding: '8px 20px', borderRadius: 8, border: 'none',
              background: CYAN, color: '#001a1c',
              fontFamily: "'Hanken Grotesk', sans-serif", fontWeight: 700, fontSize: 13,
              cursor: 'pointer', transition: 'all 0.2s',
              boxShadow: `0 0 20px ${CYAN}44`,
            }}
            onMouseEnter={e => { e.target.style.background = CYAN_LIGHT; e.target.style.boxShadow = `0 0 30px ${CYAN}66` }}
            onMouseLeave={e => { e.target.style.background = CYAN; e.target.style.boxShadow = `0 0 20px ${CYAN}44` }}
          >
            Open Dashboard
          </button>
        </div>
      </nav>

      {/* HERO */}
      <header style={{
        minHeight: '88vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        position: 'relative', overflow: 'hidden', padding: '0 24px',
        backgroundImage: `radial-gradient(${CYAN}18 1px, transparent 1px)`,
        backgroundSize: '32px 32px',
      }}>
        {/* Glow blobs */}
        <div style={{ position: 'absolute', top: '20%', left: '10%', width: 400, height: 400, background: `${CYAN}08`, borderRadius: '50%', filter: 'blur(80px)', pointerEvents: 'none' }} />
        <div style={{ position: 'absolute', bottom: '10%', right: '5%', width: 320, height: 320, background: `${PURPLE}10`, borderRadius: '50%', filter: 'blur(80px)', pointerEvents: 'none' }} />

        <motion.div
          initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: 'easeOut' }}
          style={{ textAlign: 'center', maxWidth: 860, position: 'relative', zIndex: 2 }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, marginBottom: 28 }}>
            <Icon name="radar" filled style={{ color: CYAN, fontSize: 18 }} />
            <Label color={CYAN}>Ghost Pipeline Detector + Launch Sniper</Label>
          </div>

          <h1 style={{
            fontFamily: "'Hanken Grotesk', sans-serif",
            fontSize: 'clamp(36px, 6vw, 72px)', fontWeight: 800,
            lineHeight: 1.05, letterSpacing: '-0.03em', color: TEXT, marginBottom: 24,
          }}>
            See the deals your competitors close{' '}
            <span style={{ color: CYAN_LIGHT, textDecoration: 'underline', textDecorationColor: `${CYAN}44` }}>before</span>{' '}
            they announce them.
          </h1>

          <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 18, color: TEXT_SUB, maxWidth: 600, margin: '0 auto 40px', lineHeight: 1.7 }}>
            Synthesize raw digital breadcrumbs into high-fidelity competitive intelligence. Stop reacting to press releases — start intercepting the signals.
          </p>

          <div style={{ display: 'flex', gap: 16, justifyContent: 'center', flexWrap: 'wrap' }}>
            <button
              onClick={onEnterDashboard}
              style={{
                padding: '14px 32px', borderRadius: 10, border: 'none',
                background: CYAN, color: '#001a1c',
                fontFamily: "'Hanken Grotesk', sans-serif", fontWeight: 700, fontSize: 16,
                cursor: 'pointer', boxShadow: `0 0 30px ${CYAN}44`,
                transition: 'all 0.2s',
              }}
              onMouseEnter={e => { e.target.style.transform = 'scale(1.04)'; e.target.style.boxShadow = `0 0 44px ${CYAN}66` }}
              onMouseLeave={e => { e.target.style.transform = 'scale(1)'; e.target.style.boxShadow = `0 0 30px ${CYAN}44` }}
            >
              Start tracking competitors
            </button>
            <button style={{
              padding: '14px 32px', borderRadius: 10,
              border: `1px solid ${CYAN_LIGHT}44`, background: 'transparent',
              color: CYAN_LIGHT,
              fontFamily: "'Hanken Grotesk', sans-serif", fontWeight: 600, fontSize: 16,
              cursor: 'pointer', transition: 'all 0.2s',
              display: 'flex', alignItems: 'center', gap: 8,
            }}
              onMouseEnter={e => (e.currentTarget.style.background = `${CYAN}0a`)}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <Icon name="play_circle" style={{ fontSize: 20 }} /> Watch demo
            </button>
          </div>
        </motion.div>
      </header>

      {/* GHOST PIPELINE section */}
      <section style={{ padding: '96px 24px', background: `${SURFACE}` }}>
        <div style={{ maxWidth: 1100, margin: '0 auto', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 64, alignItems: 'center' }}>

          {/* Signal cards */}
          <div style={{
            background: 'rgba(22,24,33,0.9)', backdropFilter: 'blur(20px)',
            border: `0.5px solid rgba(255,255,255,0.09)`, borderRadius: 20, padding: 32,
            boxShadow: `0 0 20px ${CYAN}18`,
          }}>
            {[
              { icon: 'verified_user', color: CYAN_LIGHT, label: 'SSL CERTIFICATE ISSUED', detail: 'new-enterprise-checkout.competitor.com', timing: 'T-MINUS 12D' },
              { icon: 'dns',          color: PURPLE_LT,   label: 'SUBDOMAIN DETECTED',    detail: 'v3-api-docs.competitor.io',           timing: 'T-MINUS 8D'  },
              { icon: 'terminal',     color: GREEN,        label: 'GITHUB REPO SPIKE',     detail: 'Auth module refactor (Private)',       timing: 'T-MINUS 2D'  },
            ].map((s, i) => (
              <div key={i}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '16px 0' }}>
                  <Icon name={s.icon} filled style={{ color: s.color, fontSize: 22, flexShrink: 0 }} />
                  <div style={{ flex: 1 }}>
                    <Label color={s.color}>{s.label}</Label>
                    <div style={{ fontFamily: "'Inter', sans-serif", fontSize: 13, color: TEXT_SUB, marginTop: 2 }}>{s.detail}</div>
                  </div>
                  <Label color={s.color} style={{ fontSize: 9 }}>{s.timing}</Label>
                </div>
                {i < 2 && <div style={{ height: 1, background: `${PURPLE}44`, backgroundImage: `radial-gradient(circle, ${PURPLE} 1px, transparent 1px)`, backgroundSize: '8px 8px' }} />}
              </div>
            ))}
          </div>

          {/* Description */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <Icon name="radar" filled style={{ color: CYAN, fontSize: 18 }} />
              <Label color={CYAN}>Ghost Pipeline Detector</Label>
            </div>
            <h2 style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 32, fontWeight: 800, color: TEXT, letterSpacing: '-0.02em', marginBottom: 16, lineHeight: 1.2 }}>
              Detect Closed Deals Before Announcements
            </h2>
            <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 16, color: TEXT_SUB, lineHeight: 1.7, marginBottom: 24 }}>
              Your competitors leave digital breadcrumbs everywhere. DealSignal tracks infrastructure shifts that signal won enterprise contracts weeks before the case studies go live.
            </p>
            <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {['Certificate Transparency (CT) Monitoring', 'Reverse DNS & Subdomain Harvesting', 'Tech Stack Beacon Fingerprinting'].map(item => (
                <li key={item} style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                  <Icon name="check_circle" filled style={{ color: CYAN, fontSize: 16, marginTop: 2, flexShrink: 0 }} />
                  <span style={{ fontFamily: "'Inter', sans-serif", fontSize: 14, color: TEXT_SUB }}>{item}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      {/* LAUNCH SNIPER section */}
      <section style={{ padding: '96px 24px', background: BG, overflow: 'hidden' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 64, alignItems: 'center' }}>

          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <Icon name="track_changes" filled style={{ color: GREEN, fontSize: 18 }} />
              <Label color={GREEN}>Launch Sniper</Label>
            </div>
            <h2 style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 32, fontWeight: 800, color: TEXT, letterSpacing: '-0.02em', marginBottom: 16, lineHeight: 1.2 }}>
              Predict Competitor Launches 45 Days Out
            </h2>
            <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 16, color: TEXT_SUB, lineHeight: 1.7, marginBottom: 24 }}>
              We don't just tell you they launched. We tell you what they're building, who their pilot customers are, and how they're positioning it.
            </p>

            {/* Intelligence brief */}
            <div style={{
              background: 'rgba(22,24,33,0.9)', backdropFilter: 'blur(20px)',
              border: `1px solid ${OUTLINE}55`, borderLeft: `4px solid ${CYAN}`,
              borderRadius: 12, padding: 20, marginTop: 24,
            }}>
              <Label color={CYAN} style={{ display: 'block', marginBottom: 10 }}>Intelligence Brief</Label>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color: TEXT_SUB, overflow: 'hidden', whiteSpace: 'nowrap' }}>
                {phrases[phrase]}
              </div>
            </div>
          </div>

          {/* Countdown sphere */}
          <div style={{ display: 'flex', justifyContent: 'center' }}>
            <div style={{
              width: 280, height: 280, borderRadius: '50%',
              background: 'rgba(22,24,33,0.9)', backdropFilter: 'blur(20px)',
              border: `1px solid ${CYAN}28`,
              display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
              position: 'relative', overflow: 'hidden',
              boxShadow: `0 0 60px ${CYAN}12, inset 0 0 60px ${CYAN}06`,
            }}>
              <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', border: `1px solid ${CYAN}18`, animation: 'ping 2s ease-in-out infinite' }} />
              <div style={{ textAlign: 'center', position: 'relative', zIndex: 1 }}>
                <Icon name="rocket_launch" style={{ fontSize: 32, color: TEXT_MUTED, display: 'block', margin: '0 auto 12px' }} />
                <Label color={TEXT_MUTED} style={{ display: 'block', marginBottom: 8 }}>T-MINUS</Label>
                <div style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 32, fontWeight: 800, color: CYAN_LIGHT, letterSpacing: '-0.02em' }}>
                  45:12:08
                </div>
                <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 12, color: TEXT_MUTED, marginTop: 12 }}>
                  Estimated Launch: Q4 Enterprise
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* CTA section */}
      <section style={{ padding: '80px 24px', background: SURFACE }}>
        <div style={{ maxWidth: 800, margin: '0 auto', textAlign: 'center' }}>
          {/* Social proof logos */}
          <div style={{ display: 'flex', justifyContent: 'center', gap: 40, marginBottom: 64, opacity: 0.35, flexWrap: 'wrap' }}>
            {['STRIPE', 'SALESFORCE', 'DATADOG', 'SNOWFLAKE', 'FIGMA'].map(co => (
              <span key={co} style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 18, fontWeight: 700, color: TEXT_MUTED }}>{co}</span>
            ))}
          </div>

          {/* Testimonial */}
          <blockquote style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 24, fontWeight: 700, color: TEXT, lineHeight: 1.4, fontStyle: 'italic', margin: '0 0 24px' }}>
            "We caught a $2M deal moving to a competitor pilot that we never would've found manually."
          </blockquote>
          <p style={{ fontFamily: "'Inter', sans-serif", color: CYAN_LIGHT, fontWeight: 600, marginBottom: 4 }}>Sarah Jenkins</p>
          <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 13, color: TEXT_MUTED, marginBottom: 56 }}>VP of Sales Intelligence, Global Tech</p>

          {/* Final CTA card */}
          <GlassCard glow style={{ padding: '56px 40px' }}>
            <h2 style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 'clamp(28px, 4vw, 44px)', fontWeight: 800, letterSpacing: '-0.02em', color: TEXT, marginBottom: 16 }}>
              Ready to start intercepting?
            </h2>
            <p style={{ fontFamily: "'Inter', sans-serif", color: TEXT_MUTED, fontSize: 16, marginBottom: 32, lineHeight: 1.6 }}>
              See every competitor signal in one place. Real-time intelligence that closes deals.
            </p>
            <button
              onClick={onEnterDashboard}
              style={{
                padding: '14px 40px', borderRadius: 10, border: 'none',
                background: CYAN, color: '#001a1c',
                fontFamily: "'Hanken Grotesk', sans-serif", fontWeight: 700, fontSize: 16,
                cursor: 'pointer', boxShadow: `0 0 30px ${CYAN}44`,
              }}
              onMouseEnter={e => (e.target.style.transform = 'scale(1.04)')}
              onMouseLeave={e => (e.target.style.transform = 'scale(1)')}
            >
              Open the Dashboard →
            </button>
          </GlassCard>
        </div>
      </section>

    </div>
  )
}

// ── Dashboard wrapper ──────────────────────────────────────────────────────────

function DashboardApp({ onHome }) {
  const [activeTab, setActiveTab] = useState('gtm')
  const [toast, setToast] = useState('')
  const showToast = useCallback(m => setToast(m), [])

  return (
    <div style={{ display: 'flex', background: BG, minHeight: '100vh' }}>
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} onHome={onHome} />
      <main style={{ marginLeft: 256, flex: 1, minHeight: '100vh', overflowY: 'auto', background: BG }}>
        {activeTab === 'gtm'
          ? <GTMDashboard showToast={showToast} />
          : <SniperDashboard showToast={showToast} />
        }
      </main>
      <Toast msg={toast} onClose={() => setToast('')} />
    </div>
  )
}

// ── Root ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [view, setView] = useState('landing')

  return view === 'landing'
    ? <LandingPage onEnterDashboard={() => setView('dashboard')} />
    : <DashboardApp onHome={() => setView('landing')} />
}
