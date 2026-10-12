"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, login } from "@/lib/api";
import { LANGS, Lang, getLang, setLang, t } from "@/lib/i18n";

export default function LoginPage() {
  const router = useRouter();
  const [lang, setLangState] = useState<Lang>("en");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [slug, setSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setLangState(getLang());
  }, []);

  function switchLang(next: Lang) {
    setLang(next);
    setLangState(next);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login({ email, password, organization_slug: slug || undefined });
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(
          "Multiple clinics match this account — enter the clinic slug.",
        );
      } else if (err instanceof ApiError && err.status === 401) {
        setError("Invalid credentials.");
      } else {
        setError("Could not sign in. Is the API running?");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header className="app-header">
        <span className="brand">{t("brand", lang)}</span>
        <span className="spacer" />
        <span className="lang-switch" role="group" aria-label="Language">
          {LANGS.map((l) => (
            <button
              key={l.code}
              type="button"
              aria-pressed={lang === l.code}
              onClick={() => switchLang(l.code)}
            >
              {l.native}
            </button>
          ))}
        </span>
      </header>
      <main className="page">
        <div className="card" style={{ maxWidth: 520 }}>
          <h1 style={{ marginTop: 0 }}>{t("signIn", lang)}</h1>
          <form className="stack" onSubmit={onSubmit}>
            <label className="field">
              {t("email", lang)}
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                required
              />
            </label>
            <label className="field">
              {t("password", lang)}
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </label>
            <label className="field">
              {t("clinicSlug", lang)}
              <input
                type="text"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                placeholder="sunrise-clinic"
              />
            </label>
            {error ? (
              <p className="error" role="alert">
                {error}
              </p>
            ) : null}
            <button className="primary" type="submit" disabled={busy}>
              {busy ? "…" : t("signIn", lang)}
            </button>
          </form>
          <p className="muted" style={{ marginTop: "1.25rem" }}>
            New clinic? Register via <code>POST /api/v1/auth/register</code>{" "}
            (self-serve onboarding ships in M2).
          </p>
        </div>
      </main>
    </>
  );
}
