/**
 * LandingPage.jsx
 *
 * Structure:
 *   1. <HeroScreen>     — 100vh, HLS video bg, liquid-glass nav, email CTA
 *                         (personalized for Deal Intelligence)
 *   2. <GhostPipeline>  — original scrolling section
 *   3. <LaunchSniper>   — original scrolling section
 *   4. <CTASection>     — original scrolling section
 *
 * The root <main> has no overflow-hidden so sections 2-4 scroll naturally.
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { Globe, ArrowRight, Check } from 'lucide-react'
import Hls from 'hls.js'

const HLS_URL =
  'https://stream.mux.com/kimF2ha9zLrX64H00UgLGPflCzNtl1T0215MlAmeOztv8.m3u8'

// ── Design tokens (mirrors App.jsx) ───────────────────────────────────────────
const BG        = '#0a0b10'
const SURFACE   = '#11131c'
const SURF_MID  = '#1d1f28'
const SURF_HIGH = '#282933'
const OUTLINE   = '#3a494b'
const CYAN      = '#00dbe7'
const CYAN_LT   = '#74f5ff'
const PURPLE    = '#6f00be'
const PURPLE_LT = '#ddb7ff'
const GREEN     = '#4ae176'
const TEXT      = '#e1e1ee'
const TEXT_SUB  = '#b9cacb'
const TEXT_MUTED= '#849495'

// ── Small helpers ──────────────────────────────────────────────────────────────
function Icon({ name, filled = false, style = {} }) {
  return (
    <span
      className="material-symbols-outlined"
      style={{
        fontVariationSettings: `'FILL' ${filled ? 1 : 0}`,
        userSelect: 'none',
        lineHeight: 1,
        ...style,
      }}
    >
      {name}
    </span>
  )
}

function Label({ children, color = CYAN, style = {} }) {
  return (
    <span style={{
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: 10, fontWeight: 700, letterSpacing: '0.12em',
      textTransform: 'uppercase', color,
      ...style,
    }}>
      {children}
    </span>
  )
}

function GlassCard({ children, glow = false, style = {} }) {
  return (
    <div style={{
      background: 'rgba(22,24,33,0.88)',
      backdropFilter: 'blur(20px)',
      WebkitBackdropFilter: 'blur(20px)',
      border: '0.5px solid rgba(255,255,255,0.09)',
      borderRadius: 16,
      ...(glow ? { boxShadow: `0 0 20px ${CYAN}18` } : {}),
      ...style,
    }}>
      {children}
    </div>
  )
}

// ── Typewriter hook ────────────────────────────────────────────────────────────
function useTypewriter(text, speed = 60, active = false) {
  const [displayed, setDisplayed] = useState('')
  const timerRef = useRef(null)

  useEffect(() => {
    clearInterval(timerRef.current)
    if (!active) { setDisplayed(''); return }
    let i = 0
    setDisplayed('')
    timerRef.current = setInterval(() => {
      i++
      setDisplayed(text.slice(0, i))
      if (i >= text.length) clearInterval(timerRef.current)
    }, speed)
    return () => clearInterval(timerRef.current)
  }, [text, speed, active])

  return displayed
}

// ── Background Video ───────────────────────────────────────────────────────────
function BackgroundVideo() {
  const videoRef = useRef(null)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = HLS_URL
      return
    }
    if (Hls.isSupported()) {
      const hls = new Hls({ startLevel: -1 })
      hls.loadSource(HLS_URL)
      hls.attachMedia(video)
      return () => hls.destroy()
    }
  }, [])

  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      <video
        ref={videoRef}
        autoPlay muted loop playsInline
        className="w-full h-full object-cover opacity-100"
      />
    </div>
  )
}

// ── Navbar ─────────────────────────────────────────────────────────────────────
function Navbar({ onEnterDashboard }) {
  return (
    <motion.nav
      className="relative z-20 px-6 py-6 w-full"
      initial={{ y: -20, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="liquid-glass rounded-full px-6 py-3 flex items-center justify-between max-w-5xl mx-auto">

        {/* Left — logo + links */}
        <div className="flex items-center gap-8">
          <div className="flex items-center gap-2">
            <Globe className="w-5 h-5 text-white" />
            <span className="text-white font-semibold text-base tracking-tight"
              style={{ fontFamily: "'Inter', sans-serif" }}>
              GTM Intel
            </span>
          </div>
          <div className="hidden md:flex items-center gap-8 text-white/70 text-sm font-medium"
            style={{ fontFamily: "'Inter', sans-serif" }}>
            {['Signals', 'Intelligence', 'Pipeline'].map(link => (
              <a key={link} href="#"
                className="hover:text-white transition-colors duration-300">
                {link}
              </a>
            ))}
          </div>
        </div>

        {/* Right — actions */}
        <div className="flex items-center gap-4">
          <button
            onClick={onEnterDashboard}
            className="liquid-glass rounded-full px-5 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity cursor-pointer"
            style={{ fontFamily: "'Inter', sans-serif" }}
          >
            Open Dashboard
          </button>
        </div>
      </div>
    </motion.nav>
  )
}

// ── Hero (first screen content) ────────────────────────────────────────────────
function HeroContent({ onEnterDashboard }) {
  const [showForm,  setShowForm]  = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [email,     setEmail]     = useState('')

  useEffect(() => {
    if (!submitted) return
    const t = setTimeout(() => {
      setShowForm(false); setSubmitted(false); setEmail('')
    }, 4000)
    return () => clearTimeout(t)
  }, [submitted])

  const placeholderText = submitted
    ? 'You are on the list — we will notify you shortly'
    : 'Enter your work email for early access'

  const typedPlaceholder = useTypewriter(placeholderText, 55, showForm)

  const handleSubmit = useCallback((e) => {
    e.preventDefault()
    if (!email.trim()) return
    setSubmitted(true)
  }, [email])

  return (
    <section className="relative flex-1 flex flex-col items-center justify-center px-6">
      <div className="relative z-10 text-center max-w-5xl mx-auto flex flex-col items-center justify-center w-full gap-12">

        {/* Tagline */}
        <motion.p
          className="text-white/75 text-[10px] md:text-[11px] font-medium tracking-[0.22em] uppercase mb-4"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          style={{ fontFamily: "'JetBrains Mono', monospace" }}
        >
          REAL-TIME B2B COMPETITIVE INTELLIGENCE
        </motion.p>

        {/* Heading */}
        <motion.h1
          style={{ fontFamily: "'Instrument Serif', serif" }}
          className="text-4xl md:text-[64px] font-medium tracking-[-0.01em] leading-[1.1] mb-6 bg-gradient-to-b from-white via-white/95 to-white/70 bg-clip-text text-transparent max-w-4xl"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
        >
          See the deals your rivals close
          <br className="hidden md:block" />
          before they announce them.
        </motion.h1>

        {/* Sub-description */}
        <motion.p
          className="text-white/55 text-[14px] md:text-[15px] font-normal leading-relaxed max-w-xl -mt-6"
          style={{ fontFamily: "'Inter', sans-serif" }}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25, duration: 0.8 }}
        >
          Turn CT logs, DNS mutations and GitHub spikes into high-fidelity deal
          intelligence — weeks before the press release.
        </motion.p>

        {/* CTA */}
        <motion.div
          className="min-h-[50px] mt-2 flex items-center justify-center"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
        >
          <AnimatePresence mode="wait">
            {!showForm ? (
              <motion.button
                key="cta-button"
                onClick={() => setShowForm(true)}
                initial={{ scale: 0.95, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.95, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="px-10 py-3 text-[14px] font-medium border border-white/10 rounded-full hover:border-white/30 hover:bg-white/[0.02] transition-all duration-300 text-white/90 backdrop-blur-sm cursor-pointer"
                style={{ fontFamily: "'Inter', sans-serif" }}
              >
                Get early access
              </motion.button>
            ) : (
              <motion.form
                key="cta-form"
                onSubmit={handleSubmit}
                initial={{ scale: 0.95, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.95, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="flex items-center gap-2 pl-5 pr-1.5 py-1.5 text-[14px] font-medium border border-white/20 rounded-full bg-white/[0.02] backdrop-blur-sm w-full max-w-[340px] focus-within:border-white/40 transition-colors duration-300"
              >
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder={typedPlaceholder}
                  autoFocus
                  className="landing-email-input bg-transparent text-white flex-1 outline-none text-[13px] min-w-0"
                  style={{ fontFamily: "'Inter', sans-serif" }}
                />
                <button
                  type="submit"
                  className="glass-pill w-8 h-8 flex items-center justify-center text-white flex-shrink-0 hover:bg-white/10 transition-colors"
                >
                  {submitted
                    ? <Check className="w-4 h-4" strokeWidth={2.5} />
                    : <ArrowRight className="w-4 h-4" strokeWidth={2} />}
                </button>
              </motion.form>
            )}
          </AnimatePresence>
        </motion.div>

        {/* Open Dashboard link */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.8 }}
        >
          <button
            onClick={onEnterDashboard}
            className="text-white/60 hover:text-white/30 transition-colors duration-300 text-[13px] font-medium tracking-wide cursor-pointer flex items-center gap-1.5"
            style={{ fontFamily: "'Inter', sans-serif" }}
          >
            Open Dashboard <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </motion.div>

      </div>
    </section>
  )
}

// ── HERO SCREEN (first 100vh) ──────────────────────────────────────────────────
function HeroScreen({ onEnterDashboard }) {
  return (
    <div className="relative h-screen w-full flex flex-col overflow-hidden">
      <BackgroundVideo />
      <Navbar onEnterDashboard={onEnterDashboard} />
      <HeroContent onEnterDashboard={onEnterDashboard} />
    </div>
  )
}

// ── SCROLLING SECTIONS (unchanged) ────────────────────────────────────────────

function GhostPipelineSection() {
  return (
    <section style={{ padding: '96px 24px', background: SURFACE }}>
      <div style={{ maxWidth: 1100, margin: '0 auto', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 64, alignItems: 'center' }}>

        {/* Signal card */}
        <div style={{
          background: 'rgba(22,24,33,0.9)', backdropFilter: 'blur(20px)',
          border: '0.5px solid rgba(255,255,255,0.09)', borderRadius: 20, padding: 32,
          boxShadow: `0 0 20px ${CYAN}18`,
        }}>
          {[
            { icon: 'verified_user', color: CYAN_LT,   label: 'SSL CERTIFICATE ISSUED',  detail: 'new-enterprise-checkout.competitor.com', timing: 'T-MINUS 12D' },
            { icon: 'dns',           color: PURPLE_LT, label: 'SUBDOMAIN DETECTED',       detail: 'v3-api-docs.competitor.io',              timing: 'T-MINUS 8D'  },
            { icon: 'terminal',      color: GREEN,      label: 'GITHUB REPO SPIKE',        detail: 'Auth module refactor (Private)',          timing: 'T-MINUS 2D'  },
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
              {i < 2 && (
                <div style={{
                  height: 1,
                  backgroundImage: `radial-gradient(circle, ${PURPLE} 1px, transparent 1px)`,
                  backgroundSize: '8px 8px',
                  opacity: 0.4,
                }} />
              )}
            </div>
          ))}
        </div>

        {/* Description */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <Icon name="radar" filled style={{ color: CYAN, fontSize: 18 }} />
            <Label color={CYAN}>Ghost Pipeline Detector</Label>
          </div>
          <h2 style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 'clamp(24px, 3vw, 32px)', fontWeight: 800, color: TEXT, letterSpacing: '-0.02em', marginBottom: 16, lineHeight: 1.2 }}>
            Detect Closed Deals Before Announcements
          </h2>
          <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 16, color: TEXT_SUB, lineHeight: 1.75, marginBottom: 24 }}>
            Your competitors leave digital breadcrumbs everywhere. GTM Intel tracks infrastructure shifts that signal won enterprise contracts weeks before the case studies go live.
          </p>
          <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[
              'Certificate Transparency (CT) Monitoring',
              'Reverse DNS & Subdomain Harvesting',
              'Tech Stack Beacon Fingerprinting',
            ].map(item => (
              <li key={item} style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                <Icon name="check_circle" filled style={{ color: CYAN, fontSize: 16, marginTop: 2, flexShrink: 0 }} />
                <span style={{ fontFamily: "'Inter', sans-serif", fontSize: 14, color: TEXT_SUB }}>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  )
}

function LaunchSniperSection() {
  const phrases = [
    'Analyzing competitor beta pricing models…',
    'Detecting subdomain registrations…',
    'Monitoring trademark filings…',
    'Scanning robots.txt changes…',
  ]
  const [phrase, setPhrase] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setPhrase(p => (p + 1) % phrases.length), 3500)
    return () => clearInterval(t)
  }, [])

  return (
    <section style={{ padding: '96px 24px', background: BG, overflow: 'hidden' }}>
      <div style={{ maxWidth: 1100, margin: '0 auto', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 64, alignItems: 'center' }}>

        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <Icon name="track_changes" filled style={{ color: GREEN, fontSize: 18 }} />
            <Label color={GREEN}>Launch Sniper</Label>
          </div>
          <h2 style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 'clamp(24px, 3vw, 32px)', fontWeight: 800, color: TEXT, letterSpacing: '-0.02em', marginBottom: 16, lineHeight: 1.2 }}>
            Predict Competitor Launches 45 Days Out
          </h2>
          <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 16, color: TEXT_SUB, lineHeight: 1.75, marginBottom: 24 }}>
            We don't just tell you they launched. We tell you what they're building, who their pilot customers are, and how they're positioning it against you.
          </p>

          {/* Intelligence brief */}
          <div style={{
            background: 'rgba(22,24,33,0.9)', backdropFilter: 'blur(20px)',
            border: `1px solid ${OUTLINE}55`, borderLeft: `4px solid ${CYAN}`,
            borderRadius: 12, padding: 20,
          }}>
            <Label color={CYAN} style={{ display: 'block', marginBottom: 10 }}>Intelligence Brief</Label>
            <div style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 12, color: TEXT_SUB,
              overflow: 'hidden', whiteSpace: 'nowrap',
            }}>
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
            <div style={{
              position: 'absolute', inset: 0, borderRadius: '50%',
              border: `1px solid ${CYAN}18`,
              animation: 'ping 2s ease-in-out infinite',
            }} />
            <div style={{ textAlign: 'center', position: 'relative', zIndex: 1 }}>
              <Icon name="rocket_launch" style={{ fontSize: 32, color: TEXT_MUTED, display: 'block', marginBottom: 12 }} />
              <Label color={TEXT_MUTED} style={{ display: 'block', marginBottom: 8 }}>T-MINUS</Label>
              <div style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 32, fontWeight: 800, color: CYAN_LT, letterSpacing: '-0.02em' }}>
                45:12:08
              </div>
              <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 12, color: TEXT_MUTED, marginTop: 12 }}>
                Estimated Launch: Q4 Enterprise Tier
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

function CTASection({ onEnterDashboard }) {
  return (
    <section style={{ padding: '80px 24px', background: SURFACE }}>
      <div style={{ maxWidth: 800, margin: '0 auto', textAlign: 'center' }}>

        {/* Social proof logos */}
        <div style={{ display: 'flex', justifyContent: 'center', gap: 40, marginBottom: 64, opacity: 0.3, flexWrap: 'wrap' }}>
          {['STRIPE', 'SALESFORCE', 'DATADOG', 'SNOWFLAKE', 'FIGMA'].map(co => (
            <span key={co} style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 17, fontWeight: 700, color: TEXT_MUTED }}>
              {co}
            </span>
          ))}
        </div>

        {/* Testimonial */}
        <blockquote style={{ fontFamily: "'Instrument Serif', serif", fontSize: 'clamp(18px, 2.5vw, 24px)', color: TEXT, lineHeight: 1.45, fontStyle: 'italic', margin: '0 0 20px' }}>
          "We caught a $2M deal moving to a competitor pilot that we never would've found manually. GTM Intel is our unfair advantage."
        </blockquote>
        <p style={{ fontFamily: "'Inter', sans-serif", fontWeight: 600, color: CYAN_LT, marginBottom: 4 }}>
          Sarah Jenkins
        </p>
        <p style={{ fontFamily: "'Inter', sans-serif", fontSize: 13, color: TEXT_MUTED, marginBottom: 56 }}>
          VP of Sales Intelligence, Global Tech
        </p>

        {/* Final CTA card */}
        <GlassCard glow style={{ padding: '56px 40px' }}>
          <h2 style={{ fontFamily: "'Hanken Grotesk', sans-serif", fontSize: 'clamp(26px, 4vw, 44px)', fontWeight: 800, letterSpacing: '-0.02em', color: TEXT, marginBottom: 16 }}>
            Ready to start intercepting?
          </h2>
          <p style={{ fontFamily: "'Inter', sans-serif", color: TEXT_MUTED, fontSize: 16, marginBottom: 32, lineHeight: 1.65 }}>
            Every deal signal in one place. Real-time competitive intelligence that puts your team on the front foot — not the back.
          </p>
          <button
            onClick={onEnterDashboard}
            style={{
              padding: '14px 40px', borderRadius: 10, border: 'none',
              background: CYAN, color: '#001a1c',
              fontFamily: "'Hanken Grotesk', sans-serif", fontWeight: 700, fontSize: 16,
              cursor: 'pointer',
              boxShadow: `0 0 30px ${CYAN}44`,
              transition: 'transform 0.2s, box-shadow 0.2s',
            }}
            onMouseEnter={e => { e.currentTarget.style.transform = 'scale(1.04)'; e.currentTarget.style.boxShadow = `0 0 44px ${CYAN}66` }}
            onMouseLeave={e => { e.currentTarget.style.transform = 'scale(1)'; e.currentTarget.style.boxShadow = `0 0 30px ${CYAN}44` }}
          >
            Open the Dashboard →
          </button>
        </GlassCard>
      </div>
    </section>
  )
}

// ── Root export ────────────────────────────────────────────────────────────────
export default function LandingPage({ onEnterDashboard }) {
  return (
    <main
      className="relative bg-black w-screen selection:bg-white selection:text-black"
      style={{ overflowX: 'hidden' }}
    >
      {/* ① Full-screen hero — 100vh, no internal scroll */}
      <HeroScreen onEnterDashboard={onEnterDashboard} />

      {/* ② Scrolling sections below */}
      <GhostPipelineSection />
      <LaunchSniperSection />
      <CTASection onEnterDashboard={onEnterDashboard} />
    </main>
  )
}
