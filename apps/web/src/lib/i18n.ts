"use client";

/**
 * Minimal M1 dictionary to prove Unicode/Devanagari rendering and the
 * language switcher. Full workflow translation is P0 for milestone M4.
 */
export type Lang = "en" | "mr" | "hi";

export const LANGS: { code: Lang; label: string; native: string }[] = [
  { code: "en", label: "English", native: "English" },
  { code: "mr", label: "Marathi", native: "मराठी" },
  { code: "hi", label: "Hindi", native: "हिंदी" },
];

type Dict = Record<string, string>;

const dictionaries: Record<Lang, Dict> = {
  en: {
    brand: "Aditya BioNova Rural Smart HMS",
    signIn: "Sign in",
    email: "Email",
    password: "Password",
    clinicSlug: "Clinic slug (optional)",
    dashboard: "Dashboard",
    patients: "Patients",
    queue: "Queue",
    logout: "Log out",
    role: "Role",
    permissions: "Permissions",
    org: "Organization",
    waiting: "Waiting",
    seen: "Seen",
    followUps: "Follow-ups",
    outstanding: "Outstanding",
    m1Notice:
      "M1 foundation build: authentication, tenancy and RBAC are live. Clinical modules arrive in M2+.",
    welcome: "Welcome",
    appointments: "Appointments",
    registerPatient: "Register patient",
    searchPatient: "Search name, phone or UHID",
    uhid: "UHID",
    fullName: "Full name",
    gender: "Gender",
    dob: "Date of birth",
    phone: "Phone",
    address: "Address",
    bloodGroup: "Blood group",
    allergies: "Allergies",
    save: "Save",
    cancel: "Cancel",
    duplicateWarn: "Possible duplicate found. Review, then confirm to register anyway.",
    confirmCreate: "Yes, register as new patient",
    candidates: "Existing matches",
    token: "Token",
    status: "Status",
    actions: "Actions",
    call: "Call",
    start: "Start",
    complete: "Complete",
    skip: "Skip",
    addQueue: "Add to queue",
    reason: "Reason",
    startsAt: "Starts",
    createAppointment: "New appointment",
    queueBoard: "OPD queue board",
    branch: "Branch",
    m2Notice:
      "M2 build: patient registration with supervised duplicate detection, appointments and the live OPD queue are active.",
  },
  mr: {
    brand: "आदित्य बायोनोवा ग्रामीण स्मार्ट एचएमएस",
    signIn: "साइन इन",
    email: "ईमेल",
    password: "पासवर्ड",
    clinicSlug: "क्लिनिक स्ल्ग (पर्यायी)",
    dashboard: "डॅशबोर्ड",
    patients: "रुग्ण",
    queue: "रुग्ण ओळी",
    logout: "लॉग आउट",
    role: "भूमिका",
    permissions: "परवानग्या",
    org: "संस्था",
    waiting: "प्रतीक्षेत",
    seen: "भेटलेले",
    followUps: "पुनरावृत्ती",
    outstanding: "थकबाकी",
    m1Notice:
      "एम1 मूल आवृत्ती: प्रमाणीकरण, स्वत्व आणि भूमिका-आधारित परवानग्या चालू आहेत. रुग्णवैद्यकीय मॉड्यूल एम२ पुढे येतील.",
    welcome: "स्वागत आहे",
    appointments: "अपॉइंटमेंट",
    registerPatient: "रुग्ण नोंदणी",
    searchPatient: "नाव, फोन किंवा यूएचआयडी शोधा",
    uhid: "यूएचआयडी",
    fullName: "पूर्ण नाव",
    gender: "लिंग",
    dob: "जन्मदिनांक",
    phone: "फोन",
    address: "पत्ता",
    bloodGroup: "रक्तगट",
    allergies: "ऍलर्जी",
    save: "जतन करा",
    cancel: "रद्द करा",
    duplicateWarn: "संभाव्य डुप्लिकेट आढळला. तपासा आणि नोंदणी पुढे सुरू ठेवा.",
    confirmCreate: "होय, नवीन रुग्ण म्हणून नोंदवा",
    candidates: "आधीच्या नोंदी",
    token: "टोकन",
    status: "स्थिती",
    actions: "कृत्ये",
    call: "बोलावा",
    start: "सुरू करा",
    complete: "पूर्ण करा",
    skip: "वगळा",
    addQueue: "ओळीत जोडा",
    reason: "कारण",
    startsAt: "सुरुवात",
    createAppointment: "नवीन अपॉइंटमेंट",
    queueBoard: "ओपीडी रुग्ण ओळी",
    branch: "शाखा",
    m2Notice:
      "एम2 आवृत्ती: सुपरवाइज्ड डुप्लिकेट तपासणीसह रुग्ण नोंदणी, अपॉइंटमेंट आणि थेट ओपीडी ओळी चालू आहे.",
  },
  hi: {
    brand: "आदित्य बायोनोवा ग्रामीण स्मार्ट एचएमएस",
    signIn: "साइन इन",
    email: "ईमेल",
    password: "पासवर्ड",
    clinicSlug: "क्लिनिक स्लग (वैकल्पिक)",
    dashboard: "डैशबोर्ड",
    patients: "रोगी",
    queue: "कतार",
    logout: "लॉग आउट",
    role: "भूमिका",
    permissions: "अनुमतियाँ",
    org: "संगठन",
    waiting: "प्रतीक्षारत",
    seen: "देखे गए",
    followUps: "फ़ॉलो-अप",
    outstanding: "बकाया",
    m1Notice:
      "एम1 फ़ाउंडेशन बिल्ड: प्रमाणीकरण, टेनेंसी और भूमिका-आधारित अनुमतियाँ चालू हैं। क्लिनिकल मॉड्यूल एम2 से आएंगे।",
    welcome: "स्वागत है",
    appointments: "अपॉइंटमेंट",
    registerPatient: "रोगी पंजीकरण",
    searchPatient: "नाम, फ़ोन या यूएचआईडी खोजें",
    uhid: "यूएचआईडी",
    fullName: "पूरा नाम",
    gender: "लिंग",
    dob: "जन्म तिथि",
    phone: "फ़ोन",
    address: "पता",
    bloodGroup: "रक्त समूह",
    allergies: "एलर्जी",
    save: "सहेजें",
    cancel: "रद्द करें",
    duplicateWarn: "संभावित डुप्लिकेट मिला। जाँच करें, फिर पंजीकरण जारी रखें।",
    confirmCreate: "हाँ, नए रोगी के रूप में पंजीकृत करें",
    candidates: "मौजूदा मिलान",
    token: "टोकन",
    status: "स्थिति",
    actions: "कार्रवाई",
    call: "बुलाएँ",
    start: "शुरू करें",
    complete: "पूर्ण करें",
    skip: "छोड़ें",
    addQueue: "कतार में जोड़ें",
    reason: "कारण",
    startsAt: "आरंभ",
    createAppointment: "नई अपॉइंटमेंट",
    queueBoard: "ओपीडी कतार बोर्ड",
    branch: "शाखा",
    m2Notice:
      "एम2 बिल्ड: सुपरवाइज़ डुप्लिकेट जाँच के साथ रोगी पंजीकरण, अपॉइंटमेंट और लाइव ओपीडी कतार सक्रिय हैं。",
  },
};

export function getLang(): Lang {
  if (typeof window === "undefined") return "en";
  const stored = localStorage.getItem("hms.lang");
  return stored === "mr" || stored === "hi" ? stored : "en";
}

export function setLang(lang: Lang): void {
  if (typeof window !== "undefined") localStorage.setItem("hms.lang", lang);
}

export function t(key: string, lang: Lang): string {
  return dictionaries[lang][key] ?? dictionaries.en[key] ?? key;
}
