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
  return signInWithPhoneNumber(auth, phone, verifier);
}

export async function confirmCode(confirmation: ConfirmationResult, code: string): Promise<string> {
  const credential = await confirmation.confirm(code);
  return credential.user.getIdToken();
}
