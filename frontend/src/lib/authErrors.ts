/**
 * What went wrong signing in, said to the person trying to sign in.
 *
 * PRISM's sign-in API already answers in sentences meant for the person at the
 * keyboard ("That code isn’t right. 3 tries left."), with a stable `code`
 * beside each one. So the sentence is shown as it is, and this adds only the
 * part a server cannot know is useful: the next move — "Send another", "Use a
 * code instead", "Wait a minute". Anything unrecognised falls through with the
 * server's own words: a message written for a developer beats a friendly
 * message that is wrong, and it is the only way an operator can debug an
 * install from a screenshot.
 */

export type AuthProblem = {
  /** One sentence, in the app's voice. */
  message: string
  /** What to do about it. Empty when there is nothing useful to add. */
  hint: string
}

/** The next move for each of the API's stable codes, where there is one. */
const HINTS: Record<string, string> = {
  WRONG_PASSWORD: 'Check the password, or use “Email me a sign-in code” instead.',
  INVALID_CODE: 'Check the newest email — asking again makes earlier codes stop working.',
  CODE_EXPIRED: 'Codes last ten minutes. Send another.',
  TOO_MANY_TRIES: 'Ask for a new code and use that one.',
  SIGN_IN_EXPIRED: 'Codes are only waited for ten minutes. Start again from here.',
  RATE_LIMITED: 'This is a guard against guessing, not a fault. Wait a few minutes.',
  WEAK_PASSWORD: '',
  INVALID_EMAIL: '',
  AMBIGUOUS_ACCOUNT: '',
  SIGN_IN_UNAVAILABLE: 'Nothing you typed was wrong. If it keeps happening, the sign-in service is down.',
  SIGN_IN_OFF: 'Whoever set PRISM up has not finished configuring sign-in.',
  UNKNOWN_PROVIDER: 'Use your email instead.',
  PROVIDER_NO_EMAIL: 'Use your email instead, or share your email with the provider and try again.',
  NETWORK: 'Check your connection, or whether the API is running.',
}

/** Failures that arrive on the URL after a provider sent the browser back. */
const RETURNS: Record<string, AuthProblem> = {
  PROVIDER_CANCELLED: {
    message: 'That sign-in was cancelled.',
    hint: 'Nothing was shared and no account was created. Try again, or use your email above.',
  },
  PROVIDER_FAILED: {
    message: 'That sign-in didn’t go through.',
    hint: 'Try again in a moment, or use your email above.',
  },
}

function readString(source: object, key: string): string {
  const value = (source as Record<string, unknown>)[key]
  return typeof value === 'string' ? value : ''
}

/**
 * Turn whatever was thrown into something worth showing.
 *
 * Never throws and never returns an empty message: a sign-in screen that fails
 * silently is worse than one that fails badly.
 */
export function describeAuthError(failure: unknown): AuthProblem {
  if (typeof failure === 'string' && failure.trim()) {
    return { message: failure.trim(), hint: '' }
  }

  if (typeof failure !== 'object' || failure === null) {
    return { message: 'That did not work.', hint: 'Try again in a moment.' }
  }

  const code = readString(failure, 'code')
  const said = readString(failure, 'message').trim()

  const returned = RETURNS[code]
  if (returned) {
    return said ? { message: returned.message, hint: said } : returned
  }

  // Fetch rejects with a bare TypeError when the host is unreachable, which
  // is the one failure that is never the person's fault.
  if (/failed to fetch|networkerror|load failed/i.test(said)) {
    return { message: 'Could not reach PRISM’s server.', hint: HINTS.NETWORK }
  }

  if (said) {
    return { message: /[.!?]$/.test(said) ? said : `${said}.`, hint: HINTS[code] || '' }
  }

  return { message: 'That did not work.', hint: 'Try again in a moment.' }
}
