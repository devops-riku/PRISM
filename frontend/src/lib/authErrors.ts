/**
 * What went wrong signing in, said to the person trying to sign in.
 *
 * Supabase returns accurate messages written for whoever integrated it:
 * "Invalid login credentials", "User already registered", "For security
 * purposes, you can only request this after 41 seconds". Correct, and none of
 * them tell somebody staring at a password field what to do next.
 *
 * So each one PRISM can recognise is translated, and the translation carries
 * the next move rather than only the diagnosis — "Sign in instead", "Send
 * another", "Wait a minute". Anything unrecognised falls through with the
 * server's own words: a message written for a developer beats a friendly
 * message that is wrong, and it is the only way an operator can debug an
 * install from a screenshot.
 *
 * The codes come from GoTrue's `error_code` field, which is stable. The message
 * matching underneath it is a second net for older releases that only set a
 * message, and is deliberately loose.
 */

export type AuthProblem = {
  /** One sentence, in the app's voice. */
  message: string
  /** What to do about it. Empty when there is nothing useful to add. */
  hint: string
}

/** GoTrue's stable `error_code`, where PRISM has something better to say. */
const BY_CODE: Record<string, AuthProblem> = {
  invalid_credentials: {
    message: 'That email and password do not match.',
    hint: 'Check the password, or use the reset link below.',
  },
  email_not_confirmed: {
    message: 'This email has not been confirmed yet.',
    hint: 'Open the link we sent when the account was made, then sign in.',
  },
  user_already_exists: {
    message: 'That email already has an account.',
    hint: 'Sign in instead — or reset the password if you have lost it.',
  },
  email_exists: {
    message: 'That email already has an account.',
    hint: 'Sign in instead — or reset the password if you have lost it.',
  },
  user_banned: {
    message: 'This account has been suspended.',
    hint: 'Whoever administers this workspace can lift it.',
  },
  weak_password: {
    message: 'That password is too easy to guess.',
    hint: 'Six characters minimum, and not one you use elsewhere.',
  },
  same_password: {
    message: 'That is the password you already have.',
    hint: 'Choose a different one.',
  },
  over_email_send_rate_limit: {
    message: 'Too many emails, too quickly.',
    hint: 'Wait a minute, then ask for another.',
  },
  over_request_rate_limit: {
    message: 'Too many attempts from here.',
    hint: 'Wait a minute, then try again.',
  },
  // GoTrue answers "Token has expired or is invalid" for THREE different
  // situations and gives no way to tell them apart: the code really did
  // expire, the digits are wrong, or the token was already spent. That last
  // one bites without anybody typing twice - if the email carries a link as
  // well, a scanner or a click-tracking redirect can follow it and consume
  // the token before the person reads the code.
  //
  // So this must not assert expiry. Saying "expired" about a code that is
  // thirty seconds old sends somebody looking for a clock problem that does
  // not exist.
  otp_expired: {
    message: 'That code did not work.',
    hint: 'It may have expired, been mistyped, or already been used. Ask for another.',
  },
  otp_disabled: {
    message: 'Sign-in codes are turned off for this install.',
    hint: 'Use a password instead.',
  },
  signup_disabled: {
    message: 'New accounts are turned off for this install.',
    hint: 'Ask whoever set PRISM up to invite you.',
  },
  email_address_invalid: {
    message: 'That email address is not valid.',
    hint: '',
  },
  validation_failed: {
    message: 'Check the details and try again.',
    hint: '',
  },
  session_expired: {
    message: 'That session has expired.',
    hint: 'Sign in again.',
  },

  // --- Coming back from Google or Facebook ---------------------------------
  //
  // These arrive on the URL after the provider has sent the browser back, not
  // as a rejected promise - see `lib/oauthReturn.ts`. They are worded for
  // somebody who just pressed a button and landed here again, so each one says
  // whether the remedy is theirs or somebody else's; "contact your
  // administrator" for a thing the person can simply retry is as unhelpful as
  // silence.
  access_denied: {
    message: 'That sign-in was cancelled.',
    hint: 'Nothing was shared and no account was created. Try again, or use your email above.',
  },
  provider_email_needs_verification: {
    message: 'That account has no verified email address.',
    hint: 'Verify your email with the provider first, then sign in here again.',
  },
  server_error: {
    message: 'The sign-in service could not finish that.',
    hint: 'Try again in a moment. If it keeps happening the provider may be misconfigured.',
  },
  temporarily_unavailable: {
    message: 'The sign-in provider is not answering right now.',
    hint: 'Try again in a minute, or use your email above.',
  },
  bad_oauth_state: {
    message: 'That sign-in could not be matched to the one that started it.',
    hint: 'It may have been left open too long. Start it again from this page.',
  },
  bad_oauth_callback: {
    message: 'The provider sent back an answer PRISM could not read.',
    hint: 'Usually a redirect URL that does not match. Try again, or use your email above.',
  },
  unexpected_failure: {
    message: 'The sign-in service hit an error it did not explain.',
    hint: 'Try again. If it keeps happening, whoever set PRISM up will find it in the sign-in logs.',
  },
}

/**
 * Matched on the message BEFORE the code is consulted, which is the opposite of
 * how everything else here works and needs its reason stated.
 *
 * GoTrue answers a sign-in through a provider nobody turned on with
 * `error_code: "validation_failed"` and the message "Unsupported provider:
 * provider is not enabled". That code is shared with genuine bad input, so the
 * ordinary path would return "Check the details and try again." - which tells a
 * studio they mistyped something, when in fact there is nothing on this screen
 * they could type differently. The remedy is not theirs at all; it is somebody
 * turning the provider on in the Supabase project.
 *
 * Only for failures where a shared code would actively mislead. Everything that
 * a code describes correctly belongs in `BY_CODE`.
 */
const BEFORE_CODE: Array<[RegExp, AuthProblem]> = [
  [
    /unsupported provider|provider is not enabled/i,
    {
      message: 'That sign-in method is not switched on for this install.',
      hint: 'Use your email and password, or ask whoever set PRISM up to switch it on.',
    },
  ],
  [
    // The provider handed back a code the account service could not trade for a
    // token - almost always a redirect URL or a client secret that does not
    // match what the provider has on file. It arrives under `error=server_error`,
    // whose own wording is a shrug ("try again in a moment"), and trying again
    // is precisely what will not fix a mismatched secret. Hence ahead of the
    // code: the specific cause is known and worth saying.
    /unable to exchange external code|invalid client|redirect_uri_mismatch/i,
    {
      message: 'The provider would not complete that sign-in.',
      hint: "Usually the app's redirect address or its secret does not match what the provider has on file. Use your email above meanwhile.",
    },
  ],
]

/** A second net: releases that set only a message, matched loosely. */
const BY_MESSAGE: Array<[RegExp, AuthProblem]> = [
  [/invalid login credentials/i, BY_CODE.invalid_credentials],
  [/email not confirmed/i, BY_CODE.email_not_confirmed],
  [/already registered|already exists/i, BY_CODE.user_already_exists],
  [/password should be at least|password is too weak/i, BY_CODE.weak_password],
  // "you can only request this after 41 seconds" is GoTrue throttling an email
  // send, so it gets the email wording rather than the general one.
  [/only request this after|email rate limit/i, BY_CODE.over_email_send_rate_limit],
  [/rate limit|too many requests/i, BY_CODE.over_request_rate_limit],
  [/token has expired|otp.*expired|expired.*code/i, BY_CODE.otp_expired],
  [/signups? not allowed|signup is disabled/i, BY_CODE.signup_disabled],
  [/unable to validate email|invalid email/i, BY_CODE.email_address_invalid],
  // Same address already has a PRISM account made another way. Linking is a
  // deliberate act in Supabase, so the honest advice is to use the first method.
  [
    /already registered with|identity is already linked|email address is already/i,
    {
      message: 'That address already signs in a different way.',
      hint: 'Use the email and password below, or the code option, for this account.',
    },
  ],
  [
    // Fetch rejects with a bare TypeError when the host is unreachable, which
    // is the one failure that is never the person's fault.
    /failed to fetch|networkerror|load failed/i,
    {
      message: 'Could not reach the sign-in service.',
      hint: 'Check your connection, or whether the API is running.',
    },
  ],
]

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

  const message = readString(failure, 'message')

  // Ahead of the code lookup on purpose - see `BEFORE_CODE`.
  for (const [pattern, problem] of BEFORE_CODE) {
    if (pattern.test(message)) return problem
  }

  const code = readString(failure, 'code') || readString(failure, 'error_code')
  const known = BY_CODE[code]
  if (known) return known

  for (const [pattern, problem] of BY_MESSAGE) {
    if (pattern.test(message)) return problem
  }

  // Unrecognised. The server's own sentence, ending in a full stop so it reads
  // as a sentence rather than a log line.
  if (message.trim()) {
    const said = message.trim()
    return { message: /[.!?]$/.test(said) ? said : `${said}.`, hint: '' }
  }

  return { message: 'That did not work.', hint: 'Try again in a moment.' }
}
