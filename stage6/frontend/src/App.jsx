import { useState, useEffect, useCallback } from 'react'
import axios from 'axios'

const API = '/api'

// ── Helpers ───────────────────────────────────────────────────────────────────

const TIER_STYLES = {
  high:   'bg-red-100 text-red-800 border border-red-200',
  medium: 'bg-amber-100 text-amber-800 border border-amber-200',
  low:    'bg-gray-100 text-gray-600 border border-gray-200',
}

function TierBadge({ tier }) {
  const t = (tier || 'low').toLowerCase()
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-semibold ${TIER_STYLES[t] || TIER_STYLES.low}`}>
      {t.toUpperCase()}
    </span>
  )
}

function StatCard({ label, value, sub }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
      <p className="text-xs font-medium text-gray-400 uppercase tracking-wider">{label}</p>
      <p className="text-3xl font-bold text-gray-900 mt-1">{value ?? '—'}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  )
}

function Spinner() {
  return (
    <div className="flex items-center justify-center py-16">
      <div className="w-8 h-8 border-4 border-gray-200 border-t-indigo-500 rounded-full animate-spin" />
    </div>
  )
}

function Toast({ msg, onClose }) {
  useEffect(() => {
    if (!msg) return
    const t = setTimeout(onClose, 5000)
    return () => clearTimeout(t)
  }, [msg, onClose])

  if (!msg) return null
  const isErr = msg.startsWith('Error')
  return (
    <div className={`fixed bottom-6 right-6 z-50 max-w-sm rounded-lg shadow-lg px-4 py-3 text-sm
      ${isErr ? 'bg-red-50 border border-red-200 text-red-800' : 'bg-green-50 border border-green-200 text-green-800'}`}>
      <div className="flex items-start gap-2">
        <span className="flex-1">{msg}</span>
        <button onClick={onClose} className="ml-2 opacity-60 hover:opacity-100">✕</button>
      </div>
    </div>
  )
}


// ── Main App ──────────────────────────────────────────────────────────────────

export default function App() {
  const [stats,      setStats]      = useState(null)
  const [deals,      setDeals]      = useState([])
  const [loading,    setLoading]    = useState(false)
  const [tierFilter, setTierFilter] = useState('')
  const [selected,   setSelected]   = useState(null)   // expanded row

  // Forms
  const [runForm,    setRunForm]    = useState({ domains: '', window_days: 30, github_orgs: '' })
  const [digestForm, setDigestForm] = useState({ to_email: '', tier_filter: 'high' })
  const [ingestFile, setIngestFile] = useState('acme.json')

  const [toast, setToast] = useState('')

  const showToast = (msg) => setToast(msg)

  // ── Data fetching ───────────────────────────────────────────────────────────

  const fetchStats = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/stats`)
      setStats(r.data)
    } catch { /* ignore */ }
  }, [])

  const fetchDeals = useCallback(async () => {
    setLoading(true)
    try {
      const params = {}
      if (tierFilter) params.tier = tierFilter
      const r = await axios.get(`${API}/intelligence`, { params })
      setDeals(r.data)
    } catch (e) {
      showToast(`Error fetching deals: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }, [tierFilter])

  useEffect(() => { fetchStats(); fetchDeals() }, [fetchStats, fetchDeals])

  // Poll stats after pipeline run
  const pollStats = () => {
    let count = 0
    const id = setInterval(() => {
      fetchStats()
      if (++count >= 12) clearInterval(id)   // stop after ~60s
    }, 5000)
  }

  // ── Actions ─────────────────────────────────────────────────────────────────

  const handleRun = async () => {
    const domains = runForm.domains.split(',').map(s => s.trim()).filter(Boolean)
    if (!domains.length) { showToast('Error: enter at least one domain'); return }

    const orgs = runForm.github_orgs.split(',').map(s => s.trim()).filter(Boolean)
    try {
      const r = await axios.post(`${API}/run`, {
        target_domains: domains,
        github_orgs:    orgs,
        window_days:    Number(runForm.window_days),
        enrich:         true,
      })
      showToast(`✓ Pipeline started (run_id ${r.data.run_id.slice(0, 8)}…) — refreshing stats`)
      pollStats()
    } catch (e) {
      showToast(`Error: ${e.response?.data?.detail || e.message}`)
    }
  }

  const handleIngest = async () => {
    try {
      const r = await axios.post(`${API}/ingest?filename=${encodeURIComponent(ingestFile)}`)
      showToast(`✓ Ingested ${r.data.ingested} records (${r.data.skipped} skipped)`)
      fetchStats(); fetchDeals()
    } catch (e) {
      showToast(`Error: ${e.response?.data?.detail || e.message}`)
    }
  }

  const handleDigest = async () => {
    if (!digestForm.to_email) { showToast('Error: enter a recipient email'); return }
    try {
      const r = await axios.post(`${API}/digest/send`, digestForm)
      showToast(`✓ Digest sent to ${digestForm.to_email} — ${r.data.count} deals`)
    } catch (e) {
      showToast(`Error: ${e.response?.data?.detail || e.message}`)
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-gray-50">

      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold tracking-tight">📡 GTM Intelligence Platform</h1>
            <p className="text-xs text-gray-400">Signals → Clusters → AI Enrichment → Delivery</p>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={() => { fetchStats(); fetchDeals() }}
              className="text-xs text-gray-500 hover:text-gray-900 border border-gray-200
                         rounded-lg px-3 py-1.5 hover:bg-gray-50 transition">
              ↺ Refresh
            </button>
            <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer"
               className="text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-100
                          rounded-lg px-3 py-1.5 hover:bg-indigo-50 transition">
              API Docs ↗
            </a>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-8">

        {/* Stats row */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
          <StatCard label="Signals"      value={stats?.signals}      />
          <StatCard label="Clusters"     value={stats?.clusters}     />
          <StatCard label="Intelligence" value={stats?.intelligence} />
          <StatCard label="HIGH"  value={stats?.tiers?.high}   sub="priority deals" />
          <StatCard label="MED"   value={stats?.tiers?.medium} sub="watch list" />
          <StatCard label="AI Cost" value={stats ? `$${stats.total_ai_cost_usd.toFixed(4)}` : null} sub="gpt-4o-mini" />
        </div>

        {/* Deal intelligence table */}
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
          <div className="px-5 py-4 border-b border-gray-100 flex flex-wrap items-center gap-3">
            <h2 className="font-semibold text-sm flex-1">Deal Intelligence</h2>
            <div className="flex items-center gap-2">
              <label className="text-xs text-gray-400">Filter</label>
              <select
                className="text-sm border border-gray-200 rounded-lg px-2 py-1 bg-white"
                value={tierFilter}
                onChange={e => setTierFilter(e.target.value)}
              >
                <option value="">All tiers</option>
                <option value="high">HIGH</option>
                <option value="medium">MEDIUM</option>
                <option value="low">LOW</option>
              </select>
            </div>
          </div>

          {loading ? <Spinner /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-gray-400 text-xs uppercase tracking-wider">
                    {['Company', 'Vendor', 'Category', 'Confidence', 'Deal Close', 'Outreach', 'Tier', ''].map(h => (
                      <th key={h} className="px-4 py-2.5 text-left font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {deals.map(d => (
                    <>
                      <tr
                        key={d.intelligence_id}
                        className="hover:bg-gray-50 cursor-pointer transition"
                        onClick={() => setSelected(selected?.intelligence_id === d.intelligence_id ? null : d)}
                      >
                        <td className="px-4 py-3 font-semibold">{d.target_company}</td>
                        <td className="px-4 py-3">{d.suspected_vendor}</td>
                        <td className="px-4 py-3 text-gray-400 text-xs">{d.vendor_category}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <div className="w-16 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                              <div
                                className="h-full bg-indigo-500 rounded-full"
                                style={{ width: `${(d.confidence * 100).toFixed(0)}%` }}
                              />
                            </div>
                            <span className="text-gray-600">{(d.confidence * 100).toFixed(0)}%</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-gray-600">{d.deal_closed_estimate}</td>
                        <td className="px-4 py-3 text-gray-600">{d.outreach_window}</td>
                        <td className="px-4 py-3"><TierBadge tier={d.tier} /></td>
                        <td className="px-4 py-3 text-gray-300 text-xs">
                          {selected?.intelligence_id === d.intelligence_id ? '▲' : '▼'}
                        </td>
                      </tr>
                      {selected?.intelligence_id === d.intelligence_id && (
                        <tr key={`${d.intelligence_id}-expand`}>
                          <td colSpan={8} className="px-6 py-4 bg-indigo-50 border-t border-indigo-100">
                            <p className="text-xs font-semibold text-gray-500 uppercase mb-1">Reasoning</p>
                            <p className="text-sm text-gray-700 leading-relaxed mb-3">{d.reasoning}</p>
                            {d.signals?.length > 0 && (
                              <>
                                <p className="text-xs font-semibold text-gray-500 uppercase mb-1">Signals</p>
                                <ul className="list-disc list-inside space-y-0.5">
                                  {d.signals.map((s, i) => (
                                    <li key={i} className="text-xs text-gray-600">{s}</li>
                                  ))}
                                </ul>
                              </>
                            )}
                            {d.new_vendor_patterns?.length > 0 && (
                              <p className="text-xs text-indigo-600 mt-2">
                                New patterns: {d.new_vendor_patterns.map(p => `${p.pattern} → ${p.vendor}`).join(', ')}
                              </p>
                            )}
                          </td>
                        </tr>
                      )}
                    </>
                  ))}
                  {deals.length === 0 && (
                    <tr>
                      <td colSpan={8} className="px-4 py-12 text-center text-gray-400 text-sm">
                        No deals yet — ingest sample data or run the pipeline ↓
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Action panels */}
        <div className="grid md:grid-cols-3 gap-6">

          {/* Run pipeline */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 space-y-3">
            <div>
              <h3 className="font-semibold text-sm">Run Pipeline</h3>
              <p className="text-xs text-gray-400 mt-0.5">Stages 1–5 in background</p>
            </div>
            <input
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-indigo-300"
              placeholder="acme.com, rival.io"
              value={runForm.domains}
              onChange={e => setRunForm(f => ({ ...f, domains: e.target.value }))}
            />
            <input
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-indigo-300"
              placeholder="GitHub orgs (optional)"
              value={runForm.github_orgs}
              onChange={e => setRunForm(f => ({ ...f, github_orgs: e.target.value }))}
            />
            <div className="flex items-center gap-2">
              <label className="text-xs text-gray-500 w-24 flex-shrink-0">Window days</label>
              <input
                type="number" min={1} max={365}
                className="flex-1 border border-gray-200 rounded-lg px-3 py-1.5 text-sm
                           focus:outline-none focus:ring-2 focus:ring-indigo-300"
                value={runForm.window_days}
                onChange={e => setRunForm(f => ({ ...f, window_days: e.target.value }))}
              />
            </div>
            <button
              onClick={handleRun}
              className="w-full bg-gray-900 text-white rounded-lg py-2 text-sm font-medium
                         hover:bg-gray-700 transition"
            >
              ▶ Run Pipeline
            </button>
          </div>

          {/* Ingest sample */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 space-y-3">
            <div>
              <h3 className="font-semibold text-sm">Ingest Sample Data</h3>
              <p className="text-xs text-gray-400 mt-0.5">Load from stage6/sample_deals/</p>
            </div>
            <input
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-indigo-300"
              placeholder="filename.json"
              value={ingestFile}
              onChange={e => setIngestFile(e.target.value)}
            />
            <p className="text-xs text-gray-400">
              Drop any <code>.json</code> file into{' '}
              <code className="bg-gray-100 px-1 rounded">stage6/sample_deals/</code>{' '}
              then enter the filename above.
            </p>
            <button
              onClick={handleIngest}
              className="w-full bg-indigo-600 text-white rounded-lg py-2 text-sm font-medium
                         hover:bg-indigo-700 transition"
            >
              ↑ Ingest JSON
            </button>
          </div>

          {/* Send digest */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 space-y-3">
            <div>
              <h3 className="font-semibold text-sm">Email Digest</h3>
              <p className="text-xs text-gray-400 mt-0.5">Send via Resend (add RESEND_API_KEY)</p>
            </div>
            <input
              type="email"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-indigo-300"
              placeholder="you@company.com"
              value={digestForm.to_email}
              onChange={e => setDigestForm(f => ({ ...f, to_email: e.target.value }))}
            />
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white
                         focus:outline-none focus:ring-2 focus:ring-indigo-300"
              value={digestForm.tier_filter}
              onChange={e => setDigestForm(f => ({ ...f, tier_filter: e.target.value }))}
            >
              <option value="high">HIGH tier only</option>
              <option value="medium">MEDIUM tier</option>
              <option value="low">LOW tier</option>
            </select>
            <button
              onClick={handleDigest}
              className="w-full bg-blue-600 text-white rounded-lg py-2 text-sm font-medium
                         hover:bg-blue-700 transition"
            >
              ✉ Send Digest
            </button>
            <a
              href={`/api/digest/preview?tier_filter=${digestForm.tier_filter}`}
              target="_blank" rel="noreferrer"
              className="block text-center text-xs text-blue-500 hover:text-blue-700"
            >
              Preview HTML ↗
            </a>
          </div>

        </div>
      </main>

      <Toast msg={toast} onClose={() => setToast('')} />
    </div>
  )
}
