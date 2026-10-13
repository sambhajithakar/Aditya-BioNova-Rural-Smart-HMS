"use client";

/**
 * Doctor's consultation workspace (M3): open an encounter from the queue,
 * record vitals + structured diagnosis, draft and sign prescriptions with
 * the server's allergy/duplicate safety gate, then close the encounter.
 */
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  ApiError,
  Branch,
  Drug,
  Encounter,
  EncounterDetail,
  PageOf,
  Patient,
  PrescriptionDetail,
  QueueEntry,
  RxWarnings,
  api,
} from "@/lib/api";
import { getLang, t } from "@/lib/i18n";

type Lang = "en" | "mr" | "hi";

type DraftItem = { drug_id: string; dose: string; frequency: string; duration_days: number };

const EMPTY_VITALS = {
  bp_systolic: "",
  bp_diastolic: "",
  pulse: "",
  temperature_c: "",
  spo2: "",
  weight_kg: "",
};

export default function ConsultationPage() {
  const [lang, setLang] = useState<Lang>("en");
  const [patients, setPatients] = useState<Patient[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [queue, setQueue] = useState<QueueEntry[]>([]);
  const [open, setOpen] = useState<EncounterDetail | null>(null);
  const [drugs, setDrugs] = useState<Drug[]>([]);
  const [drugQuery, setDrugQuery] = useState("");
  const [vitals, setVitals] = useState(EMPTY_VITALS);
  const [dx, setDx] = useState({ code: "", label: "" });
  const [items, setItems] = useState<DraftItem[]>([]);
  const [rxDraft, setRxDraft] = useState({ diagnosis_text: "", advice: "", follow_up: "" });
  const [signedRx, setSignedRx] = useState<PrescriptionDetail | null>(null);
  const [warnings, setWarnings] = useState<RxWarnings | null>(null);
  const [ackAllergies, setAckAllergies] = useState(false);
  const [pickPatient, setPickPatient] = useState("");
  const [pickQueue, setPickQueue] = useState("");
  const [chiefComplaint, setChiefComplaint] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => setLang(getLang()), []);

  const load = useCallback(async () => {
    try {
      const [patientPage, branchList, queuePage, encounterPage, drugPage] =
        await Promise.all([
          api.get<PageOf<Patient>>("/patients?limit=100"),
          api.get<Branch[]>("/branches"),
          api.get<PageOf<QueueEntry>>("/opd/queue?limit=100"),
          api.get<PageOf<Encounter>>("/encounters?status=open&limit=10"),
          api.get<Drug[]>(`/prescriptions/drugs?q=${encodeURIComponent(drugQuery)}`),
        ]);
      setPatients(patientPage.items);
      setBranches(branchList.filter((b) => b.status === "active"));
      setQueue(
        queuePage.items.filter((e) =>
          ["waiting", "called", "in_consultation"].includes(e.status)
        )
      );
      setDrugs(drugPage);
      if (encounterPage.items.length > 0) {
        const detail = await api.get<EncounterDetail>(
          `/encounters/${encounterPage.items[0].id}`
        );
        setOpen(detail);
      } else {
        setOpen(null);
      }
    } catch {
      setError("Could not load the workspace.");
    }
  }, [drugQuery]);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drugQuery]);

  async function startEncounter() {
    if (!pickPatient) return setError("Pick a patient first.");
    setError(null);
    try {
      const branchId = branches[0]?.id;
      const queueId = pickQueue || undefined;
      const enc = await api.post<Encounter>("/encounters", {
        patient_id: pickPatient,
        branch_id: branchId,
        chief_complaint: chiefComplaint,
        queue_entry_id: queueId,
      });
      setNotice(t("startEncounter", lang));
      setChiefComplaint("");
      setPickQueue("");
      await load();
      const detail = await api.get<EncounterDetail>(`/encounters/${enc.id}`);
      setOpen(detail);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Start failed");
    }
  }

  async function saveVitals() {
    if (!open) return;
    setError(null);
    try {
      const body: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(vitals)) {
        if (v !== "") body[k] = Number(v);
      }
      await api.post(`/encounters/${open.id}/vitals`, body);
      setVitals(EMPTY_VITALS);
      setNotice(t("recordVitals", lang));
      setOpen(await api.get<EncounterDetail>(`/encounters/${open.id}`));
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Vitals failed");
    }
  }

  async function saveDiagnosis() {
    if (!open || !dx.code || !dx.label) return;
    setError(null);
    try {
      await api.post(`/encounters/${open.id}/diagnoses`, {
        code: dx.code,
        label: dx.label,
        kind: "primary",
      });
      setDx({ code: "", label: "" });
      setOpen(await api.get<EncounterDetail>(`/encounters/${open.id}`));
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Diagnosis failed");
    }
  }

  async function closeEncounter() {
    if (!open) return;
    setError(null);
    try {
      await api.post(`/encounters/${open.id}/complete`, {});
      setNotice(t("encounterClosed", lang));
      setOpen(await api.get<EncounterDetail>(`/encounters/${open.id}`));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Close failed");
    }
  }

  async function signPrescription() {
    if (!open) return;
    setError(null);
    try {
      const draft = await api.post<PrescriptionDetail>("/prescriptions", {
        patient_id: open.patient_id,
        encounter_id: open.id,
        ...rxDraft,
        items,
      });
      const w = await api.post<RxWarnings>(
        `/prescriptions/${draft.id}/warnings`,
        {}
      );
      setWarnings(w);
      if (w.blocking && !ackAllergies) {
        setSignedRx(draft);
        return;
      }
      const signed = await api.post<PrescriptionDetail>(
        `/prescriptions/${draft.id}/sign`,
        { acknowledge_allergies: ackAllergies }
      );
      finishSign(signed);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Sign failed");
    }
  }

  function finishSign(signed: PrescriptionDetail) {
    setSignedRx(signed);
    setWarnings(null);
    setAckAllergies(false);
    setItems([]);
    setRxDraft({ diagnosis_text: "", advice: "", follow_up: "" });
    setNotice(`${t("signed", lang)} v${signed.version}`);
    void load();
  }

  const patient = open ? patients.find((p) => p.id === open.patient_id) : null;

  return (
    <AppShell active="consultation">
      <main className="page">
        <h1 style={{ marginTop: 0 }}>{t("consultation", lang)}</h1>
        <p className="muted">{t("m3Notice", lang)}</p>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {notice && <p className="notice">{notice}</p>}

        {!open && (
          <section className="card">
            <h3 style={{ marginTop: 0 }}>{t("startEncounter", lang)}</h3>
            <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
              <div className="field">
                <label htmlFor="cp">{t("patients", lang)}</label>
                <select
                  id="cp"
                  value={pickPatient}
                  onChange={(e) => setPickPatient(e.target.value)}
                >
                  <option value="">—</option>
                  {patients.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.uhid} — {p.full_name}
                      {p.allergies ? ` ⚠ ${p.allergies}` : ""}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="cq">{t("token", lang)}</label>
                <select
                  id="cq"
                  value={pickQueue}
                  onChange={(e) => setPickQueue(e.target.value)}
                >
                  <option value="">—</option>
                  {queue.map((q) => (
                    <option key={q.id} value={q.id}>
                      #{q.token_number} ({q.status})
                    </option>
                  ))}
                </select>
              </div>
              <div className="field" style={{ flex: 1, minWidth: "220px" }}>
                <label htmlFor="cc">{t("chiefComplaint", lang)}</label>
                <input
                  id="cc"
                  value={chiefComplaint}
                  onChange={(e) => setChiefComplaint(e.target.value)}
                  placeholder={t("chiefComplaint", lang)}
                />
              </div>
              <button
                className="primary"
                type="button"
                style={{ alignSelf: "end" }}
                onClick={() => void startEncounter()}
              >
                {t("startEncounter", lang)}
              </button>
            </div>
          </section>
        )}

        {open && (
          <>
            <section className="card">
              <h3 style={{ marginTop: 0 }}>
                {patient ? `${patient.uhid} — ${patient.full_name}` : open.patient_id}
                {open.status === "completed" && (
                  <span className="tag" style={{ marginLeft: "0.5rem" }}>
                    {t("encounterClosed", lang)}
                  </span>
                )}
              </h3>
              {patient?.allergies && (
                <p className="error">
                  ⚠ {t("allergies", lang)}: {patient.allergies}
                </p>
              )}
              <p className="muted">
                {t("chiefComplaint", lang)}: {open.chief_complaint || "—"}
              </p>

              {open.status === "open" && (
                <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                  <button className="primary" type="button" onClick={() => void closeEncounter()}>
                    {t("closeEncounter", lang)}
                  </button>
                </div>
              )}
            </section>

            <div
              style={{
                display: "grid",
                gap: "1rem",
                gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
              }}
            >
              <section className="card">
                <h3 style={{ marginTop: 0 }}>{t("vitals", lang)}</h3>
                <div style={{ display: "grid", gap: "0.5rem" }}>
                  {(
                    [
                      ["bp_systolic", t("bp", lang)],
                      ["bp_diastolic", "DBP"],
                      ["pulse", t("pulse", lang)],
                      ["temperature_c", t("temp", lang)],
                      ["spo2", t("spo2", lang)],
                      ["weight_kg", t("weight", lang)],
                    ] as const
                  ).map(([key, label]) => (
                    <div className="field" key={key}>
                      <label htmlFor={`v-${key}`}>{label}</label>
                      <input
                        id={`v-${key}`}
                        inputMode="decimal"
                        value={vitals[key]}
                        disabled={open.status !== "open"}
                        onChange={(e) =>
                          setVitals((v) => ({ ...v, [key]: e.target.value }))
                        }
                      />
                    </div>
                  ))}
                  <button
                    type="button"
                    disabled={open.status !== "open"}
                    onClick={() => void saveVitals()}
                  >
                    {t("recordVitals", lang)}
                  </button>
                </div>
                {open.vitals.length > 0 && (
                  <table style={{ marginTop: "0.75rem" }}>
                    <thead>
                      <tr>
                        <th>BP</th>
                        <th>{t("pulse", lang)}</th>
                        <th>{t("spo2", lang)}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {open.vitals.slice(-3).map((v) => (
                        <tr key={v.id}>
                          <td>
                            {v.bp_systolic ?? "—"}/{v.bp_diastolic ?? "—"}
                          </td>
                          <td>{v.pulse ?? "—"}</td>
                          <td>{v.spo2 ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>

              <section className="card">
                <h3 style={{ marginTop: 0 }}>{t("diagnosis", lang)}</h3>
                <div className="field">
                  <label htmlFor="dx-code">{t("dxCode", lang)}</label>
                  <input
                    id="dx-code"
                    value={dx.code}
                    disabled={open.status !== "open"}
                    onChange={(e) => setDx((d) => ({ ...d, code: e.target.value }))}
                    placeholder="J06.9"
                  />
                </div>
                <div className="field">
                  <label htmlFor="dx-label">{t("dxLabel", lang)}</label>
                  <input
                    id="dx-label"
                    value={dx.label}
                    disabled={open.status !== "open"}
                    onChange={(e) => setDx((d) => ({ ...d, label: e.target.value }))}
                  />
                </div>
                <button
                  type="button"
                  disabled={open.status !== "open"}
                  onClick={() => void saveDiagnosis()}
                >
                  {t("addDiagnosis", lang)}
                </button>
                <ul>
                  {open.diagnoses.map((d) => (
                    <li key={d.id}>
                      <code>{d.code}</code> {d.label}{" "}
                      <span className="muted">({d.kind})</span>
                    </li>
                  ))}
                </ul>
              </section>
            </div>

            <section className="card" style={{ marginTop: "1.25rem" }}>
              <h3 style={{ marginTop: 0 }}>{t("prescribe", lang)}</h3>

              <div className="field">
                <label htmlFor="drug-q">{t("drug", lang)}</label>
                <input
                  id="drug-q"
                  value={drugQuery}
                  onChange={(e) => setDrugQuery(e.target.value)}
                  placeholder={t("searchPatient", lang)}
                />
              </div>
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                {drugs.slice(0, 8).map((d) => (
                  <button
                    key={d.id}
                    type="button"
                    className="ghost"
                    disabled={open.status !== "open"}
                    onClick={() =>
                      setItems((xs) => [
                        ...xs,
                        { drug_id: d.id, dose: "1 tablet", frequency: "TDS", duration_days: 5 },
                      ])
                    }
                  >
                    + {d.generic_name}
                  </button>
                ))}
              </div>

              {items.length > 0 && (
                <table style={{ marginTop: "0.75rem" }}>
                  <thead>
                    <tr>
                      <th>{t("drug", lang)}</th>
                      <th>{t("dose", lang)}</th>
                      <th>{t("frequency", lang)}</th>
                      <th>{t("duration", lang)}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((it, idx) => (
                      <tr key={`${it.drug_id}-${idx}`}>
                        <td>{drugs.find((d) => d.id === it.drug_id)?.generic_name}</td>
                        <td>{it.dose}</td>
                        <td>{it.frequency}</td>
                        <td>{it.duration_days}</td>
                        <td>
                          <button
                            type="button"
                            className="ghost"
                            onClick={() =>
                              setItems((xs) => xs.filter((_, i) => i !== idx))
                            }
                          >
                            ✕
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <div className="field" style={{ marginTop: "0.75rem" }}>
                <label htmlFor="rx-dx">{t("diagnosis", lang)}</label>
                <input
                  id="rx-dx"
                  value={rxDraft.diagnosis_text}
                  onChange={(e) =>
                    setRxDraft((r) => ({ ...r, diagnosis_text: e.target.value }))
                  }
                />
              </div>
              <div className="field">
                <label htmlFor="rx-advice">Advice</label>
                <input
                  id="rx-advice"
                  value={rxDraft.advice}
                  onChange={(e) => setRxDraft((r) => ({ ...r, advice: e.target.value }))}
                />
              </div>
              <div className="field">
                <label htmlFor="rx-follow">{t("followUps", lang)}</label>
                <input
                  id="rx-follow"
                  value={rxDraft.follow_up}
                  onChange={(e) =>
                    setRxDraft((r) => ({ ...r, follow_up: e.target.value }))
                  }
                />
              </div>

              <button
                className="primary"
                type="button"
                disabled={items.length === 0 || open.status !== "open"}
                onClick={() => void signPrescription()}
              >
                {t("signRx", lang)}
              </button>
            </section>

            {signedRx && warnings && warnings.warnings.length > 0 && (
              <section className="card error" role="alert">
                <h3 style={{ marginTop: 0 }}>{t("signWarningTitle", lang)}</h3>
                <ul>
                  {warnings.warnings.map((w, i) => (
                    <li key={i}>{w.message}</li>
                  ))}
                </ul>
                <label style={{ display: "block", marginBottom: "0.5rem" }}>
                  <input
                    type="checkbox"
                    checked={ackAllergies}
                    onChange={(e) => setAckAllergies(e.target.checked)}
                  />{" "}
                  {t("acknowledgeAllergies", lang)}
                </label>
                <button
                  className="primary"
                  type="button"
                  disabled={!ackAllergies}
                  onClick={() => {
                    if (!signedRx) return;
                    void api
                      .post<PrescriptionDetail>(
                        `/prescriptions/${signedRx.id}/sign`,
                        { acknowledge_allergies: true }
                      )
                      .then(finishSign)
                      .catch((err) =>
                        setError(
                          err instanceof ApiError ? String(err.message) : "Sign failed"
                        )
                      );
                  }}
                >
                  {t("signRx", lang)}
                </button>
              </section>
            )}

            {signedRx && !warnings && (
              <p className="notice">
                {t("signed", lang)} v{signedRx.version} —{" "}
                <a href={`/print/rx/${signedRx.id}`} target="_blank">
                  {t("printRx", lang)}
                </a>
              </p>
            )}

            {open.prescription_ids.length > 0 && (
              <section className="card" style={{ marginTop: "1.25rem" }}>
                <h3 style={{ marginTop: 0 }}>{t("prescriptions", lang)}</h3>
                <ul>
                  {open.prescription_ids.map((id) => (
                    <li key={id}>
                      <a href={`/print/rx/${id}`} target="_blank">
                        {t("printRx", lang)}
                      </a>{" "}
                      <code className="muted">{id.slice(0, 8)}</code>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </main>
    </AppShell>
  );
}
