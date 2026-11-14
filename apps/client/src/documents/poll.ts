/** How often a statement being made is read again, in milliseconds. Most take seconds. */
export const STATEMENT_POLL_MS = 2_000;
/** After this many reads the screen stops asking and says the statement will arrive by email. */
export const STATEMENT_POLL_LIMIT = 30;

/** In light mode the statement is read less often, over the same minute before the screen gives up. */
export const LIGHT_STATEMENT_POLL_MS = 5_000;
export const LIGHT_STATEMENT_POLL_LIMIT = 12;
