"use client";

/**
 * Appointments: day list with filters plus the booking form (patient,
 * branch, slot, reason). Provider conflicts surface as 409 from the API.
 */
import { FormEvent, useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ApiError, Appointment, Branch, PageOf, Patient, api } from "@/lib/api";
import { getLang, t } from "@/lib/i18n";

type Lang = "en" | "mr" | "hi";

const EMPTY = { patient_id: "", branch_id: "", date: "", time: "", minutes: "30", reason: "" };

export default function AppointmentsPage() {
  const [lang, setLang] = useState<Lang>("en");
  const [items, setItems] = useState<Appointment[]>([]);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => setLang(getLang()), []);

  const load = useCallback(async () => {
    try {
      const [apptPage, patientPage, branchList] = await Promise.all([
        api.get<PageOf<Appointment>>("/appointments?limit=50"),
        api.get<PageOf<Patient>>("/patients?limit=50"),
        api.get<Branch[]>("/branches"),
      ]);
      setItems(apptPage.items);
      setPatients(patientPage.items);
      setBranches(branchList.filter((b) => b.status === "active"));
      setForm((f) => ({ ...f, branch_id: f.branch_id || branchList[0]?.id || "" }));
    } catch {
      setError("Could not load appointments.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const startsAt = new Date(`${form.date}T${form.time}:00+05:30`);
    const endsAt = new Date(startsAt.getTime() + Number(form.minutes) * 60_000);
    try {
      await api.post("/appointments", {
        patient_id: form.patient_id,
        branch_id: form.branch_id,
        starts_at: startsAt.toISOString(),
        ends_at: endsAt.toISOString(),
        reason: form.reason,
      });
      setNotice("Appointment booked.");
      setForm({ ...EMPTY, branch_id: form.branch_id });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Booking failed");
    }
  }

  async function cancel(id: string) {
    setError(null);
    try {
      await api.patch(`/appointments/${id}`, { status: "cancelled" });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Cancel failed");
    }
  }

  const nameOf = (id: string) =>
    patients.find((p) => p.id === id)?.full_name ?? "—";

  return (
    <AppShell active="appointments">
      <main className="page">
        <h1 style={{ marginTop: 0 }}>{t("appointments", lang)}</h1>

        <section className="card">
          <h3 style={{ marginTop: 0 }}>{t("createAppointment", lang)}</h3>
          <form onSubmit={onSubmit} style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <div className="field">
              <label htmlFor="apq">{t("patients", lang)}</label>
              <select
                id="apq"
                required
                value={form.patient_id}
                onChange={(e) => setForm({ ...form, patient_id: e.target.value })}
              >
                <option value="">—</option>
                {patients.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.uhid} — {p.full_name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="apb">{t("branch", lang)}</label>
              <select
                id="apb"
                value={form.branch_id}
                onChange={(e) => setForm({ ...form, branch_id: e.target.value })}
              >
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="apd">{t("dob", lang)}</label>
              <input
                id="apd"
                type="date"
                required
                value={form.date}
                onChange={(e) => setForm({ ...form, date: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="apt">{t("startsAt", lang)}</label>
              <input
                id="apt"
                type="time"
                required
                value={form.time}
                onChange={(e) => setForm({ ...form, time: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="apr">{t("reason", lang)}</label>
              <input
                id="apr"
                value={form.reason}
                onChange={(e) => setForm({ ...form, reason: e.target.value })}
              />
            </div>
            <button className="primary" type="submit" style={{ alignSelf: "end" }}>
              {t("createAppointment", lang)}
            </button>
          </form>
          {notice && <p className="notice">{notice}</p>}
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <table>
            <thead>
              <tr>
                <th>{t("startsAt", lang)}</th>
                <th>{t("patients", lang)}</th>
                <th>{t("reason", lang)}</th>
                <th>{t("status", lang)}</th>
                <th>{t("actions", lang)}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.id}>
                  <td>{new Date(a.starts_at).toLocaleString()}</td>
                  <td>{nameOf(a.patient_id)}</td>
                  <td>{a.reason || "—"}</td>
                  <td>{a.status}</td>
                  <td>
                    {(a.status === "scheduled" || a.status === "checked_in") && (
                      <button
                        className="ghost"
                        type="button"
                        onClick={() => void cancel(a.id)}
                      >
                        {t("cancel", lang)}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    No appointments yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </main>
    </AppShell>
  );
}
