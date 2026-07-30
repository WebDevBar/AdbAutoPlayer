import { Instant } from "@js-joda/core";

function escapeHtml(unsafe: string): string {
  return unsafe
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

/**
 * Markup the log is allowed to carry, as a fixed allowlist.
 *
 * Log lines contain device output and exception text, so `escapeHtml` neutralises
 * everything by default and that stays true - this re-permits exactly four classes and
 * nothing else. The pattern matches an OPENING tag, its text, and its CLOSING tag as one
 * unit, so an orphan `</span>` in arbitrary log text can never become real markup, and no
 * attribute other than the four class names below can survive.
 *
 * Emitted by the Python side: `odds.py` (_FAVOURED_CLASS, emphasise_side) and the SC-75
 * result line. Styled in `LogEntry.svelte`.
 *
 * Learned the hard way, 2026-07-30: a doc note claimed inline markup already worked
 * because the renderer uses {@html}. It does - but every message is escaped before it
 * reaches the renderer, so release 23 printed raw `<span class="sc-red">` on screen.
 */
const ALLOWED_SPAN =
  /&lt;span class=&quot;(sc-blue|sc-red|sc-hit|sc-miss)&quot;&gt;((?:(?!&lt;)[\s\S])*?)&lt;\/span&gt;/g;

export function restoreAllowedMarkup(escaped: string): string {
  return escaped.replace(ALLOWED_SPAN, '<span class="$1">$2</span>');
}

export function formatMessage(message: string): string {
  const urlRegex = /(https?:\/\/[^\s'"]+)/g;
  // Refined regex for Windows paths and Unix paths
  // 1. Windows drive: \b[a-zA-Z]:\\...
  // 2. %USERPROFILE%: %USERPROFILE%\\...
  // 3. Unix home: ~/...
  const pathRegex =
    /(\b[a-zA-Z]:\\[\\\w\s\.\-!@#$%^&()]+|%USERPROFILE%\\[\\\w\s\.\-!@#$%^&()]+|~[\/\w\s\.\-!@#$%^&()]+)/g;

  const withLinks = escapeHtml(message)
    .replace(urlRegex, '<a class="anchor" href="$1" target="_blank">$1</a>')
    .replace(pathRegex, (match) => {
      // If it looks like it's inside an HTML tag (already replaced by urlRegex), skip it
      if (match.includes("://")) return match;
      return `<span class="path-link cursor-pointer underline text-accent hover:text-accent-hi" data-path="${match}">${match}</span>`;
    })
    .replace(/\r?\n/g, "<br>");

  return restoreAllowedMarkup(withLinks);
}

export function getLogClass(message: string): string {
  if (message.includes("[DEBUG]")) return "text-primary-500";
  if (message.includes("[INFO]")) return "text-success-500";
  if (message.includes("[WARNING]")) return "text-warning-500";
  if (message.includes("[ERROR]")) return "text-error-500";
  if (message.includes("[FATAL]")) return "text-error-950";
  return "text-primary-50";
}

function sanitizeMessage(message: string): string {
  // Regex to match Windows user-profile path: C:\Users\username\
  const windowsUserPathRegex = /C:\\Users\\[^\\]+\\/gi;
  // Regex to match macOS user-profile path: /Users/username/
  const macosUserPathRegex = /\/Users\/[^/]+\//gi;
  // Regex to match Linux user-profile path: /home/username/
  const linuxUserPathRegex = /\/home\/[^/]+\//gi;

  let sanitized = message.replace(windowsUserPathRegex, "%USERPROFILE%\\");
  sanitized = sanitized.replace(macosUserPathRegex, "~/");
  sanitized = sanitized.replace(linuxUserPathRegex, "~/");

  return sanitized;
}

export function logMessageToTextDisplayCardItem(
  logMessage: LogMessage,
): TextDisplayCardItem {
  const sanitized = sanitizeMessage(logMessage.message);
  const formatted = formatMessage(sanitized);
  const message = `[${logMessage.level}] ${formatted}`;

  return {
    message,
    timestamp: Instant.parse(logMessage.timestamp),
    html_class: logMessage.html_class ?? getLogClass(message),
  };
}

export type TextDisplayCardItem = {
  message: string;
  timestamp: Instant;
  html_class: string;
};
