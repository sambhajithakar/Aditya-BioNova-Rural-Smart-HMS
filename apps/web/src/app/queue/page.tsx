"use client";

/**
 * OPD queue board: today's tokens with call/start/complete/skip actions and
 * the reception flow to add a patient to the queue (walk-in or appointment).
 * The backend state machine refuses illegal transitions; the UI reflects it.
 */
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ApiError, Branch, PageOf, Patient, QueueEntry, api } from "@/lib/api";
import { getLang, t } from "@/lib/i18n";

type Lang = "en" | "mr" | "hi";

export default function QueuePage() {
  const [lang, setLang] = useState<Lang>("en");
  const [board, setBoard] = useState<QueueEntry[]>([]);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [patientId, setPatientId] = useState("");
  const [branchId, setBranchId] = useState("");
  const [priority, setPriority] = useState("normal");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => setLang(getLang()), []);

  const load = useCallback(async () => {
    try {
      const [queue, patientPage, branchList] = await Promise.all([
        api.get<PageOf<QueueEntry>>("/opd/queue?limit=100"),
        api.get<PageOf<Patient>>("/patients?limit=50"),
        api.get<Branch[]>("/branches"),
      ]);
      setBoard(queue.items);
      setPatients(patientPage.items);
      setBranches(branchList.filter((b) => b.status === "active"));
      setBranchId((prev) => prev || branchList[0]?.id || "");
    } catch {
      setError("Could not load the queue.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(entryId: string, action: string) {
    setError(null);
    try {
      await api.post(`/opd/queue/${entryId}/${action}`, {});
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Action failed");
    }
  }

  async function addToQueue() {
    if (!patientId || !branchId) {
      setError("Pick a patient and a branch first.");
      return;
    }
    setError(null);
    try {
      const entry = await api.post<QueueEntry>(
        "/opd/queue",
        { patient_id: patientId, branch_id: branchId, priority },
        `web-queue-${patientId}-${new Date().toISOString().slice(0, 10)}`
      );
      setNotice(`Token ${entry.token_number} issued.`);
      setPatientId("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Could not add");
    }
  }

  const ACTION_BY_STATUS: Record<string, { label: string; path: string }[]> = {
    waiting: [
      { label: t("call", lang), path: "call" },
      { label: t("skip", lang), path: "skip" },
    ],
    called: [
      { label: t("start", lang), path: "start" },
      { label: t("skip", lang), path: "skip" },
    ],
    in_consultation: [{ label: t("complete", lang), path: "complete" }],
    skipped: [{ label: t("call", lang), path: "call" }],
    completed: [],
    cancelled: [],
  };

  return (
    <AppShell active="queue">
      <main className="page">
        <h1 style={{ marginTop: 0 }}>{t("queueBoard", lang)}</h1>

        <section className="card">
          <h3 style={{ marginTop: 0 }}>{t("addQueue", lang)}</h3>
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <div className="field">
              <label htmlFor="pq">{t("patients", lang)}</label>
              <select
                id="pq"
                value={patientId}
                onChange={(e) => setPatientId(e.target.value)}
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
              <label htmlFor="bq">{t("branch", lang)}</label>
              <select
                id="bq"
                value={branchId}
                onChange={(e) => setBranchId(e.target.value)}
              >
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="pq_priority">{t("status", lang)}</label>
              <select
                id="pq_priority"
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
              >
                <option value="normal">normal</option>
                <option value="urgent">urgent</option>
              </select>
            </div>
            <button
              className="primary"
              type="button"
              style={{ alignSelf: "end" }}
              onClick={() => void addToQueue()}
            >
              {t("addQueue", lang)}
            </button>
          </div>
          {notice && <p className="notice">{notice}</p>}
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <table>
            <thead>
              <tr>
                <th>{t("token", lang)}</th>
                <th>{t("patients", lang)}</th>
                <th>{t("status", lang)}</th>
                <th>{t("actions", lang)}</th>
              </tr>
            </thead>
            <tbody>
              {board.map((e) => {
                const patient = patients.find((p) => p.id === e.patient_id);
                return (
                  <tr key={e.id}>
                    <td>
                      <strong>#{e.token_number}</strong>
                      {e.priority === "urgent" && (
                        <span className="tag">urgent</span>
                      )}
                    </td>
                    <td>{patient ? `${patient.uhid} — ${patient.full_name}` : e.patient_id}</td>
                    <td>{e.status}</td>
                    <td>
                      {(ACTION_BY_STATUS[e.status] ?? []).map((a) => (
                        <button
                          key={a.path}
                          className="ghost"
                          type="button"
                          onClick={() => void act(e.id, a.path)}
                        >
                          {a.label}
                        </button>
                      ))}
                    </td>
                  </tr>
                );
              })}
              {board.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    Queue is empty.
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
