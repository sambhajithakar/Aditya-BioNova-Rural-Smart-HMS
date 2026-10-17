"use client";

/**
 * M6 inpatient page: the live bed board (wards + beds with derived
 * occupancy), patient admission, bed transfers, discharge with outcome and
 * summary, and append-only nursing records per admission.
 * The backend refuses illegal states (one inpatient per bed/patient,
 * discharge-once); the UI surfaces those errors verbatim.
 */
import { CSSProperties, Fragment, useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  Admission,
  ApiError,
  Bed,
  Branch,
  NursingRecord,
  PageOf,
  Patient,
  Ward,
  api,
} from "@/lib/api";
import { getLang, t } from "@/lib/i18n";

type Lang = "en" | "mr" | "hi";

const OUTCOMES: { value: string; key: string }[] = [
  { value: "recovered", key: "recovered" },
  { value: "improved", key: "improved" },
  { value: "referred", key: "referred" },
  { value: "left_against_advice", key: "leftAdvice" },
  { value: "death", key: "death" },
  { value: "transfer_out", key: "transferOut" },
];

const rowBtn: CSSProperties = {
  border: "1px solid var(--border)",
  background: "var(--white)",
  color: "var(--navy-800)",
  borderRadius: "var(--radius)",
  padding: "0.35rem 0.7rem",
  minHeight: "40px",
  fontWeight: 600,
  cursor: "pointer",
};

const chip = (bg: string): CSSProperties => ({
  display: "inline-block",
  border: "1px solid var(--border)",
  borderRadius: "999px",
  padding: "0.25rem 0.7rem",
  margin: "0.15rem 0.3rem 0.15rem 0",
  fontSize: "0.9rem",
  background: bg,
});

export default function IpdPage() {
  const [lang, setLang] = useState<Lang>("en");
  const [wards, setWards] = useState<Ward[]>([]);
  const [beds, setBeds] = useState<Bed[]>([]);
  const [admissions, setAdmissions] = useState<Admission[]>([]);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [records, setRecords] = useState<Record<string, NursingRecord[]>>({});

  const [branchId, setBranchId] = useState("");
  const [wardName, setWardName] = useState("");
  const [wardCode, setWardCode] = useState("");
  const [wardType, setWardType] = useState("general");
  const [newBed, setNewBed] = useState<Record<string, string>>({});

  const [patientId, setPatientId] = useState("");
  const [wardId, setWardId] = useState("");
  const [bedId, setBedId] = useState("");
  const [reason, setReason] = useState("");

  const [open, setOpen] = useState<string | null>(null);
  const [outcome, setOutcome] = useState("recovered");
  const [summary, setSummary] = useState("");
  const [transferBed, setTransferBed] = useState("");
  const [transferReason, setTransferReason] = useState("");
  const [noteType, setNoteType] = useState("note");
  const [note, setNote] = useState("");
  const [bpSys, setBpSys] = useState("");
  const [bpDia, setBpDia] = useState("");
  const [pulse, setPulse] = useState("");
  const [temp, setTemp] = useState("");
  const [spo2, setSpo2] = useState("");
  const [nurseId, setNurseId] = useState<string | null>(null);
  void nurseId;

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => setLang(getLang()), []);

  const load = useCallback(async () => {
    try {
      const [w, b, adm, patientPage, branchList] = await Promise.all([
        api.get<Ward[]>("/ipd/wards"),
        api.get<Bed[]>("/ipd/beds"),
        api.get<PageOf<Admission>>("/ipd/admissions?status=admitted&limit=100"),
        api.get<PageOf<Patient>>("/patients?limit=200"),
        api.get<Branch[]>("/branches"),
      ]);
      setWards(w);
      setBeds(b);
      setAdmissions(adm.items);
      setPatients(patientPage.items);
      setBranches(branchList.filter((x) => x.status === "active"));
      setBranchId((prev) => prev || branchList[0]?.id || "");
      setWardId((prev) => prev || w[0]?.id || "");
    } catch {
      setError("Could not load the IPD board.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function fail(err: unknown) {
    setError(err instanceof ApiError ? String(err.message) : "Action failed");
  }

  async function addWard() {
    if (!wardName || !wardCode || !branchId) {
      setError("Ward name, code and branch are required.");
      return;
    }
    setError(null);
    try {
      await api.post("/ipd/wards", {
        branch_id: branchId,
        name: wardName,
        code: wardCode,
        ward_type: wardType,
      });
      setWardName("");
      setWardCode("");
      setNotice("Ward created.");
      await load();
    } catch (err) {
      fail(err);
    }
  }

  async function addBed(ward: string) {
    const label = (newBed[ward] || "").trim();
    if (!label) {
      setError("Bed label is required.");
      return;
    }
    setError(null);
    try {
      await api.post("/ipd/beds", { ward_id: ward, label });
      setNewBed((prev) => ({ ...prev, [ward]: "" }));
      await load();
    } catch (err) {
      fail(err);
    }
  }

  async function admit() {
    if (!patientId || !wardId || !bedId || reason.trim().length < 3) {
      setError("Pick a patient, ward and bed, and give a reason (3+ chars).");
      return;
    }
    setError(null);
    try {
      await api.post(
        "/ipd/admissions",
        { patient_id: patientId, ward_id: wardId, bed_id: bedId, reason },
        `web-admit-${patientId}-${bedId}`
      );
      setReason("");
      setBedId("");
      setNotice("Patient admitted to the bed.");
      await load();
    } catch (err) {
      fail(err);
    }
  }

  async function openDetail(admissionId: string) {
    setOpen((prev) => (prev === admissionId ? null : admissionId));
    if (records[admissionId]) return;
    try {
      const page = await api.get<PageOf<NursingRecord>>(
        `/ipd/admissions/${admissionId}/nursing`
      );
      setRecords((prev) => ({ ...prev, [admissionId]: page.items }));
    } catch (err) {
      fail(err);
    }
  }

  async function transfer(admissionId: string) {
    if (!transferBed || transferReason.trim().length < 3) {
      setError("Choose the target bed and give a reason (3+ chars).");
      return;
    }
    setError(null);
    try {
      await api.post(`/ipd/admissions/${admissionId}/transfer`, {
        to_bed_id: transferBed,
        reason: transferReason,
      });
      setTransferBed("");
      setTransferReason("");
      setNotice("Patient transferred.");
      await load();
    } catch (err) {
      fail(err);
    }
  }

  async function dischargePatient(admissionId: string) {
    if (summary.trim().length < 3) {
      setError("A discharge summary is mandatory.");
      return;
    }
    setError(null);
    try {
      await api.post(`/ipd/admissions/${admissionId}/discharge`, {
        outcome,
        summary,
      });
      setSummary("");
      setOpen(null);
      setNotice("Patient discharged — the bed is free again.");
      await load();
    } catch (err) {
      fail(err);
    }
  }

  async function addRecord(admissionId: string) {
    setError(null);
    const body: Record<string, unknown> = { note_type: noteType, note };
    if (bpSys) body.bp_systolic = Number(bpSys);
    if (bpDia) body.bp_diastolic = Number(bpDia);
    if (pulse) body.pulse = Number(pulse);
    if (temp) body.temperature_c = Number(temp);
    if (spo2) body.spo2 = Number(spo2);
    try {
      await api.post(`/ipd/admissions/${admissionId}/nursing`, body, undefined);
      setNote("");
      setBpSys("");
      setBpDia("");
      setPulse("");
      setTemp("");
      setSpo2("");
      const page = await api.get<PageOf<NursingRecord>>(
        `/ipd/admissions/${admissionId}/nursing`
      );
      setRecords((prev) => ({ ...prev, [admissionId]: page.items }));
      setNotice("Nursing record saved (immutable).");
    } catch (err) {
      fail(err);
    }
  }

  const wardBeds = (id: string) => beds.filter((b) => b.ward_id === id);
  const freeBeds = (id: string) =>
    wardBeds(id).filter((b) => b.status === "available" && !b.admission_id);
  const bedLabel = (id: string) => beds.find((b) => b.id === id)?.label ?? "—";
  const wardNameOf = (id: string) =>
    wards.find((w) => w.id === id)?.name ?? "—";
  const patient = (id: string) => patients.find((p) => p.id === id);

  const transferOptions = beds.filter(
    (b) => b.status === "available" && !b.admission_id
  );

  return (
    <AppShell active="ipd">
      <main className="page">
        <h1 style={{ marginTop: 0 }}>{t("ipd", lang)}</h1>
        <p className="notice">{t("m6Notice", lang)}</p>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>{t("addWard", lang)}</h3>
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <div className="field">
              <label htmlFor="wbr">{t("branch", lang)}</label>
              <select
                id="wbr"
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
              <label htmlFor="wn">{t("wardName", lang)}</label>
              <input
                id="wn"
                value={wardName}
                onChange={(e) => setWardName(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="wc">Code</label>
              <input
                id="wc"
                value={wardCode}
                onChange={(e) => setWardCode(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="wt">{t("wardType", lang)}</label>
              <select
                id="wt"
                value={wardType}
                onChange={(e) => setWardType(e.target.value)}
              >
                {["general", "icu", "maternity", "pediatric", "isolation",
                  "day_care", "private", "other"].map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <button
              className="primary"
              type="button"
              style={{ alignSelf: "end" }}
              onClick={() => void addWard()}
            >
              {t("addWard", lang)}
            </button>
          </div>
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>
            {t("beds", lang)}
            <span className="muted" style={{ fontWeight: 400, marginLeft: "0.75rem" }}>
              {t("occupied", lang)} {beds.filter((b) => b.admission_id).length} /{" "}
              {beds.length}
            </span>
          </h3>
          {wards.length === 0 && (
            <p className="muted">No wards yet — create one above.</p>
          )}
          {wards.map((w) => (
            <div
              key={w.id}
              style={{
                borderTop: "1px solid var(--border)",
                paddingTop: "0.75rem",
                marginTop: "0.5rem",
              }}
            >
              <strong>{w.name}</strong>{" "}
              <span className="tag">{w.code}</span>
              <span className="tag">{w.ward_type}</span>
              <span className="tag">
                {w.occupied_count}/{w.bed_count} {t("occupied", lang)}
              </span>
              <div style={{ marginTop: "0.4rem" }}>
                {wardBeds(w.id).map((b) => (
                  <span
                    key={b.id}
                    style={chip(
                      b.admission_id
                        ? "#fef3f2"
                        : b.status === "available"
                          ? "var(--teal-100)"
                          : "#f6f9fc"
                    )}
                    title={b.admission_id ? t("occupied", lang) : b.status}
                  >
                    {b.label}
                    {b.admission_id
                      ? ` · ${t("occupied", lang)}`
                      : b.status !== "available"
                        ? ` · ${b.status}`
                        : ""}
                  </span>
                ))}
              </div>
              <div
                style={{
                  display: "flex",
                  gap: "0.5rem",
                  alignItems: "end",
                  marginTop: "0.5rem",
                }}
              >
                <div className="field">
                  <label htmlFor={`bed-${w.id}`}>{t("addBed", lang)}</label>
                  <input
                    id={`bed-${w.id}`}
                    value={newBed[w.id] ?? ""}
                    placeholder={t("bedLabel", lang)}
                    onChange={(e) =>
                      setNewBed((prev) => ({ ...prev, [w.id]: e.target.value }))
                    }
                  />
                </div>
                <button
                  type="button"
                  style={rowBtn}
                  onClick={() => void addBed(w.id)}
                >
                  {t("addBed", lang)}
                </button>
              </div>
            </div>
          ))}
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>{t("admitPatient", lang)}</h3>
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <div className="field">
              <label htmlFor="ap">{t("patients", lang)}</label>
              <select
                id="ap"
                value={patientId}
                onChange={(e) => {
                  setPatientId(e.target.value);
                  setWardId("");
                  setBedId("");
                }}
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
              <label htmlFor="aw">{t("wards", lang)}</label>
              <select
                id="aw"
                value={wardId}
                onChange={(e) => {
                  setWardId(e.target.value);
                  setBedId("");
                }}
              >
                <option value="">—</option>
                {wards.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="ab">{t("beds", lang)}</label>
              <select
                id="ab"
                value={bedId}
                onChange={(e) => setBedId(e.target.value)}
              >
                <option value="">—</option>
                {(wardId
                  ? freeBeds(wardId)
                  : []
                ).map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field" style={{ minWidth: "220px" }}>
              <label htmlFor="ar">{t("admissionReason", lang)}</label>
              <input
                id="ar"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </div>
            <button
              className="primary"
              type="button"
              style={{ alignSelf: "end" }}
              onClick={() => void admit()}
            >
              {t("admit", lang)}
            </button>
          </div>
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>
            {t("inpatients", lang)} ({admissions.length})
          </h3>
          <table>
            <thead>
              <tr>
                <th>{t("uhid", lang)}</th>
                <th>{t("patients", lang)}</th>
                <th>
                  {t("wards", lang)} / {t("beds", lang)}
                </th>
                <th>{t("startsAt", lang)}</th>
                <th>{t("actions", lang)}</th>
              </tr>
            </thead>
            <tbody>
              {admissions.map((a) => {
                const p = patient(a.patient_id);
                return (
                  <Fragment key={a.id}>
                  <tr>
                    <td>{a.patient_uhid || p?.uhid || "—"}</td>
                    <td>{a.patient_full_name || p?.full_name || "—"}</td>
                    <td>
                      {wardNameOf(a.ward_id)} · {bedLabel(a.bed_id)}
                    </td>
                    <td>{new Date(a.admitted_at).toLocaleString()}</td>
                    <td>
                      <button
                        type="button"
                        style={rowBtn}
                        onClick={() => void openDetail(a.id)}
                      >
                        {t("nursing", lang)}
                      </button>{" "}
                      <button
                        type="button"
                        style={rowBtn}
                        onClick={() => setOpen(open === a.id ? null : a.id)}
                      >
                        {t("discharge", lang)}
                      </button>
                    </td>
                  </tr>
                    {open === a.id && (
                      <tr>
                      <td colSpan={5}>
                        <div
                          style={{
                            display: "flex",
                            gap: "1rem",
                            flexWrap: "wrap",
                            borderTop: "1px dashed var(--border)",
                            paddingTop: "0.75rem",
                          }}
                        >
                          <div className="field">
                            <label htmlFor={`tr-${a.id}`}>{t("transferTo", lang)}</label>
                            <select
                              id={`tr-${a.id}`}
                              value={transferBed}
                              onChange={(e) => setTransferBed(e.target.value)}
                            >
                              <option value="">—</option>
                              {transferOptions.map((b) => (
                                <option key={b.id} value={b.id}>
                                  {wardNameOf(b.ward_id)} · {b.label}
                                </option>
                              ))}
                            </select>
                          </div>
                          <div className="field">
                            <label htmlFor={`trr-${a.id}`}>{t("reason", lang)}</label>
                            <input
                              id={`trr-${a.id}`}
                              value={transferReason}
                              onChange={(e) => setTransferReason(e.target.value)}
                            />
                          </div>
                          <button
                            type="button"
                            style={rowBtn}
                            onClick={() => void transfer(a.id)}
                          >
                            {t("transfer", lang)}
                          </button>

                          <div className="field">
                            <label htmlFor={`oc-${a.id}`}>{t("outcome", lang)}</label>
                            <select
                              id={`oc-${a.id}`}
                              value={outcome}
                              onChange={(e) => setOutcome(e.target.value)}
                            >
                              {OUTCOMES.map((o) => (
                                <option key={o.value} value={o.value}>
                                  {t(o.key, lang)}
                                </option>
                              ))}
                            </select>
                          </div>
                          <div className="field" style={{ minWidth: "240px" }}>
                            <label htmlFor={`ds-${a.id}`}>
                              {t("dischargeSummary", lang)}
                            </label>
                            <input
                              id={`ds-${a.id}`}
                              value={summary}
                              onChange={(e) => setSummary(e.target.value)}
                            />
                          </div>
                          <button
                            type="button"
                            style={{ ...rowBtn, color: "var(--danger)" }}
                            onClick={() => void dischargePatient(a.id)}
                          >
                            {t("discharge", lang)}
                          </button>
                        </div>

                        <div style={{ marginTop: "1rem" }}>
                          <strong>{t("nursing", lang)}</strong>
                          <div
                            style={{
                              display: "flex",
                              gap: "0.6rem",
                              flexWrap: "wrap",
                              alignItems: "end",
                              marginTop: "0.5rem",
                            }}
                          >
                            <div className="field">
                              <label htmlFor={`nt-${a.id}`}>{t("noteType", lang)}</label>
                              <select
                                id={`nt-${a.id}`}
                                value={noteType}
                                onChange={(e) => setNoteType(e.target.value)}
                              >
                                {["note", "vitals", "intake_output", "care"].map(
                                  (v) => (
                                    <option key={v} value={v}>
                                      {v}
                                    </option>
                                  )
                                )}
                              </select>
                            </div>
                            <div className="field" style={{ minWidth: "220px" }}>
                              <label htmlFor={`nn-${a.id}`}>{t("note", lang)}</label>
                              <input
                                id={`nn-${a.id}`}
                                value={note}
                                onChange={(e) => setNote(e.target.value)}
                              />
                            </div>
                            <div className="field">
                              <label htmlFor={`sys-${a.id}`}>BP sys</label>
                              <input
                                id={`sys-${a.id}`}
                                value={bpSys}
                                onChange={(e) => setBpSys(e.target.value)}
                              />
                            </div>
                            <div className="field">
                              <label htmlFor={`dia-${a.id}`}>BP dia</label>
                              <input
                                id={`dia-${a.id}`}
                                value={bpDia}
                                onChange={(e) => setBpDia(e.target.value)}
                              />
                            </div>
                            <div className="field">
                              <label htmlFor={`pl-${a.id}`}>{t("pulse", lang)}</label>
                              <input
                                id={`pl-${a.id}`}
                                value={pulse}
                                onChange={(e) => setPulse(e.target.value)}
                              />
                            </div>
                            <div className="field">
                              <label htmlFor={`tp-${a.id}`}>{t("temp", lang)}</label>
                              <input
                                id={`tp-${a.id}`}
                                value={temp}
                                onChange={(e) => setTemp(e.target.value)}
                              />
                            </div>
                            <div className="field">
                              <label htmlFor={`sp-${a.id}`}>{t("spo2", lang)}</label>
                              <input
                                id={`sp-${a.id}`}
                                value={spo2}
                                onChange={(e) => setSpo2(e.target.value)}
                              />
                            </div>
                            <button
                              className="primary"
                              type="button"
                              onClick={() => void addRecord(a.id)}
                            >
                              {t("addRecord", lang)}
                            </button>
                          </div>
                          <table style={{ marginTop: "0.75rem" }}>
                            <thead>
                              <tr>
                                <th>{t("time", lang)}</th>
                                <th>{t("noteType", lang)}</th>
                                <th>{t("note", lang)}</th>
                                <th>BP</th>
                                <th>{t("pulse", lang)}</th>
                                <th>{t("temp", lang)}</th>                              <th>SpO₂</th>
                            </tr>
                            </thead>
                            <tbody>
                              {(records[a.id] ?? []).map((r) => (
                                <tr key={r.id}>
                                  <td>
                                    {new Date(r.recorded_at).toLocaleString()}
                                  </td>
                                  <td>{r.note_type}</td>
                                  <td>{r.note || "—"}</td>
                                  <td>
                                    {r.bp_systolic && r.bp_diastolic
                                      ? `${r.bp_systolic}/${r.bp_diastolic}`
                                      : "—"}
                                  </td>
                                  <td>{r.pulse ?? "—"}</td>
                                  <td>{r.temperature_c ?? "—"}</td>
                                  <td>{r.spo2 ?? "—"}</td>
                                </tr>
                              ))}
                              {(records[a.id] ?? []).length === 0 && (
                                <tr>
                                  <td colSpan={7} className="muted">
                                    No records yet.
                                  </td>
                                </tr>
                              )}
                            </tbody>
                          </table>
                        </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
              {admissions.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    No one is admitted right now.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>

        {notice && <p className="notice">{notice}</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </main>
    </AppShell>
  );
}