import { useMemo } from 'react'
import type { ReactElement, ReactNode } from 'react'

/**
 * A small, dependency-free markdown renderer.
 *
 * PRISM ships two documents whose markdown the server renders; the browser only
 * has to display it. Everything the renderers emit is covered here: headings,
 * bold, italic, inline and fenced code, links, ordered and unordered lists with
 * nesting, GFM pipe tables, horizontal rules, blockquotes and paragraphs.
 *
 * Presentation belongs to `.prose` in src/index.css, so this emits plain
 * semantic elements and only two hints on top of them: the `align` attribute on
 * table cells, which `.prose` turns into right-aligned tabular mono exactly
 * where the money columns are, and a mono typeface for a figure sitting in a
 * column the document left aligned.
 *
 * Source text is HTML-escaped before any formatting is applied, and link hrefs
 * are restricted to http, https, mailto and same-document targets, so nothing
 * the model writes can inject markup into the page.
 */

/** Which sheet the markdown is being printed on. */
type Tone = 'original' | 'duplicate'

/** The two class hints `toneStyles` hands the renderers. */
type ToneContext = {
  code: string
  pre: string
}

/** One line of a list, with the indent that decides how deep it nests. */
type ListItem = {
  indent: number
  ordered: boolean
  text: string
}

const ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
const escapeHtml = (value: string) => String(value).replace(/[&<>"']/g, (char: string) => ESCAPES[char])

const SAFE_HREF = /^(?:https?:\/\/|mailto:|\/|#)/i

// A link target, tolerating balanced parentheses so that
// `https://en.wikipedia.org/wiki/Foo_(bar)` and `javascript:alert(1)` are both
// consumed whole — the second is then rejected rather than half-swallowed.
const HREF = '([^\\s()]*(?:\\([^()]*\\)[^\\s()]*)*)'
const IMAGE_RE = new RegExp(`!\\[([^\\]]*)\\]\\(${HREF}(?:\\s+[^)]*)?\\)`, 'g')
const LINK_RE = new RegExp(`\\[([^\\]]+)\\]\\(${HREF}(?:\\s+[^)]*)?\\)`, 'g')

// Placeholder for an extracted code span. Printable ASCII, so HTML escaping
// leaves it untouched, and it survives every emphasis pass unchanged.
const CODE_SLOT = (index: number) => `%%PRISMCODE${index}ENDCODE%%`
const CODE_SLOT_RE = /%%PRISMCODE(\d+)ENDCODE%%/g

const LIST_ITEM = /^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$/
const ORDERED_ITEM = /^\s*\d+[.)]\s+/
// GFM task item. Every acceptance criterion in the developer sheet is one.
const TASK_ITEM = /^\[([ xX])\]\s+/
const TABLE_DIVIDER = /^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$/
const THEMATIC_BREAK = /^\s*(?:-{3,}|_{3,}|\*{3,})\s*$/
const HEADING = /^(#{1,6})\s+(.*)$/
const FENCE = /^\s*```/
const NUMERIC_CELL = /^[^A-Za-z]*\d[\d.,\s%×/+-]*$/

/**
 * `.prose` prints code on duplicate stock, which is invisible once the sheet
 * itself is duplicate stock. The developer's copy flips it back to paper.
 */
function toneStyles(tone: Tone): ToneContext {
  const onDuplicate = tone === 'duplicate'
  return {
    code: onDuplicate ? 'bg-paper' : '',
    pre: onDuplicate ? 'bg-paper' : '',
  }
}

function inline(source: string, ctx: ToneContext): string {
  const codes: string[] = []

  // Pull code spans out first so emphasis markers inside them stay literal.
  let text = String(source == null ? '' : source).replace(/`([^`]+)`/g, (_match: string, body: string) => {
    codes.push(body)
    return CODE_SLOT(codes.length - 1)
  })

  text = escapeHtml(text)

  // Images become their alt text: a quotation is prose, and remote loads are not
  // something a generated document gets to trigger.
  text = text.replace(IMAGE_RE, (_match: string, alt: string) => alt)

  text = text.replace(LINK_RE, (_match: string, label: string, href: string) => {
    const target = href.replace(/&amp;/g, '&')
    if (!SAFE_HREF.test(target)) return label
    return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`
  })

  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  text = text.replace(/__([^_]+)__/g, '<strong>$1</strong>')
  text = text.replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
  text = text.replace(/(^|[\s(])_([^_\s][^_]*)_(?=$|[\s).,;:!?])/g, '$1<em>$2</em>')

  // Undo the two escapes the backend renderer emits, and only those:
  // `\<` from markdown.py's _clean and `\|` from its _cell. Escaping is already
  // done, so the `<` comes back as the entity and cannot reopen a tag. Anything
  // broader would strip a literal `\*` and let it re-render as emphasis.
  text = text.replace(/\\(&lt;|\|)/g, '$1')

  text = text.replace(CODE_SLOT_RE, (_match: string, index: string) => {
    const body = escapeHtml(codes[Number(index)])
    return ctx.code ? `<code class="${ctx.code}">${body}</code>` : `<code>${body}</code>`
  })

  return text
}

const html = (source: string, ctx: ToneContext) => ({ __html: inline(source, ctx) })

function startsBlock(line: string): boolean {
  return (
    HEADING.test(line) ||
    FENCE.test(line) ||
    THEMATIC_BREAK.test(line) ||
    /^\s*>/.test(line) ||
    LIST_ITEM.test(line)
  )
}

// Cells are separated by *unescaped* pipes only. The server escapes every pipe
// inside model text as `\|` (backend/app/renderers/markdown.py `_cell`), so a
// naive split on `|` invents a cell, shifts every column to its right, and the
// last one — the money column — falls off the end of the row.
//
// Only the pipe escape is undone. Unescaping everything would turn a literal
// `\*` back into markdown emphasis when the cell is rendered inline below.
const splitRow = (row: string): string[] =>
  row
    .trim()
    .replace(/^\|/, '')
    .replace(/(?<!\\)\|$/, '')
    .split(/(?<!\\)\|/)
    .map((cell) => cell.trim().replace(/\\\|/g, '|'))

function collectListItems(lines: string[], start: number): [ListItem[], number] {
  const items: ListItem[] = []
  let index = start

  while (index < lines.length) {
    const match = LIST_ITEM.exec(lines[index])
    if (match) {
      const indent = match[1].replace(/\t/g, '  ').length
      const ordered = ORDERED_ITEM.test(lines[index])
      // A bullet list followed straight after by a numbered one (or the
      // reverse) is two lists, not one. Stop so the caller starts a new block.
      if (items.length > 0 && indent <= items[0].indent && ordered !== items[0].ordered) break
      items.push({ indent, ordered, text: match[2] })
      index += 1
      continue
    }
    // A wrapped continuation line belongs to the item above it.
    if (items.length > 0 && /^\s{2,}\S/.test(lines[index])) {
      items[items.length - 1].text += ` ${lines[index].trim()}`
      index += 1
      continue
    }
    break
  }

  return [items, index]
}

function renderList(
  items: ListItem[],
  cursor: { index: number },
  indent: number,
  key: string,
  ctx: ToneContext,
): ReactElement {
  const ordered = items[cursor.index].ordered
  // Declared with a props shape rather than left to `ReactElement<any>`, so
  // that `previous.props.children` below reads as a node and not as `any`.
  const children: ReactElement<{ children?: ReactNode }>[] = []
  let seq = 0

  while (cursor.index < items.length && items[cursor.index].indent >= indent) {
    const item = items[cursor.index]

    if (item.indent > indent && children.length > 0) {
      const nested = renderList(items, cursor, item.indent, `${key}-n${seq}`, ctx)
      const previous = children[children.length - 1]
      children[children.length - 1] = (
        <li key={previous.key}>
          {previous.props.children}
          {nested}
        </li>
      )
      continue
    }

    cursor.index += 1
    seq += 1
    const task = TASK_ITEM.exec(item.text)
    children.push(
      task ? (
        <li key={`${key}-i${seq}`} className="task">
          <span
            className="task-box"
            data-done={task[1] !== ' '}
            role="img"
            aria-label={task[1] === ' ' ? 'Not done' : 'Done'}
          />
          <span dangerouslySetInnerHTML={html(item.text.slice(task[0].length), ctx)} />
        </li>
      ) : (
        <li key={`${key}-i${seq}`}>
          <span dangerouslySetInnerHTML={html(item.text, ctx)} />
        </li>
      ),
    )
  }

  return ordered ? (
    <ol key={key}>{children}</ol>
  ) : (
    <ul key={key}>{children}</ul>
  )
}

function renderHeading(level: number, text: string, key: string, ctx: ToneContext): ReactElement {
  // The clamp can produce nothing else, but a template literal widens to
  // `string` and JSX will not take a `string` as a tag, so the result is named.
  const Tag = `h${Math.min(Math.max(level, 1), 6)}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6'
  return <Tag key={key} dangerouslySetInnerHTML={html(text, ctx)} />
}

function renderTable(
  header: string[],
  aligns: string[],
  rows: string[][],
  key: string,
  ctx: ToneContext,
): ReactElement {
  const alignOf = (column: number): 'right' | 'center' | undefined => {
    const value = aligns[column]
    return value === 'right' || value === 'center' ? value : undefined
  }

  // A pipe table with an empty header row is a key/value block — the letterhead
  // of a quotation, not a schedule of figures. It gets no header rule, and its
  // last row is not a total, so the heavier rule `.prose` puts above a final
  // bold row has to be taken back off.
  const headerless = header.every((cell) => cell.trim() === '')

  return (
    <table key={key}>
      {headerless ? null : (
        <thead>
          <tr>
            {header.map((cell, column) => (
              <th
                key={`h${column}`}
                scope="col"
                align={alignOf(column)}
                dangerouslySetInnerHTML={html(cell, ctx)}
              />
            ))}
          </tr>
        </thead>
      )}
      <tbody>
        {rows.map((row, rowIndex) => {
          const isLast = rowIndex === rows.length - 1
          return (
            <tr key={`r${rowIndex}`}>
              {header.map((_unused, column) => {
                const cell = row[column] == null ? '' : row[column]
                // Every number is set in mono, including one sitting in a
                // column the author left aligned. Only the typeface changes —
                // moving the cell would break the column it lives in.
                const figure = cell !== '' && !alignOf(column) && NUMERIC_CELL.test(cell)
                const classes = [
                  figure ? 'font-label tabular-nums' : '',
                  headerless && isLast ? 'border-t-0 font-normal' : '',
                ]
                  .filter(Boolean)
                  .join(' ')
                return (
                  <td
                    key={`c${column}`}
                    align={alignOf(column)}
                    className={classes || undefined}
                    dangerouslySetInnerHTML={html(cell, ctx)}
                  />
                )
              })}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function renderBlocks(markdown: string, ctx: ToneContext, prefix = 'b'): ReactElement[] {
  const lines = String(markdown == null ? '' : markdown)
    .replace(/\r\n?/g, '\n')
    .split('\n')
  const blocks: ReactElement[] = []
  let index = 0
  let key = 0

  const nextKey = () => `${prefix}${(key += 1)}`

  while (index < lines.length) {
    const line = lines[index]

    if (!line.trim()) {
      index += 1
      continue
    }

    // A line holding only `&nbsp;` is the signature block asking for air.
    // Python treats a real no-break space as whitespace and strips it, so the
    // renderers agreed on this literal instead - see the note in
    // backend/app/renderers/proposal.py. Every renderer has to know it: the
    // print view and the PDF already do, and this is the third.
    if (line.trim() === '&nbsp;') {
      blocks.push(<span key={nextKey()} className="sign-space" aria-hidden="true" />)
      index += 1
      continue
    }

    if (FENCE.test(line)) {
      const body = []
      index += 1
      while (index < lines.length && !FENCE.test(lines[index])) {
        body.push(lines[index])
        index += 1
      }
      index += 1
      blocks.push(
        <pre key={nextKey()} className={ctx.pre || undefined}>
          <code>{body.join('\n')}</code>
        </pre>,
      )
      continue
    }

    const heading = HEADING.exec(line)
    if (heading) {
      blocks.push(renderHeading(heading[1].length, heading[2], nextKey(), ctx))
      index += 1
      continue
    }

    if (THEMATIC_BREAK.test(line)) {
      blocks.push(<hr key={nextKey()} />)
      index += 1
      continue
    }

    if (/^\s*>/.test(line)) {
      const body = []
      while (index < lines.length && /^\s*>/.test(lines[index])) {
        body.push(lines[index].replace(/^\s*>\s?/, ''))
        index += 1
      }
      const quoteKey = nextKey()
      blocks.push(
        <blockquote key={quoteKey}>{renderBlocks(body.join('\n'), ctx, `${quoteKey}q`)}</blockquote>,
      )
      continue
    }

    if (line.includes('|') && index + 1 < lines.length && TABLE_DIVIDER.test(lines[index + 1])) {
      const header = splitRow(line)
      const aligns = splitRow(lines[index + 1]).map((cell) => {
        if (/^:-+:$/.test(cell)) return 'center'
        if (/^-+:$/.test(cell)) return 'right'
        return 'left'
      })
      index += 2
      const rows = []
      while (index < lines.length && lines[index].trim() && lines[index].includes('|')) {
        rows.push(splitRow(lines[index]))
        index += 1
      }
      blocks.push(renderTable(header, aligns, rows, nextKey(), ctx))
      continue
    }

    if (LIST_ITEM.test(line)) {
      const [items, after] = collectListItems(lines, index)
      if (items.length > 0) {
        blocks.push(renderList(items, { index: 0 }, items[0].indent, nextKey(), ctx))
        index = after
        continue
      }
    }

    const paragraph = []
    while (index < lines.length && lines[index].trim() && !startsBlock(lines[index])) {
      paragraph.push(lines[index].trim())
      index += 1
    }
    if (paragraph.length > 0) {
      blocks.push(<p key={nextKey()} dangerouslySetInnerHTML={html(paragraph.join(' '), ctx)} />)
    } else {
      // Nothing consumed this line; never loop on it.
      index += 1
    }
  }

  return blocks
}

type MarkdownViewProps = {
  markdown: string
  tone?: Tone
}

export default function MarkdownView({ markdown, tone = 'original' }: MarkdownViewProps) {
  const ctx = useMemo(() => toneStyles(tone), [tone])
  const blocks = useMemo(() => renderBlocks(markdown, ctx), [markdown, ctx])

  if (blocks.length === 0) {
    return (
      <p className="font-body text-[15px] leading-[1.6] text-void">
        This document came back empty. Prepare the quotation again, or check the API window for what
        the model returned.
      </p>
    )
  }

  return <div className="prose">{blocks}</div>
}
