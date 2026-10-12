"use client";

/**
 * Token storage (M1: localStorage).
 *
 * Known trade-off, documented in SECURITY_CHECKLIST.md: M1 stores tokens in
 * localStorage so the scaffold works end-to-end. Milestone M4/M7 should move
 * refresh tokens to httpOnly+SameSite cookies with CSRF protection, keeping
 * only the short-lived access token in memory.
 */
export type TokenPair = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
};

const ACCESS_KEY = "hms.access_token";
const REFRESH_KEY = "hms.refresh_token";

export function saveTokens(tokens: TokenPair): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(ACCESS_KEY, tokens.access_token);
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACCESS_KEY);
}

export function clearTokens(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}
