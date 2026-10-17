"use client";

import { clearTokens, getAccessToken, saveTokens } from "./auth";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  const token = getAccessToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`/api/v1${path}`, { ...options, headers });
  if (res.status === 401 && token) {
    clearTokens();
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown, idempotencyKey?: string) =>
    request<T>(path, {
      method: "POST",
      body: JSON.stringify(body),
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
    }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
};

export type Me = {
  user: { id: string; email: string; full_name: string; status: string };
  organization: { id: string; name: string; slug: string; status: string };
  membership_id: string;
  branch_id: string | null;
  role: string;
  permissions: string[];
};

export type LoginResponse = {
  user: Me["user"];
  organization: Me["organization"];
  role: string;
  permissions: string[];
  tokens: { access_token: string; refresh_token: string; expires_in: number };
};

export async function login(input: {
  email: string;
  password: string;
  organization_slug?: string;
}): Promise<LoginResponse> {
  const data = await api.post<LoginResponse>("/auth/login", input);
  saveTokens(data.tokens);
  return data;
}

// --- M2 clinical types -------------------------------------------------------
export type Patient = {
  id: string;
  organization_id: string;
  uhid: string;
  full_name: string;
  gender: string;
  date_of_birth: string | null;
  dob_estimated: boolean;
  phone: string | null;
  address: string;
  blood_group: string | null;
  allergies: string;
  status: string;
  merged_into_id: string | null;
  created_at: string;
};

export type DuplicateCandidate = {
  id: string;
  uhid: string;
  full_name: string;
  gender: string;
  date_of_birth: string | null;
  phone: string | null;
  matched_on: string[];
};

export type Appointment = {
  id: string;
  patient_id: string;
  branch_id: string;
  provider_user_id: string | null;
  starts_at: string;
  ends_at: string;
  reason: string;
  status: string;
  notes: string;
};

export type QueueEntry = {
  id: string;
  patient_id: string;
  branch_id: string;
  appointment_id: string | null;
  service_date: string;
  token_number: number;
  status: string;
  priority: string;
  doctor_user_id: string | null;
  called_at: string | null;
  started_at: string | null;
  completed_at: string | null;
};

export type PageOf<T> = { items: T[]; total: number; limit: number; offset: number };

export type Branch = { id: string; name: string; code: string; status: string };

// --- M3 clinical + billing types --------------------------------------------
export type Encounter = {
  id: string;
  organization_id: string;
  branch_id: string;
  patient_id: string;
  appointment_id: string | null;
  queue_entry_id: string | null;
  provider_user_id: string | null;
  chief_complaint: string;
  history: string;
  examination: string;
  assessment: string;
  plan: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  created_at: string;
};

export type Vitals = {
  id: string;
  encounter_id: string;
  bp_systolic: number | null;
  bp_diastolic: number | null;
  pulse: number | null;
  temperature_c: number | null;
  spo2: number | null;
  respiratory_rate: number | null;
  weight_kg: number | null;
  height_cm: number | null;
  notes: string;
  recorded_at: string;
};

export type Diagnosis = {
  id: string;
  encounter_id: string;
  code: string;
  label: string;
  kind: string;
  status: string;
  created_at: string;
};

export type EncounterDetail = Encounter & {
  vitals: Vitals[];
  diagnoses: Diagnosis[];
  prescription_ids: string[];
};

export type Drug = {
  id: string;
  generic_name: string;
  brand_hint: string;
  drug_class: string;
};

export type RxItem = {
  id: string;
  prescription_id: string;
  drug_id: string;
  dose: string;
  route: string;
  frequency: string;
  duration_days: number;
  quantity: string;
  instructions: string;
  seq: number;
};

export type Prescription = {
  id: string;
  organization_id: string;
  encounter_id: string | null;
  patient_id: string;
  prescriber_user_id: string | null;
  status: string;
  version: number;
  supersedes_id: string | null;
  diagnosis_text: string;
  advice: string;
  follow_up: string;
  allergy_acknowledged: boolean;
  signed_at: string | null;
  amendment_reason: string;
  created_at: string;
};

export type PrescriptionDetail = Prescription & { items: RxItem[] };

export type RxWarning = { kind: string; message: string; drug_ids: string[] };
export type RxWarnings = { warnings: RxWarning[]; blocking: boolean };

export type InvoiceItem = {
  id: string;
  invoice_id: string;
  item_type: string;
  description: string;
  quantity: number;
  unit_price: number;
  tax_rate: number;
  line_total: number;
  seq: number;
};

export type Invoice = {
  id: string;
  organization_id: string;
  branch_id: string;
  patient_id: string;
  encounter_id: string | null;
  invoice_number: string;
  status: string;
  subtotal: number;
  discount: number;
  tax_total: number;
  total: number;
  amount_paid: number;
  notes: string;
  issued_at: string | null;
  created_at: string;
};

export type Payment = {
  id: string;
  invoice_id: string;
  amount: number;
  method: string;
  gateway: string;
  status: string;
  gateway_ref: string;
  failure_reason: string;
  created_at: string;
};

export type InvoiceDetail = Invoice & { items: InvoiceItem[]; payments: Payment[] };

export type Refund = {
  id: string;
  payment_id: string;
  amount: number;
  reason: string;
  status: string;
  created_at: string;
};

export type LedgerEntry = {
  id: number;
  invoice_id: string;
  payment_id: string | null;
  refund_id: string | null;
  entry_type: string;
  direction: string;
  amount: number;
  description: string;
  created_at: string;
};

export type Receipt = {
  invoice: Invoice;
  items: InvoiceItem[];
  payments: Payment[];
  refunds: Refund[];
  ledger: LedgerEntry[];
  balance_due: number;
  org_name: string;
  branch_name: string;
  patient_name: string;
  patient_uhid: string;
};

// --- M5 pharmacy types -------------------------------------------------------
export type StockBatch = {
  id: string;
  branch_id: string;
  drug_id: string;
  generic_name: string;
  batch_number: string;
  expiry_date: string;
  quantity_received: number;
  quantity_on_hand: number;
  unit_cost: number;
  supplier: string;
  status: string;
  received_at: string;
};

export type StockMovement = {
  id: number;
  batch_id: string;
  movement_type: string;
  direction: string;
  quantity: number;
  dispense_id: string | null;
  reason: string;
  created_at: string;
};

export type DispenseItem = {
  id: string;
  batch_id: string;
  drug_id: string;
  quantity: number;
  unit_price: number;
};

export type Dispense = {
  id: string;
  branch_id: string;
  prescription_id: string | null;
  patient_id: string | null;
  notes: string;
  created_at: string;
  items?: DispenseItem[];
};

export type LowStockEntry = {
  drug_id: string;
  generic_name: string;
  on_hand: number;
};

export type StockAlerts = {
  expiring_soon: StockBatch[];
  expired: StockBatch[];
  low_stock: LowStockEntry[];
};

// --- M5 laboratory types -----------------------------------------------------
export type LabTest = {
  id: string;
  code: string;
  name: string;
  specimen_type: string;
  unit: string;
  reference_range: string;
};

export type LabOrder = {
  id: string;
  patient_id: string;
  encounter_id: string | null;
  branch_id: string;
  status: string;
  priority: string;
  clinical_notes: string;
  cancel_reason: string;
  created_at: string;
};

export type LabSpecimen = {
  id: string;
  order_id: string;
  barcode: string;
  specimen_type: string;
  status: string;
  rejection_reason: string;
  collected_at: string;
};

export type LabResult = {
  id: string;
  order_id: string;
  test_id: string;
  specimen_id: string | null;
  value_text: string;
  value_numeric: number | null;
  unit: string;
  reference_range: string;
  flag: string;
  status: string;
  superseded_by_id: string | null;
  amendment_reason: string;
  verified_at: string | null;
  released_at: string | null;
  created_at: string;
};

export type LabOrderDetail = LabOrder & {
  specimens: LabSpecimen[];
  results: LabResult[];
};

// --- M6 IPD + operations types -----------------------------------------------
export type Ward = {
  id: string;
  branch_id: string;
  name: string;
  code: string;
  ward_type: string;
  status: string;
  created_at: string;
  bed_count: number;
  occupied_count: number;
  available_count: number;
};

export type Bed = {
  id: string;
  ward_id: string;
  label: string;
  status: string;
  admission_id: string | null;
  patient_id: string | null;
  admitted_at: string | null;
};

export type Admission = {
  id: string;
  patient_id: string;
  branch_id: string;
  ward_id: string;
  bed_id: string;
  reason: string;
  attending_user_id: string | null;
  status: string;
  admitted_at: string;
  discharge_outcome: string | null;
  discharge_summary: string;
  discharged_at: string | null;
  patient_uhid: string;
  patient_full_name: string;
};

export type NursingRecord = {
  id: string;
  admission_id: string;
  note_type: string;
  note: string;
  bp_systolic: number | null;
  bp_diastolic: number | null;
  pulse: number | null;
  respiratory_rate: number | null;
  temperature_c: number | null;
  spo2: number | null;
  recorded_at: string;
  recorded_by: string | null;
};

export type InventoryItem = {
  id: string;
  branch_id: string;
  name: string;
  sku: string;
  unit: string;
  quantity_on_hand: number;
  reorder_level: number;
  unit_cost: number;
  status: string;
  created_at: string;
  low_stock: boolean;
};

export type InventoryMovement = {
  id: number;
  item_id: string;
  movement_type: string;
  direction: string;
  quantity: number;
  reason: string;
  created_at: string;
};

export type Expense = {
  id: string;
  branch_id: string | null;
  category: string;
  amount: number;
  expense_date: string;
  description: string;
  reference: string;
  status: string;
  void_reason: string;
  created_at: string;
};

export type ReportSummary = {
  from_date: string;
  to_date: string;
  ipd: {
    currently_admitted: number;
    admissions_in_period: number;
    discharges_in_period: number;
    deaths_in_period: number;
    beds_total: number;
    beds_occupied: number;
    beds_maintenance: number;
  };
  finance: {
    revenue: number;
    refunds: number;
    expenses: number;
    net: number;
    by_category: { category: string; total: number }[];
  };
  inventory: {
    items_active: number;
    low_stock: {
      id: string;
      name: string;
      unit: string;
      quantity_on_hand: number;
      reorder_level: number;
    }[];
  };
};
