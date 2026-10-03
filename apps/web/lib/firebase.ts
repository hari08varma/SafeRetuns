/** Firebase Phone Auth (client side). Firebase sends and checks the SMS code; the backend
 * verifies the resulting ID token and issues our own session. */
import { type FirebaseApp, getApps, initializeApp } from "firebase/app";
import {
  type ConfirmationResult,
  RecaptchaVerifier,
  getAuth,
  signInWithPhoneNumber,
} from "firebase/auth";

const config = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
};

export const firebaseEnabled = Boolean(config.apiKey && config.projectId);

function app(): FirebaseApp {
  return getApps()[0] ?? initializeApp(config);
}

let verifier: RecaptchaVerifier | null = null;

/** Sends the SMS. `buttonId` is the element the invisible reCAPTCHA attaches to. */
export async function sendCode(phone: string, buttonId: string): Promise<ConfirmationResult> {
  const auth = getAuth(app());
  verifier ??= new RecaptchaVerifier(auth, buttonId, { size: "invisible" });
  try {
    return await signInWithPhoneNumber(auth, phone, verifier);
  } catch (err) {
    // A used or failed reCAPTCHA cannot be reused: start fresh on the next attempt.
    verifier.clear();
    verifier = null;
    throw err;
  }
}

const SEND_ERRORS: Record<string, string> = {
  "auth/unauthorized-domain": "This website is not authorised for sign-in yet (Firebase authorised domains).",
  "auth/operation-not-allowed": "Phone sign-in is not enabled for this store yet.",
  "auth/billing-not-enabled": "SMS sign-in is not available yet: the Firebase project needs billing enabled.",
  "auth/invalid-phone-number": "That mobile number does not look right. Check it and try again.",
  "auth/too-many-requests": "Too many attempts. Please wait a few minutes and try again.",
  "auth/quota-exceeded": "SMS limit reached for today. Please try again later.",
  "auth/captcha-check-failed": "The security check failed. Reload the page and try again.",
  "auth/invalid-app-credential": "The security check failed. Reload the page and try again.",
  "auth/network-request-failed": "Network problem. Check your connection and try again.",
};

/** A customer-facing reason for a failed send, with the Firebase code for support. */
export function sendErrorMessage(err: unknown): string | null {
  const code = (err as { code?: string } | null)?.code;
  if (!code || !code.startsWith("auth/")) return null;
  return `${SEND_ERRORS[code] ?? "Could not send the code."} (${code})`;
}

export async function confirmCode(confirmation: ConfirmationResult, code: string): Promise<string> {
  const credential = await confirmation.confirm(code);
  return credential.user.getIdToken();
}
