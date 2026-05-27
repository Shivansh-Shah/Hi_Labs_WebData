/**
 * SkewPanel — animated skew-card used for Pipeline Controls.
 * Same visual language as gradient-card-showcase; adapted for form content.
 *
 * Props:
 *   title       string  – card heading
 *   sub         string  – muted subtitle
 *   numLabel    string  – e.g. "01"
 *   numColor    string  – accent hex for the number
 *   gradientFrom string – start color of the skewed gradient panel
 *   gradientTo   string – end color
 *   children    ReactNode – form fields / buttons
 */
export function SkewPanel({ title, sub, numLabel, numColor, gradientFrom, gradientTo, children }) {
  return (
    <div className="group relative w-full min-h-[420px] flex flex-col transition-all duration-500">

      {/* Skewed solid gradient panel */}
      <span
        className="absolute top-0 left-[50px] w-1/2 h-full rounded-xl skew-x-[15deg]
                   transition-all duration-500
                   group-hover:skew-x-0 group-hover:left-5 group-hover:w-[calc(100%-40px)]"
        style={{ background: `linear-gradient(315deg, ${gradientFrom}, ${gradientTo})` }}
      />

      {/* Skewed blur / glow duplicate */}
      <span
        className="absolute top-0 left-[50px] w-1/2 h-full rounded-xl skew-x-[15deg] blur-[30px]
                   transition-all duration-500
                   group-hover:skew-x-0 group-hover:left-5 group-hover:w-[calc(100%-40px)]"
        style={{ background: `linear-gradient(315deg, ${gradientFrom}, ${gradientTo})` }}
      />

      {/* Floating blob squares that appear on hover */}
      <span className="pointer-events-none absolute inset-0 z-10 overflow-visible">
        <span
          className="absolute top-0 left-0 w-0 h-0 rounded-lg opacity-0
                     bg-white/10 backdrop-blur-[10px] shadow
                     transition-all duration-100 blob-anim
                     group-hover:-top-10 group-hover:left-10
                     group-hover:w-20 group-hover:h-20 group-hover:opacity-100"
        />
        <span
          className="absolute bottom-0 right-0 w-0 h-0 rounded-lg opacity-0
                     bg-white/10 backdrop-blur-[10px] shadow
                     transition-all duration-500 blob-anim-delay
                     group-hover:-bottom-10 group-hover:right-10
                     group-hover:w-20 group-hover:h-20 group-hover:opacity-100"
        />
      </span>

      <div
        className="relative z-20 flex-1 rounded-xl text-white flex flex-col"
        style={{
          padding: '24px',
          background: 'rgba(10,10,11,0.92)',
          backdropFilter: 'blur(10px)',
          WebkitBackdropFilter: 'blur(10px)',
          boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 18 }}>
          {numLabel && (
            <span style={{
              fontSize: 11, fontWeight: 800, letterSpacing: '0.1em',
              color: numColor, paddingTop: 2, flexShrink: 0,
            }}>
              {numLabel}
            </span>
          )}
          <div>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#fff' }}>{title}</div>
            {sub && <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)', marginTop: 3 }}>{sub}</div>}
          </div>
        </div>

        {/* Slot for form fields */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1 }}>
          {children}
        </div>
      </div>
    </div>
  )
}
