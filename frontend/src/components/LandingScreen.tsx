import PrismMark from './PrismMark'
import { ACTION_PRIMARY, DISPLAY, MONO_LABEL } from './tokens'

const SPECTRUM = {
  teal: '#1b98a8',
  amber: '#e3ae3c',
  coral: '#d9645e',
} as const

function ArrowIcon() {
  return (
    <svg
      aria-hidden="true"
      className="landing-cta-arrow size-4"
      viewBox="0 0 20 20"
      fill="none"
    >
      <path
        d="M4 10h11m-4-4 4 4-4 4"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function StudioLink() {
  return (
    <a
      href="#/"
      className={`${ACTION_PRIMARY} landing-studio-link gap-2 px-5 py-2 font-label text-[12px] uppercase tracking-[0.1em] shadow-none`}
    >
      Open App
      <ArrowIcon />
    </a>
  )
}

function PrismArtwork({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x}, ${y})`}>
      <path
        className="landing-prism-impact"
        d="M50 0 100 86.6H0Z"
        fill="none"
        stroke="var(--logo-mark)"
        strokeWidth="3.5"
        strokeLinejoin="round"
      />
      <g transform="translate(26, 102)">
        <line
          x1="0"
          y1="0"
          x2="12"
          y2="0"
          stroke={SPECTRUM.teal}
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <line
          x1="18"
          y1="0"
          x2="30"
          y2="0"
          stroke={SPECTRUM.amber}
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <line
          x1="36"
          y1="0"
          x2="48"
          y2="0"
          stroke={SPECTRUM.coral}
          strokeWidth="2.5"
          strokeLinecap="round"
        />
      </g>
    </g>
  )
}

function HeroBackdrop() {
  return (
    <div className="landing-hero-backdrop" aria-hidden="true">
      <svg
        viewBox="0 0 1440 900"
        preserveAspectRatio="xMidYMid slice"
        focusable="false"
      >
        <defs>
          <pattern
            id="landing-paper-dots"
            width="48"
            height="48"
            patternUnits="userSpaceOnUse"
          >
            <circle cx="2" cy="2" r="1" fill="var(--color-rule)" />
          </pattern>
        </defs>

        <rect width="1440" height="900" fill="url(#landing-paper-dots)" opacity="0.48" />
        <circle cx="1310" cy="120" r="260" fill="var(--color-accent-soft)" opacity="0.72" />
        <circle cx="30" cy="820" r="235" fill="var(--color-duplicate)" opacity="0.68" />

        <g className="landing-backdrop-shape landing-backdrop-shape-left">
          <path
            d="M-145 780 160 252 465 780Z"
            fill="none"
            stroke="var(--color-rule)"
            strokeWidth="1.25"
            vectorEffect="non-scaling-stroke"
          />
          <path
            d="M-65 780 160 390 385 780"
            fill="none"
            stroke="var(--color-hairline)"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        </g>

        <g className="landing-backdrop-shape landing-backdrop-shape-right">
          <path
            d="m1000 126 258 447 258-447Z"
            fill="none"
            stroke="var(--color-rule)"
            strokeWidth="1.25"
            vectorEffect="non-scaling-stroke"
          />
          <path
            d="m1082 126 176 305 176-305"
            fill="none"
            stroke="var(--color-hairline)"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        </g>

        <path
          d="M0 182h260M1180 718h260"
          fill="none"
          stroke="var(--color-rule)"
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
        />
        <circle cx="260" cy="182" r="3" fill="var(--color-ballpoint)" opacity="0.7" />
        <circle cx="1180" cy="718" r="3" fill="var(--color-ballpoint)" opacity="0.7" />

        <g className="landing-backdrop-spectrum" fill="none" strokeLinecap="round" strokeWidth="2">
          <path d="m1240 778 102-36" stroke={SPECTRUM.teal} />
          <path d="m1254 800 102-18" stroke={SPECTRUM.amber} />
          <path d="m1268 822 102 0" stroke={SPECTRUM.coral} />
        </g>
      </svg>
    </div>
  )
}

function DesktopDiagram() {
  return (
    <div className="landing-fade-up landing-stagger-2 relative hidden w-full md:block">
      <svg
        className="h-auto w-full overflow-visible"
        viewBox="0 0 1400 310"
        fill="none"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-labelledby="landing-diagram-title landing-diagram-description"
      >
        <title id="landing-diagram-title">One scope becomes two project documents</title>
        <desc id="landing-diagram-description">
          PRISM turns your scope into a client quotation and a requirements sheet.
        </desc>

        <g aria-hidden="true">
          <path
            className="landing-line-draw landing-scope-ray"
            d="M50 155H625"
            stroke="var(--color-rule)"
            strokeWidth="1.5"
          />
          <circle
            className="landing-ray-tracer landing-tracer-scope"
            r="2"
            fill="var(--color-ballpoint)"
          />

          <path
            className="landing-line-draw landing-output-ray-one"
            d="M775 155 1250 65"
            stroke="var(--color-ballpoint)"
            strokeWidth="1.5"
          />
          <path
            className="landing-line-draw landing-output-ray-two"
            d="M775 155 1250 245"
            stroke="var(--color-ballpoint)"
            strokeWidth="1.5"
          />
          <circle
            className="landing-ray-tracer landing-tracer-output-one"
            r="2"
            fill={SPECTRUM.teal}
          />
          <circle
            className="landing-ray-tracer landing-tracer-output-two"
            r="2"
            fill={SPECTRUM.coral}
          />

          <circle
            className="landing-diagram-dot landing-dot-start"
            cx="50"
            cy="155"
            r="4"
            fill="var(--color-ballpoint)"
          />
          <circle
            className="landing-diagram-dot landing-dot-end-one"
            cx="1250"
            cy="65"
            r="4"
            fill={SPECTRUM.teal}
          />
          <circle
            className="landing-diagram-dot landing-dot-end-two"
            cx="1250"
            cy="245"
            r="4"
            fill={SPECTRUM.coral}
          />

          <PrismArtwork x={650} y={105} />

          <text x="50" y="188" className="landing-diagram-label">
            YOUR SCOPE
          </text>
          <text
            x="1235"
            y="38"
            textAnchor="end"
            className="landing-diagram-label landing-output-label-one"
          >
            CLIENT QUOTATION
          </text>
          <text
            x="1235"
            y="280"
            textAnchor="end"
            className="landing-diagram-label landing-output-label-two"
          >
            REQUIREMENTS SHEET
          </text>
        </g>
      </svg>
    </div>
  )
}

function MobileDiagram() {
  return (
    <div className="landing-fade-up landing-stagger-2 relative mx-auto block w-full max-w-[280px] py-2 sm:max-w-[320px] md:hidden">
      <svg
        className="h-auto w-full overflow-visible"
        viewBox="0 0 320 420"
        fill="none"
        role="img"
        aria-labelledby="landing-mobile-diagram-title landing-mobile-diagram-description"
      >
        <title id="landing-mobile-diagram-title">One scope becomes two project documents</title>
        <desc id="landing-mobile-diagram-description">
          PRISM turns your scope into a client quotation and a requirements sheet.
        </desc>

        <g aria-hidden="true">
          <path
            className="landing-line-draw landing-scope-ray"
            d="M160 20V120"
            stroke="var(--color-rule)"
            strokeWidth="1.5"
          />
          <circle
            className="landing-ray-tracer landing-tracer-scope-mobile"
            r="2"
            fill="var(--color-ballpoint)"
          />

          <path
            className="landing-line-draw landing-output-ray-one"
            d="M160 270 65 360"
            stroke="var(--color-ballpoint)"
            strokeWidth="1.5"
          />
          <path
            className="landing-line-draw landing-output-ray-two"
            d="M160 270 255 360"
            stroke="var(--color-ballpoint)"
            strokeWidth="1.5"
          />
          <circle
            className="landing-ray-tracer landing-tracer-output-one-mobile"
            r="2"
            fill={SPECTRUM.teal}
          />
          <circle
            className="landing-ray-tracer landing-tracer-output-two-mobile"
            r="2"
            fill={SPECTRUM.coral}
          />

          <circle
            className="landing-diagram-dot landing-dot-start"
            cx="160"
            cy="20"
            r="4"
            fill="var(--color-ballpoint)"
          />
          <circle
            className="landing-diagram-dot landing-dot-end-one"
            cx="65"
            cy="360"
            r="4"
            fill={SPECTRUM.teal}
          />
          <circle
            className="landing-diagram-dot landing-dot-end-two"
            cx="255"
            cy="360"
            r="4"
            fill={SPECTRUM.coral}
          />

          <PrismArtwork x={110} y={140} />

          <text x="160" y="12" textAnchor="middle" className="landing-diagram-label">
            YOUR SCOPE
          </text>
          <text
            x="4"
            y="398"
            textAnchor="start"
            className="landing-diagram-label landing-output-label-one"
          >
            CLIENT QUOTATION
          </text>
          <text
            x="316"
            y="398"
            textAnchor="end"
            className="landing-diagram-label landing-output-label-two"
          >
            REQUIREMENTS SHEET
          </text>
        </g>
      </svg>
    </div>
  )
}

const PROCESS_STEPS = [
  {
    number: '01',
    title: 'Capture the scope',
    body: 'Describe the client, discipline, project brief, budget, and any supporting files.',
  },
  {
    number: '02',
    title: 'Apply studio rules',
    body: 'Choose currency, market, tax, payment terms, and either rate-card or requirements-based pricing.',
  },
  {
    number: '03',
    title: 'Review both outputs',
    body: 'Review the quotation and requirements together. When scope changes, create a revision without overwriting its parent.',
  },
] as const

const QUOTATION_DETAILS = [
  'Scope inclusions and exclusions',
  'Priced line items and totals',
  'Timeline and payment milestones',
  'Assumptions and next steps',
  'Markdown, printable HTML, and PDF',
] as const

const REQUIREMENTS_DETAILS = [
  'Sections shaped for the discipline',
  'Numbered requirements and phases',
  'Acceptance criteria',
  'Identified delivery risks',
  'A structured handoff for delivery',
] as const

const GROUNDING_POINTS = [
  'With rate-card pricing selected, covered roles use your exact configured rates.',
  'The quotation and requirements document render from one stored estimate.',
  'The server recalculates subtotals, contingency, discount, tax, totals, and payment milestones.',
] as const

const WORKFLOW_STEPS = [
  'Collect client request',
  'Prepare quotation',
  'Share with client',
  'Revise or finalise',
  'Build a proposal when needed',
] as const

type SectionIntroProps = {
  id: string
  eyebrow: string
  title: string
  description?: string
}

function SectionIntro({ id, eyebrow, title, description }: SectionIntroProps) {
  return (
    <div className="max-w-[760px]">
      <p className={MONO_LABEL}>{eyebrow}</p>
      <h2
        id={id}
        className={`${DISPLAY} mt-5 text-[clamp(2rem,4.5vw,3.6rem)] leading-[1.06] tracking-[-0.035em] text-ink`}
      >
        {title}
      </h2>
      {description ? (
        <p className="mt-6 max-w-[680px] text-[17px] leading-relaxed text-void md:text-[19px]">
          {description}
        </p>
      ) : null}
    </div>
  )
}

type DocumentPanelProps = {
  title: string
  description: string
  details: readonly string[]
  tone: 'quotation' | 'requirements'
}

function DocumentPanel({ title, description, details, tone }: DocumentPanelProps) {
  const headingId = `landing-document-${tone}-title`

  return (
    <article
      className={`landing-document landing-document-${tone}`}
      aria-labelledby={headingId}
    >
      <div className="flex items-center justify-between gap-6 border-b border-hairline pb-5">
        <h3 id={headingId} className={`${MONO_LABEL} text-ink`}>
          {title}
        </h3>
        <span className="landing-document-swatch" aria-hidden="true" />
      </div>
      <p className={`${DISPLAY} mt-8 max-w-[28rem] text-[25px] leading-tight text-ink md:text-[30px]`}>
        {description}
      </p>
      <ul className="mt-10 border-t border-rule" aria-label={`${title} includes`}>
        {details.map((detail) => (
          <li
            key={detail}
            className="flex items-center gap-4 border-b border-hairline py-4 text-[15px] text-body"
          >
            <span className="landing-document-index" aria-hidden="true" />
            {detail}
          </li>
        ))}
      </ul>
    </article>
  )
}

/** Public, light-first introduction to PRISM. */
export default function LandingScreen() {
  return (
    <div className="sheet-light min-h-dvh overflow-x-hidden bg-canvas font-body text-body">
      <header className="fixed inset-x-0 top-0 z-40 border-b border-hairline bg-canvas">
        <div className="mx-auto flex h-16 max-w-app items-center justify-between px-6 md:px-14">
          <div className="flex items-center gap-3" aria-label="PRISM">
            <PrismMark size={28} />
            <span className={`${MONO_LABEL} text-[13px] font-semibold tracking-[0.18em] text-ink`}>
              PRISM
            </span>
          </div>
          <StudioLink />
        </div>
      </header>

      <main>
        <section className="landing-hero relative isolate flex min-h-dvh flex-col items-center justify-center overflow-hidden px-6 pb-8 pt-24 md:px-14 md:pb-10">
          <HeroBackdrop />
          <div className="relative z-[1] mx-auto w-full max-w-app">
            <div className="mb-8 text-center md:mb-12">
              <h1
                className={`${DISPLAY} landing-fade-up text-[clamp(2.25rem,6vw,4rem)] leading-[1.05] tracking-[-0.04em] text-ink`}
              >
                One scope. Two results.
              </h1>
              <p className="landing-fade-up landing-stagger-1 mx-auto mt-8 max-w-[620px] text-[18px] leading-relaxed text-void md:text-[20px]">
                A project scope becomes a review-ready client quotation and a
                discipline-specific requirements sheet, both rendered from the same checked
                estimate.
              </p>
            </div>

            <DesktopDiagram />
            <MobileDiagram />

          </div>
        </section>

        <section
          className="border-y border-hairline bg-paper px-6 py-24 md:px-14 md:py-32"
          aria-labelledby="landing-process-title"
        >
          <div className="landing-section-reveal mx-auto max-w-app">
            <div className="grid gap-10 lg:grid-cols-[0.8fr_1.2fr] lg:gap-20">
              <p className={MONO_LABEL}>How it works</p>
              <div>
                <h2
                  id="landing-process-title"
                  className={`${DISPLAY} text-[clamp(2rem,4.5vw,3.6rem)] leading-[1.06] tracking-[-0.035em] text-ink`}
                >
                  From a project scope to two aligned documents.
                </h2>
                <p className="mt-6 max-w-[680px] text-[17px] leading-relaxed text-void md:text-[19px]">
                  Submit once. PRISM builds one structured estimate, then renders the commercial
                  and delivery views from the same source.
                </p>
              </div>
            </div>

            <ol className="landing-process mt-16 border-y border-rule md:mt-24">
              {PROCESS_STEPS.map((step) => (
                <li key={step.number} className="landing-process-step">
                  <span className="font-label text-[12px] font-semibold tracking-[0.14em] text-ballpoint">
                    {step.number}
                  </span>
                  <h3 className={`${DISPLAY} mt-8 text-[24px] leading-tight text-ink`}>
                    {step.title}
                  </h3>
                  <p className="mt-4 text-[15px] leading-relaxed text-void">{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section
          className="bg-duplicate px-6 py-24 md:px-14 md:py-32"
          aria-labelledby="landing-documents-title"
        >
          <div className="landing-section-reveal mx-auto max-w-app">
            <SectionIntro
              id="landing-documents-title"
              eyebrow="Two documents, one source"
              title="Commercial clarity for the client. Delivery clarity for the team."
              description="The language and structure change for each audience while the underlying scope and commercial figures stay aligned."
            />

            <div className="landing-scope-branch mt-16 md:mt-24" aria-hidden="true">
              <span>Your scope</span>
            </div>

            <div className="grid gap-6 md:grid-cols-2 md:gap-8">
              <DocumentPanel
                title="Client quotation"
                description="A review-ready commercial view of the work."
                details={QUOTATION_DETAILS}
                tone="quotation"
              />
              <DocumentPanel
                title="Requirements sheet"
                description="A structured delivery brief shaped for the selected discipline."
                details={REQUIREMENTS_DETAILS}
                tone="requirements"
              />
            </div>

            <p className="mx-auto mt-10 max-w-[720px] text-center text-[15px] leading-relaxed text-void">
              Both documents inherit the same estimate, keeping commercial and delivery details
              aligned as the work moves forward.
            </p>
          </div>
        </section>

        <section
          className="border-y border-rule bg-accent-soft px-6 py-24 md:px-14 md:py-32"
          aria-labelledby="landing-grounded-title"
        >
          <div className="landing-section-reveal mx-auto grid max-w-app gap-14 lg:grid-cols-[0.8fr_1.2fr] lg:gap-24">
            <div>
              <p className={MONO_LABEL}>Grounded, not guessed</p>
              <h2
                id="landing-grounded-title"
                className={`${DISPLAY} mt-5 text-[clamp(2rem,4.5vw,3.6rem)] leading-[1.06] tracking-[-0.035em] text-ink`}
              >
                Grounded in how your studio works.
              </h2>
              <p className="mt-6 max-w-[560px] text-[17px] leading-relaxed text-void">
                AI drafts the structured estimate. Your studio settings and the server keep the
                commercial result accountable.
              </p>
            </div>

            <ol className="border-t border-rule">
              {GROUNDING_POINTS.map((point, index) => (
                <li
                  key={point}
                  className="grid gap-5 border-b border-rule py-7 sm:grid-cols-[3rem_1fr] sm:items-start"
                >
                  <span className="font-label text-[12px] font-semibold tracking-[0.14em] text-ballpoint">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <p className={`${DISPLAY} text-[21px] leading-snug text-ink md:text-[25px]`}>
                    {point}
                  </p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section
          className="bg-paper px-6 py-24 md:px-14 md:py-32"
          aria-labelledby="landing-workflow-title"
        >
          <div className="landing-section-reveal mx-auto max-w-app">
            <SectionIntro
              id="landing-workflow-title"
              eyebrow="The real workflow"
              title="A clear path from request to reviewed quotation."
              description="Collect the request, prepare both outputs, and share the quotation for revision or finalisation. A selected quotation can also seed a separate client proposal when needed."
            />

            <ol className="landing-workflow mt-16 md:mt-24" aria-label="PRISM workflow">
              {WORKFLOW_STEPS.map((step, index) => (
                <li key={step} className="landing-workflow-step">
                  <span className="landing-workflow-marker" aria-hidden="true">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <span className="mt-5 block max-w-[11rem] font-label text-[12px] font-semibold uppercase leading-relaxed tracking-[0.1em] text-ink">
                    {step}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="bg-ink px-6 py-24 text-paper md:px-14 md:py-32">
          <div className="mx-auto max-w-app">
            <div className="max-w-[850px]">
              <p className="font-label text-[12px] font-medium uppercase tracking-[0.14em] text-paper">
                One brief in. Two documents out.
              </p>
              <h2 className={`${DISPLAY} mt-5 text-[clamp(2.3rem,5.5vw,5rem)] leading-[1.02] tracking-[-0.045em] text-paper`}>
                Turn your next scope into both documents.
              </h2>
              <p className="mt-7 max-w-[680px] text-[17px] leading-relaxed text-paper md:text-[19px]">
                Start with the work you already know. PRISM structures the commercial and delivery
                views together.
              </p>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-hairline bg-ink py-10 text-paper">
        <div className="mx-auto flex max-w-app flex-col items-start justify-between gap-5 px-6 sm:flex-row sm:items-center md:px-14">
          <div className="flex items-center gap-3">
            <PrismMark size={20} />
            <span className="font-label text-[11px] font-semibold uppercase tracking-[0.2em] text-paper">
              PRISM
            </span>
          </div>
          <p className="font-label text-[11px] uppercase tracking-[0.14em] text-paper">
            Scope in. Quotation and requirements out.
          </p>
        </div>
      </footer>
    </div>
  )
}
