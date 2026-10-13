"use client";

/**
 * Patients: search (name/phone/UHID), supervised duplicate-aware registration
 * and the patient profile card. Duplicate 409s show the candidates and ask
 * for an explicit confirmation — the workflow the blueprint requires.
 */
import { FormEvent, useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ApiError, DuplicateCandidate, PageOf, Patient, api } from "@/lib/api";
import { getLang, t } from "@/lib/i18n";

type Lang = "en" | "mr" | "hi";

const EMPTY_FORM = {
  full_name: "",
  gender: "female",
  date_of_birth: "",
  phone: "",
  address: "",
  blood_group: "",
  allergies: "",
};

export default function PatientsPage() {
  const [lang, setLang] = useState<Lang>("en");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Patient[]>([]);
  const [selected, setSelected] = useState<Patient | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [duplicates, setDuplicates] = useState<DuplicateCandidate[]>([]);
  const [pendingAllow, setPendingAllow] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => setLang(getLang()), []);

  const search = useCallback(async (q: string) => {
    try {
      const page = await api.get<PageOf<Patient>>(
        `/patients?limit=20${q ? `&q=${encodeURIComponent(q)}` : ""}`
      );
      setResults(page.items);
    } catch {
      setError("Search failed");
    }
  }, []);

  useEffect(() => {
    void search("");
  }, [search]);

  async function submit(allowDuplicates: boolean) {
    setBusy(true);
    setError(null);
    setDuplicates([]);
    try {
      const payload: Record<string, unknown> = {
        ...form,
        date_of_birth: form.date_of_birth || null,
        blood_group: form.blood_group || null,
        allow_duplicates: allowDuplicates,
      };
      const created = await api.post<Patient>("/patients", payload);
      setNotice(`Registered ${created.full_name} — UHID ${created.uhid}`);
      setForm(EMPTY_FORM);
      setPendingAllow(false);
      void search(query);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const detail = err.detail as {
          candidates?: DuplicateCandidate[];
          message?: string;
        };
        if (detail.candidates?.length) {
          setDuplicates(detail.candidates);
          setPendingAllow(true);
        } else {
          setError(detail.message ?? "Conflict");
        }
      } else {
        setError(err instanceof ApiError ? String(err.message) : "Request failed");
      }
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void submit(false);
  }

  return (
    <AppShell active="patients">
      <main className="page">
        <p className="notice">{t("m2Notice", lang)}</p>

        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: "1.25rem" }}>
          <section className="card">
            <h2 style={{ marginTop: 0 }}>{t("patients", lang)}</h2>
            <div className="field">
              <label htmlFor="q">{t("searchPatient", lang)}</label>
              <input
                id="q"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  void search(e.target.value);
                }}
                placeholder={t("searchPatient", lang)}
              />
            </div>
            <table>
              <thead>
                <tr>
                  <th>{t("uhid", lang)}</th>
                  <th>{t("fullName", lang)}</th>
                  <th>{t("phone", lang)}</th>
                </tr>
              </thead>
              <tbody>
                {results.map((p) => (
                  <tr key={p.id}>
                    <td>{p.uhid}</td>
                    <td>
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => setSelected(p)}
                      >
                        {p.full_name}
                      </button>
                    </td>
                    <td>{p.phone ?? "—"}</td>
                  </tr>
                ))}
                {results.length === 0 && (
                  <tr>
                    <td colSpan={3} className="muted">
                      No patients yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </section>

          <section className="card">
            <h2 style={{ marginTop: 0 }}>{t("registerPatient", lang)}</h2>
            <form className="stack" onSubmit={onSubmit}>
              <div className="field">
                <label htmlFor="full_name">{t("fullName", lang)}</label>
                <input
                  id="full_name"
                  required
                  minLength={2}
                  value={form.full_name}
                  onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="gender">{t("gender", lang)}</label>
                <select
                  id="gender"
                  value={form.gender}
                  onChange={(e) => setForm({ ...form, gender: e.target.value })}
                >
                  <option value="female">female</option>
                  <option value="male">male</option>
                  <option value="other">other</option>
                  <option value="unknown">unknown</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="dob">{t("dob", lang)}</label>
                <input
                  id="dob"
                  type="date"
                  value={form.date_of_birth}
                  onChange={(e) => setForm({ ...form, date_of_birth: e.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="phone">{t("phone", lang)}</label>
                <input
                  id="phone"
                  inputMode="tel"
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="blood">{t("bloodGroup", lang)}</label>
                <select
                  id="blood"
                  value={form.blood_group}
                  onChange={(e) => setForm({ ...form, blood_group: e.target.value })}
                >
                  <option value="">—</option>
                  {["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"].map((b) => (
                    <option key={b} value={b}>{b}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="address">{t("address", lang)}</label>
                <input
                  id="address"
                  value={form.address}
                  onChange={(e) => setForm({ ...form, address: e.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="allergies">{t("allergies", lang)}</label>
                <input
                  id="allergies"
                  value={form.allergies}
                  onChange={(e) => setForm({ ...form, allergies: e.target.value })}
                />
              </div>

              {duplicates.length > 0 && (
                <div className="error" role="alert">
                  <p style={{ marginTop: 0 }}>{t("duplicateWarn", lang)}</p>
                  <ul>
                    {duplicates.map((d) => (
                      <li key={d.id}>
                        <strong>{d.uhid}</strong> — {d.full_name} (
                        {d.matched_on.join(", ")})
                      </li>
                    ))}
                  </ul>
                  <p className="muted">{t("candidates", lang)}</p>
                </div>
              )}

              <div style={{ display: "flex", gap: "0.5rem" }}>
                <button className="primary" type="submit" disabled={busy}>
                  {t("registerPatient", lang)}
                </button>
                {pendingAllow && (
                  <button
                    className="ghost"
                    type="button"
                    disabled={busy}
                    onClick={() => void submit(true)}
                  >
                    {t("confirmCreate", lang)}
                  </button>
                )}
              </div>
            </form>
          </section>
        </div>

        {notice && <p className="notice">{notice}</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}

        {selected && (
          <section className="card" style={{ marginTop: "1.25rem" }}>
            <h3 style={{ marginTop: 0 }}>
              {selected.full_name} <span className="tag">{selected.uhid}</span>
            </h3>
            <p className="muted" style={{ margin: "0.25rem 0" }}>
              {t("gender", lang)}: {selected.gender} · {t("dob", lang)}:{" "}
              {selected.date_of_birth ?? "—"} · {t("phone", lang)}:{" "}
              {selected.phone ?? "—"} · {t("bloodGroup", lang)}:{" "}
              {selected.blood_group ?? "—"}
            </p>
            <p className="muted" style={{ margin: "0.25rem 0" }}>
              {t("address", lang)}: {selected.address || "—"}
            </p>
            <p className="error" style={{ margin: "0.25rem 0" }}>
              {t("allergies", lang)}: {selected.allergies || "none recorded"}
            </p>
          </section>
        )}
      </main>
    </AppShell>
  );
}
