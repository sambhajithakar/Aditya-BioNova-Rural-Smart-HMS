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
