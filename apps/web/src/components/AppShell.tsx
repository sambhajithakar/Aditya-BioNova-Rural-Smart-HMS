"use client";

/**
 * Shared authenticated shell: header with language switcher + the module
 * navigation. Every M2 page wraps its content in <AppShell>.
 */
import { ReactNode, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Me, api } from "@/lib/api";
import { clearTokens } from "@/lib/auth";
import { LANGS, Lang, getLang, setLang, t } from "@/lib/i18n";

const NAV: { key: string; href: string }[] = [
  { key: "dashboard", href: "/dashboard" },
  { key: "patients", href: "/patients" },
  { key: "appointments", href: "/appointments" },
  { key: "queue", href: "/queue" },
  { key: "consultation", href: "/consultation" },
  { key: "billing", href: "/billing" },
  { key: "pharmacy", href: "/pharmacy" },
  { key: "lab", href: "/lab" },
];

export function AppShell({
  children,
  active,
}: {
  children: ReactNode;
  active: string;
}) {
  const router = useRouter();
  const [lang, setLangState] = useState<Lang>("en");
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    setLangState(getLang());
    api
      .get<Me>("/auth/me")
      .then(setMe)
      .catch(() => router.replace("/login"));
  }, [router]);

  function signOut() {
    void api.post("/auth/logout", {}).catch(() => undefined);
    clearTokens();
    router.replace("/login");
  }

  return (
    <>
      <header className="app-header">
        <span className="brand">{t("brand", lang)}</span>
        {me && (
          <span className="context">
            {me.organization.name} <span className="muted">/</span>{" "}
            {me.user.full_name}
          </span>
        )}
        <span className="spacer" />
        <span className="lang-switch" role="group" aria-label="Language">
          {LANGS.map((l) => (
            <button
              key={l.code}
              type="button"
              aria-pressed={lang === l.code}
              onClick={() => {
                setLang(l.code);
                setLangState(l.code);
              }}
            >
              {l.native}
            </button>
          ))}
        </span>
        <button className="ghost" type="button" onClick={signOut}>
          {t("logout", lang)}
        </button>
      </header>

      <nav className="layout-nav" aria-label="Modules">
        {NAV.map((item) => (
          <a
            key={item.key}
            href={item.href}
            className={item.key === active ? "active" : undefined}
          >
            {t(item.key, lang)}
          </a>
        ))}
      </nav>

      {children}
    </>
  );
}
