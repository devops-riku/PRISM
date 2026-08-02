/**
 * The shapes the API sends, written down once.
 *
 * The backend is the authority. Every type here mirrors a Pydantic model in
 * backend/app - schemas.py for the quotation and the proposal, jobs.py for
 * background work, settings.py for the studio's defaults, members.py and
 * inbox.py and workspaces.py for the team, the mail and the books of work. When
 * one of those models changes, this file is wrong until somebody changes it
 * too; nothing here is inferred from a response at runtime.
 *
 * Two rules that explain most of what follows:
 *
 *   * **Nothing is optional just because it might be empty.** Every field on
 *     every server model has a default, FastAPI serialises all of them, and
 *     Pydantic fills the defaults when it reads a bundle written before a field
 *     existed. So a key is absent from the JSON only when the endpoint really
 *     does omit it - and there is exactly one of those in here, marked where it
 *     is. An empty string, an empty array and a zero are the server's way of
 *     saying nothing, and they all typecheck as themselves.
 *   * **Money stays `number`.** Every figure in a document is computed and
 *     rounded server-side, in the selected currency, with no FX anywhere. The
 *     client's whole job is to print what it was handed.
 *
 * This is a leaf: it imports nothing, so any module can import it without
 * dragging the API client or React in behind it.
 */

// --- closed sets --------------------------------------------------------------

/** Requirement weight. `schemas.Priority`. */
export type Priority = 'must' | 'should' | 'could' | 'wont'

/** `schemas.RequirementType`. */
export type RequirementType = 'functional' | 'non_functional'

/** How sure the model was of its own estimate. `schemas.Confidence`. */
export type Confidence = 'low' | 'medium' | 'high'

/** What one unit of a line item is. `schemas.UnitKind`. */
export type UnitKind = 'hour' | 'day' | 'week' | 'month' | 'item' | 'lump_sum'

/**
 * The discipline a quotation belongs to - the ids in app/kinds.py.
 *
 * The server's field is a plain `str` and `kinds.resolve()` maps anything it
 * does not recognise to `software`, so this union is the set the pad sends and
 * the documents print rather than a constraint the API enforces.
 */
export type QuotationKind = 'software' | 'accounting' | 'engineering' | 'design' | 'marketing' | 'other'

/**
 * Whether tax is added on top, already inside the rates, or absent entirely.
 * `none` is a studio decision the model does not get to overrule.
 */
export type TaxMode = 'exclusive' | 'inclusive' | 'none'

/** Whether the studio's own card binds this quotation's rates. */
export type PricingBasis = 'rate_card' | 'requirements'

/**
 * How the instalments after the deposit fall due.
 *
 * Like `QuotationKind`, this is what the pad sends rather than something the API
 * enforces: the recorded field is a plain `str` with no validator behind it.
 */
export type PaymentCadence = 'monthly' | 'phase' | 'milestone'

/** How the next quotation or proposal number is drawn. */
export type ReferenceMode = 'incremental' | 'random'

/**
 * Two roles, because two is what the difference is actually about: whether you
 * can change what the studio charges and throw work away, or not. `members.ROLES`.
 */
export type MemberRole = 'admin' | 'member'

/** Which of a bundle's two documents a file is. `GeneratedFile.kind`. */
export type DocumentKind = 'proposal' | 'requirements'

// --- the estimate -------------------------------------------------------------

export type Requirement = {
  /** Stable ID, e.g. FR-01 or NFR-03. */
  id: string
  title: string
  description: string
  type: RequirementType
  priority: Priority
  acceptance_criteria: string[]
}

export type LineItem = {
  /** Stable ID, e.g. LI-01. */
  id: string
  /** Design, Frontend, Backend, QA, PM, Infra, Third-party. */
  category: string
  description: string
  /** Who performs it, e.g. Senior Backend Engineer. */
  role: string
  quantity: number
  unit: UnitKind
  unit_rate: number
  /** Recomputed server-side as quantity * unit_rate. Never add these up here. */
  subtotal: number
  requirement_ids: string[]
  notes: string
}

export type Phase = {
  name: string
  objective: string
  deliverables: string[]
  duration_weeks: number
  line_item_ids: string[]
}

export type PaymentMilestone = {
  label: string
  percent: number
  amount: number
  /** What must happen for this to become payable. */
  trigger: string
}

export type CostSummary = {
  subtotal: number
  /** Percent, e.g. 10 means 10%. */
  contingency_pct: number
  contingency_amount: number
  discount_amount: number
  /** e.g. VAT. Empty string means no tax line. */
  tax_label: string
  tax_pct: number
  /**
   * False: the tax is added on top and `total` is larger than `subtotal` by it.
   * True: the rates already contain it, `total` is the priced work itself, and
   * `tax_amount` is the portion extracted from it.
   */
  tax_inclusive: boolean
  tax_amount: number
  /**
   * The buffer that was folded into the line item quantities instead of being
   * itemised, when the studio does not show contingency to the client.
   */
  contingency_absorbed_pct: number
  total: number
  payment_milestones: PaymentMilestone[]
  /** One sentence on how the rates were derived for this market. */
  rate_basis: string
}

export type Risk = {
  description: string
  impact: string
  likelihood: string
  mitigation: string
}

export type TechStackItem = {
  /** e.g. Frontend, API, Datastore, Hosting. */
  layer: string
  choice: string
  rationale: string
}

export type ApiEndpoint = {
  method: string
  path: string
  purpose: string
  request_notes: string
  response_notes: string
}

/** How the work gets from a developer's machine to production, and stays there. */
export type DevOpsPlan = {
  environments: string[]
  ci_cd: string
  infrastructure: string
  observability: string
  release_and_rollback: string
  backup_and_recovery: string
  secrets_and_access: string
}

/** One part of a requirements document, for a discipline that is not software. */
export type SpecSection = {
  heading: string
  body: string
  points: string[]
}

/** Prose for the client-facing proposal. Numbers never live here. */
export type ClientNarrative = {
  title: string
  executive_summary: string
  understanding: string
  proposed_solution: string
  scope_inclusions: string[]
  scope_exclusions: string[]
  assumptions: string[]
  timeline_summary: string
  next_steps: string[]
  validity_days: number
}

/**
 * Prose for the handoff document.
 *
 * The typed fields describe software, because that is what PRISM was built to
 * quote. Every other discipline fills `sections` instead. Nothing reads both -
 * branch on the estimate's `kind` once, at the top.
 */
export type DeveloperSpec = {
  overview: string
  architecture_summary: string
  tech_stack: TechStackItem[]
  data_model_notes: string
  api_surface: ApiEndpoint[]
  integrations: string[]
  non_functional: string[]
  testing_strategy: string
  devops: DevOpsPlan
  open_questions: string[]
  /** Empty for software, one entry per section the kind declares otherwise. */
  sections: SpecSection[]
}

/** The complete Gemini response. Both output documents render from this. */
export type Estimate = {
  project_name: string
  client_name: string
  kind: QuotationKind
  /** What the studio called the discipline, when `kind` is 'other'. Empty otherwise. */
  kind_label: string
  /** ISO 4217 code the line items are priced in. */
  currency: string
  market_region: string
  confidence: Confidence
  /** What each uploaded image showed. Empty when no images were sent. */
  image_observations: string[]
  requirements: Requirement[]
  phases: Phase[]
  line_items: LineItem[]
  cost: CostSummary
  risks: Risk[]
  client: ClientNarrative
  developer: DeveloperSpec
  /** The reference the documents print, e.g. ABC-0002187. Server-set. */
  quotation_ref: string
}

/**
 * The persuasive half of a proposal - the only half a model writes.
 *
 * There is deliberately no field for terms, warranty, ownership or payment
 * wording. Those are the studio's, printed verbatim from `policies`.
 */
export type ProposalNarrative = {
  title: string
  cover_letter: string
  executive_summary: string
  understanding: string
  scope_overview: string
  approach: string
  why_us: string[]
  deliverables: string[]
  risks_addressed: string[]
  next_steps: string[]
}

// --- what a quotation is delivered as -----------------------------------------

export type GeneratedFile = {
  kind: DocumentKind
  filename: string
  markdown: string
  download_url: string
  print_url: string
  pdf_url: string
}

/** One payment of a written schedule, as recorded on a bundle. */
export type ScheduleRowRecord = {
  percent: number
  trigger: string
}

/** The payment terms a quotation was prepared under, so a revision inherits them. */
export type PaymentTermsRecord = {
  deposit_pct: number
  instalments: number
  cadence: PaymentCadence
  deposit_trigger: string
  schedule: ScheduleRowRecord[]
}

/** One of the other tiers quoted from the same brief. */
export type TierSibling = {
  id: string
  tier_name: string
  tier_index: number
  total: number
  currency: string
}

/**
 * What POST /api/proposals eventually produces, and what GET
 * /api/proposals/{id} returns.
 *
 * Revision and tier metadata live here rather than on `Estimate` because
 * `Estimate` is the Gemini response schema: provenance is the server's
 * business, not the model's.
 */
export type ProposalBundle = {
  id: string
  /** ISO-8601 UTC timestamp. */
  created_at: string
  estimate: Estimate
  files: GeneratedFile[]

  /** 1 for an original, 2 for its first revision. */
  revision: number
  /** Bundle this was revised from. Empty for an original. */
  parent_id: string
  /** That bundle's printed reference. Empty when the parent has been deleted. */
  parent_ref: string
  root_id: string
  revision_instruction: string

  /** Requested total. 0 when none was set. */
  target_total: number
  /** False when the requested total was unreachable and the nearest was used. */
  hit_target: boolean
  target_note: string

  /** Shared by every tier prepared from one brief. Empty for a single quotation. */
  tier_group_id: string
  tier_name: string
  tier_index: number
  tier_siblings: TierSibling[]

  /** Line items priced from the studio card. 0 when no card is configured. */
  rate_card_bound: number
  /** Line items deleted because the card does not cover them, described. */
  rate_card_removed: string[]
  payment_terms: PaymentTermsRecord
  tier_ceiling: number
  ceiling_applied: boolean
  tier_cap: number
  tier_order_enforced: boolean
  tier_cap_note: string
  priced_from_rate_card: boolean

  payment_terms_applied: boolean
  /** Money the removals took off the quotation, before contingency and tax. */
  rate_card_removed_value: number
}

/**
 * One row in the quotations list.
 *
 * Deliberately not a `ProposalBundle`: a bundle carries both rendered documents
 * and runs to tens of kilobytes, so a hundred of them would be a multi-megabyte
 * response for a screen that shows a total and a date.
 */
export type ProposalSummary = {
  id: string
  created_at: string
  /** What a client quotes back at you, e.g. ABC-0002187. The id above is storage. */
  quotation_ref: string
  project_name: string
  client_name: string
  currency: string
  total: number
  /** How many line items it has - a count, not the items. */
  line_items: number
  revision: number
  parent_id: string
  root_id: string
  target_total: number
  hit_target: boolean
  tax_label: string
  tax_pct: number
  tax_inclusive: boolean
  tier_group_id: string
  tier_name: string
  proposal_url: string
  requirements_url: string
}

// --- the built proposal -------------------------------------------------------

/** One section of the proposal as the template had it when it was built. */
export type SectionRecord = {
  id: string
  heading: string
}

/** One clause as it stood when the proposal was built. */
export type PolicyRecord = {
  id: string
  title: string
  body: string
}

/**
 * A proposal built from one quotation, exactly as that quotation stood.
 *
 * `policies`, `sections` and `design` are snapshots. A proposal sent in March
 * says what it said in March and looks the way it looked in March, whatever
 * Settings looks like in April.
 */
export type ProposalDocument = {
  id: string
  created_at: string
  /** The bundle this was built from. */
  quotation_id: string
  quotation_ref: string
  /** This proposal's own number, e.g. P-0000041. Empty on one built before they were numbered. */
  reference: string
  /** When the quotation was issued. Validity is counted from here. */
  quotation_issued_at: string
  title: string
  client_name: string
  project_name: string
  currency: string
  total: number
  studio_name: string
  signatory: string
  signatory_title: string
  narrative: ProposalNarrative
  policies: PolicyRecord[]
  /** Empty means the shipped order. */
  sections: SectionRecord[]
  design: ProposalDesign
  files: GeneratedFile[]
}

/** A row in the list of proposals already built. */
export type ProposalDocumentSummary = {
  id: string
  created_at: string
  quotation_id: string
  quotation_ref: string
  /** The proposal's own number. */
  reference: string
  title: string
  client_name: string
  project_name: string
  currency: string
  total: number
  policy_count: number
}

// --- background work ----------------------------------------------------------

/** One unit of work the job promised to do. */
export type JobStep = {
  label: string
  done: boolean
}

/** What state a job is in. `jobs.State`. */
export type JobState = 'queued' | 'running' | 'done' | 'failed'

/**
 * What the server starts a job for. Taken from the `jobs.create` calls in
 * main.py rather than from the field's own description, which still says only
 * "quotation | revision" - proposals grew a job later.
 */
export type JobKind = 'quotation' | 'revision' | 'proposal'

/**
 * A piece of work running behind the request that asked for it - `jobs.JobView`.
 *
 * `owner` is on the stored job and is excluded from the view: who started a job
 * is the server's business, not the workspace's. It is absent here for the same
 * reason, and adding it would be inventing a field the API never sends.
 *
 * `progress` is the view's own addition, computed from the steps actually
 * finished. It is 0 to 1, and it only ever moves on a real completion.
 */
export type Job = {
  id: string
  kind: JobKind
  /** What the user recognises it by. */
  title: string
  /** A second line: client, tiers, whatever helps. */
  detail: string
  state: JobState
  /** What is happening right now. */
  stage: string
  steps: JobStep[]
  created_at: string
  updated_at: string
  finished_at: string
  /**
   * What this produced: quotation ids for a `quotation` or `revision` job, and
   * the one proposal document's id for a `proposal` job. Which it is follows
   * from `kind`, which is what decides where a finished job links to.
   */
  result_ids: string[]
  /** What went wrong, in the user's terms. */
  error: string
  progress: number
}

// --- studio defaults ----------------------------------------------------------

/**
 * One studio-wide working day.
 *
 * Deprecated on the server too: each role on the card now carries its own,
 * because a monthly retainer and a day rate do not share a working month. It is
 * read to migrate a settings file written before the move and dropped once it
 * has been, so it is `null` on everything saved since.
 */
export type UnitBasis = {
  hours_per_day: number
  days_per_week: number
}

/**
 * One line of the studio's rate card, including what its unit means.
 *
 * The numbers are numbers here because that is what the server sends and stores.
 * The Settings editor holds its own draft shape while somebody is typing - a
 * rate field has to be emptiable - and converts at the save boundary.
 */
export type RoleRate = {
  /** How the studio names the job, e.g. 'Senior Backend Engineer'. */
  role: string
  /** The period the rate buys. A plain string server-side, but the same six values. */
  unit: UnitKind
  /** Charge for one `unit`, in the studio's default currency. */
  rate: number
  hours_per_day: number
  days_per_week: number
  /** Working days in one month of this role. A retainer month is not 30 days. */
  days_per_month: number
}

/** One numbered term in the proposal's conditions. Printed exactly as written. */
export type PolicyClause = {
  /** Stable key, e.g. 'validity'. Used to match on edit. */
  id: string
  title: string
  body: string
  /** False leaves it out of the document entirely. */
  enabled: boolean
}

/**
 * One section of the proposal, as the studio wants it - the server's
 * `template.SectionSpec`.
 *
 * Renamed here because `SpecSection` above is a different thing entirely (a
 * part of a requirements document), and two types called SectionSpec in one
 * file would be a coin flip at every use site.
 */
export type TemplateSection = {
  /** Which builder this is. The renderer drops an id it does not know. */
  id: string
  /** The heading printed. Empty uses the default. */
  heading: string
  enabled: boolean
}

/** One of the typefaces both renderers can draw. `design.FONTS`. */
export type DesignFont = 'sans' | 'grotesque' | 'serif'
/** How the cover is laid out. */
export type DesignCover = 'centred' | 'left' | 'banner'
/** Page margins, by name rather than by millimetre. */
export type DesignMargins = 'compact' | 'standard' | 'roomy'
/** How the tables are ruled. */
export type DesignTables = 'ruled' | 'zebra' | 'plain'

/**
 * The look of a proposal. Content is not configurable here, only its dress -
 * nothing in this shape can reach a figure, which is why a studio may edit it
 * freely.
 */
export type ProposalDesign = {
  /** A data: URI for the studio's mark. Empty prints the studio name as text. */
  logo: string
  /** Headings and rules. */
  brand_colour: string
  /** Totals, links, the edge. */
  accent_colour: string
  heading_font: DesignFont
  body_font: DesignFont
  cover: DesignCover
  margins: DesignMargins
  tables: DesignTables
  page_numbers: boolean
  /** One line in the footer, e.g. a registration number. Empty prints nothing. */
  footer_note: string
}

/**
 * The values a new brief form opens with - and, apart from the rate card, only
 * that. `GET /api/settings` and the body of `PUT /api/settings`.
 */
export type StudioDefaults = {
  /** Who is quoting. Empty falls back to PRISM. */
  studio_name: string
  /** ISO 4217 code. */
  currency: string
  market_region: string
  tax_mode: TaxMode
  /** Derived from `tax_mode`. Kept so an older client still reads the default. */
  tax_inclusive: boolean
  /**
   * Genuinely absent as a value: the server sends `null` for every settings file
   * saved since the basis moved onto each role, and an object only for one that
   * has not been migrated yet.
   */
  unit_basis: UnitBasis | null
  /** Up to four letters in front of every quotation number. Empty falls back to Q. */
  reference_prefix: string
  reference_mode: ReferenceMode
  /** Read-only. What the next reference will look like; ignored on write. */
  reference_preview: string
  /** Up to four letters in front of every proposal number. Empty falls back to P. */
  proposal_prefix: string
  proposal_reference_mode: ReferenceMode
  /** Read-only. What the next proposal number will look like; ignored on write. */
  proposal_reference_preview: string
  /** False folds the contingency into the priced work so the client sees no line for it. */
  show_contingency_to_client: boolean
  proposal_signatory: string
  proposal_signatory_title: string
  /** Empty means the shipped document. */
  proposal_sections: TemplateSection[]
  proposal_design: ProposalDesign
  /** Empty means the recommended set in app/policies.py is used. */
  policies: PolicyClause[]
  /** Empty means the model prices from the requirements at market rates. */
  rate_card: RoleRate[]
}

// --- workspaces, teams and mail -----------------------------------------------

/**
 * One separate book of work, with enough about it to choose between two -
 * `main.WorkspaceView`, which is the stored workspace plus what is in it.
 */
export type Workspace = {
  /** Slug, and the folder name under generated/w/. */
  id: string
  /** What the studio calls it. */
  name: string
  created_at: string
  quotations: number
  proposals: number
  /** What this workspace's own settings call the studio. */
  studio_name: string
}

/**
 * One person on a workspace, as the team screen sees them - `main.MemberView`.
 *
 * The stored member carries a Supabase id as well; it is never sent, so it is
 * not here. `you` is the server's answer to "is this row me", computed against
 * the token rather than left for the client to guess.
 */
export type Member = {
  email: string
  role: MemberRole
  added_at: string
  you: boolean
}

/** A standing offer for one email address to join - `main.InviteView`. */
export type Invite = {
  email: string
  role: MemberRole
  invited_by: string
  expires_at: string
  /** Where the invitation points. Send it however you like. */
  link: string
  /** Whether Resend accepted the message. */
  emailed: boolean
  /** Why it was not emailed, if it was not. */
  problem: string
}

/** One workspace's people, and what the person asking may do. */
export type Team = {
  workspace: string
  name: string
  /**
   * Empty when the server has no opinion yet - an install that requires a
   * sign-in, asked by somebody not on this roster. The screens treat that as
   * "not an admin"; the 403 is still the real boundary.
   */
  your_role: MemberRole | ''
  members: Member[]
  invites: Invite[]
  email_configured: boolean
}

/** What an invitation says, before anybody accepts it. Readable without a session. */
export type InvitePreview = {
  workspace: string
  name: string
  email: string
  role: MemberRole
  invited_by: string
  expires_at: string
  valid: boolean
  problem: string
}

/**
 * One thing that happened, as told to you.
 *
 * `kind` stays a string: the server adds one every time something new becomes
 * worth telling somebody about, and the bell looks it up in a table with a
 * default rather than switching over every value.
 */
export type Note = {
  id: string
  kind: string
  at: string
  title: string
  body: string
  /** Where to go to act on it. Empty means nowhere. */
  href: string
  /** Empty until it has been read. */
  read_at: string
}

/** Your own mail in the workspace the header names. */
export type Mailbox = {
  unread: number
  notes: Note[]
}

// --- small reference shapes ---------------------------------------------------

/**
 * One currency the server will price in.
 *
 * There is no exchange rate here and there must never be one: Gemini prices
 * directly in the requested currency for the requested market.
 */
export type Currency = {
  code: string
  name: string
  symbol: string
}

/** What the client needs before it can show a sign-in screen. `/api/auth/config`. */
export type AuthConfig = {
  /** False means this install has no accounts and answers everyone. */
  required: boolean
  /** The Supabase project URL, or empty. */
  url: string
  /** The publishable key. Public by design; the secret is never sent. */
  anon_key: string
}
